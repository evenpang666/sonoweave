from __future__ import annotations

from dataclasses import replace
import logging
import re
from queue import Empty
import threading
import time
import numpy as np

from PySide6.QtCore import QObject, Signal

from .audio import capture_loopback
from .config import LANGUAGES
from .recognition import AdaptiveDecoding, LanguageTracker, load_recognizer
from .streaming import CaptionRun, CaptionUpdate, LatestQueue, SourceChunk, TextJob, TimedWord, TranscriptCache, TranslationCache, slice_runs
from .speakers import SpeakerRegistry, SpeakerTracker, SpeechSeparator, align_speakers
from .translation import TranslationCancelled, TranslationError, Translator, UntranslatedOutput

log = logging.getLogger(__name__)


class CaptionEngine(QObject):
    caption = Signal(object)
    status = Signal(str, bool)
    translation_status = Signal(str, bool, int)

    def __init__(self, target, model_path=None, *, speaker_colors=False):
        super().__init__()
        self.target = target
        self.model_path = model_path
        self.model_generation = 0
        self.revision = 0
        self.stop_event = threading.Event()
        self.audio_queue = LatestQueue(maxsize=3)
        self.speaker_queue = LatestQueue(maxsize=3)
        self.speaker_windows = {}
        self.speaker_colors = speaker_colors
        self.speaker_generation = 0
        self.speaker_registry = SpeakerRegistry()
        self.speaker_audio = {}
        self.overlap_queue = LatestQueue(maxsize=2)
        self.overlap_scheduled = {}
        self.overlap_applied = {}
        self.pending_repairs = set()
        self.text_queue = LatestQueue(maxsize=1)
        self.source_cache = TranscriptCache()
        self.translation_cache = TranslationCache()
        self.lock = threading.Lock()
        self.last_job = None
        self.last_published = None
        self.threads = []
        self.ready = set()
        self.errors = {}
        self.translation_active_since = None
        self.recognition_active_since = None
        self.recognition_stall_seconds = 8
        self.last_caption_at = 0.0
        self.translation_stall_seconds = 8
        self.stall_notified = False

    def start(self):
        if self.threads:
            return
        self.status.emit("正在加载离线识别与翻译模型…", False)
        for name, function in (("system-audio", self._capture), ("recognition", self._recognize),
                               ("speakers", self._diarize), ("overlap", self._separate), ("translation", self._translate)):
            thread = threading.Thread(name=name, target=function, daemon=True)
            self.threads.append(thread)
            thread.start()

    def stop(self):
        self.stop_event.set()

    def wait_stopped(self, timeout=8, cancelled=lambda: False):
        deadline = time.monotonic() + timeout
        for thread in self.threads:
            while thread.is_alive():
                remaining = deadline - time.monotonic()
                if cancelled() or remaining <= 0:
                    return False
                thread.join(min(0.2, remaining))
        return True

    def check_health(self, now=None):
        if self.stop_event.is_set():
            return
        now = time.monotonic() if now is None else now
        with self.lock:
            since = self.translation_active_since
            dead = next((thread.name for thread in self.threads
                         if thread.name in ("translation", "recognition", "system-audio") and
                         thread.ident is not None and not thread.is_alive()), None)
            recognition_stuck = (self.recognition_active_since is not None and
                                 now - self.recognition_active_since >= self.recognition_stall_seconds)
            translation_stuck = since is not None and now - max(since, self.last_caption_at) >= self.translation_stall_seconds
            if self.stall_notified or not (dead or recognition_stuck or translation_stuck):
                return
            self.stall_notified = True
            revision = self.revision
        log.warning("Caption worker stopped or made no progress: %s", dead or ("recognition" if recognition_stuck else "translation"))
        if dead:
            component = {"translation": "翻译", "recognition": "识别", "system-audio": "音频"}[dead]
            message = f"{component}线程已停止，请右键重新连接"
        elif recognition_stuck:
            message = "识别暂时无进展，请右键重新连接"
        else:
            message = "翻译暂时无进展，请右键重新连接或切换 2B 模型"
        self.translation_status.emit(message, True, revision)

    def set_target(self, target):
        if target not in LANGUAGES:
            raise ValueError("Unsupported target language")
        with self.lock:
            self.target = target
            self.revision += 1
            self.text_queue.clear()
            self.translation_cache.clear()
            if self.last_job:
                job = replace(self.last_job, target=target, revision=self.revision)
                self.last_job = job
                self.text_queue.put("recent-transcript", job)

    def set_speaker_colors(self, enabled):
        with self.lock:
            if enabled == self.speaker_colors:
                return
            self.speaker_colors = enabled
            self.speaker_generation += 1
            self.revision += 1
            self.speaker_queue.clear()
            self.speaker_windows.clear()
            self.speaker_audio.clear()
            self.overlap_queue.clear()
            self.overlap_scheduled.clear()
            self.overlap_applied.clear()
            self.pending_repairs.clear()
            self.speaker_registry = SpeakerRegistry()
            self.errors.pop("speakers", None)
            self.errors.pop("overlap", None)
            self.text_queue.clear()
            if not enabled:
                for key, chunk in self.source_cache.chunks.items():
                    self.source_cache.chunks[key] = replace(chunk, runs=(), words=(), separated=False)
                self.source_cache.version += 1
                self.translation_cache.runs = {key: (CaptionRun(text),)
                                               for key, text in self.translation_cache.chunks.items()}
            self.last_published = None
            self._queue_transcript()

    def _capture(self):
        capture_loopback(self.stop_event, self._enqueue_audio,
                         lambda text, error: self._component_status("audio", text, error))

    def set_model(self, model_path):
        with self.lock:
            if self.model_path == model_path:
                return
            self.model_path = model_path
            self.model_generation += 1
            self.revision += 1
            self.ready.discard("translation")
            self.errors.pop("translation", None)
            self.text_queue.clear()
            self.translation_cache.clear()
            if self.last_job:
                self.last_job = replace(self.last_job, target=self.target, revision=self.revision)
                self.text_queue.put("recent-transcript", self.last_job)
        self.status.emit("正在切换离线翻译模型…", False)

    def _component_status(self, name, text="", error=False, speaker_generation=None):
        with self.lock:
            if speaker_generation is not None and (not self.speaker_colors or speaker_generation != self.speaker_generation):
                return
            if error:
                self.errors[name] = text
            else:
                self.errors.pop(name, None)
                self.ready.add(name)
            if self.errors:
                message, failed = next(iter(self.errors.values())), True
            elif {"recognition", "translation", "audio"} <= self.ready:
                message, failed = "正在聆听系统声音 · 离线", False
            else:
                pending = "翻译" if "translation" not in self.ready else "识别"
                message, failed = f"正在加载离线{pending}模型…", False
        self.status.emit(message, failed)

    def _recognize(self):
        try:
            model = load_recognizer()
        except Exception as exc:
            log.exception("Offline recognition initialization failed")
            self._component_status("recognition", str(exc) if isinstance(exc, RuntimeError) else "离线识别模型加载失败，请右键重试", True)
            return
        if self.stop_event.is_set():
            return
        self._component_status("recognition")
        self.recognition_stall_seconds = 30 if getattr(getattr(model, "model", None), "device", None) == "cpu" else 8
        language_tracker = LanguageTracker()
        decoding = AdaptiveDecoding()
        while not self.stop_event.is_set():
            try:
                window = self.audio_queue.get()
            except Empty:
                continue
            if not window.final and time.monotonic() - window.ended > 4:
                continue
            language_tracker.begin(window)
            with self.lock:
                self.recognition_active_since = time.monotonic()
                speaker_colors, speaker_generation = self.speaker_colors, self.speaker_generation
            try:
                if language_tracker.should_probe(window):
                    detected, probability, _ = model.detect_language(audio=window.audio[-128000:])
                    language_tracker.observe(detected, probability, window)
                language = language_tracker.language
                segments, info = model.transcribe(
                    window.audio, beam_size=decoding.beam(window), language=language, vad_filter=True,
                    vad_parameters={"min_silence_duration_ms": 200, "speech_pad_ms": 100},
                    condition_on_previous_text=False, temperature=0,
                    max_new_tokens=160, word_timestamps=speaker_colors, without_timestamps=not speaker_colors,
                )
                # faster-whisper already combines no-speech probability with
                # decoder confidence. A second probability-only filter drops
                # valid speech it deliberately retained, leaving captions stale.
                segments = tuple(segments)
                parts = [segment.text.strip() for segment in segments]
                text = " ".join(part for part in parts if part).strip()
                words = ()
                if speaker_colors and segments and all(getattr(segment, "words", None) for segment in segments):
                    words = tuple(TimedWord(word.word, word.start, word.end)
                                  for segment in segments for word in segment.words)
                    words = (replace(words[0], text=words[0].text.lstrip()), *words[1:])
                    words = (*words[:-1], replace(words[-1], text=words[-1].text.rstrip()))
                    text = "".join(word.text for word in words)
                decoding.observe(text, window)
                if not text or self.stop_event.is_set():
                    continue
                if language is None:
                    language_tracker.observe(info.language, getattr(info, "language_probability", 0), window)
                source = info.language
                with self.lock:
                    if not self.speaker_colors or speaker_generation != self.speaker_generation:
                        words = ()
                        text = " ".join(part for part in parts if part).strip()
                    _, turns = self.speaker_windows.get(window.utterance_id, (0, ()))
                    current = self.source_cache.chunks.get(window.utterance_id)
                    if current and current.separated and self.speaker_colors:
                        # A later mixed ASR draft must not overwrite separated speech.
                        # New audio for this utterance is already queued for refinement.
                        continue
                    runs = align_speakers(words, turns) if words else ()
                    chunk = SourceChunk(window.utterance_id, text, source, window.sequence,
                                        window.final, window.started, window.ended, runs, words)
                    if not self.source_cache.update(chunk, window.overlap_previous):
                        continue
                    self._queue_transcript()
                self._component_status("recognition")
            except Exception:
                log.exception("Speech recognition failed")
                self._component_status("recognition", "语音识别暂时失败，正在继续聆听…", True)
            finally:
                with self.lock:
                    self.recognition_active_since = None

    def _enqueue_audio(self, window):
        self.audio_queue.put(window.utterance_id, window)
        with self.lock:
            if self.speaker_colors:
                self.speaker_audio[window.utterance_id] = window
                while len(self.speaker_audio) > 8:
                    key = next(iter(self.speaker_audio))
                    del self.speaker_audio[key]
                    self.overlap_scheduled.pop(key, None)
                    self.overlap_applied.pop(key, None)
                if window.utterance_id in self.overlap_scheduled:
                    self._schedule_overlap(window)
                self.speaker_queue.put(window.utterance_id, window)

    def _queue_transcript(self, repair_id=None):
        """Called under lock for either new text or a speaker-only revision."""
        recent = self.source_cache.recent()
        if not recent:
            return
        if repair_id is not None:
            self.pending_repairs.add(repair_id)
        retained = self.source_cache.chunks
        self.pending_repairs.intersection_update(retained)
        selected = {part.utterance_id: part for part in recent}
        for key in sorted(self.pending_repairs)[-8:]:
            if retained[key].source == recent[-1].source:
                selected[key] = retained[key]
        recent = tuple(selected[key] for key in sorted(selected))
        current = recent[-1]
        context = self.source_cache.context_before(recent[0].utterance_id)
        job = TextJob("\n".join(part.text for part in recent), current.source, self.target, self.revision,
                      current.utterance_id, current.sequence, current.final, context,
                      current.started, current.ended, recent, self.source_cache.version)
        self.last_job = job
        self.text_queue.put("recent-transcript", job)

    def _diarize(self):
        tracker, tracker_generation, failed_generation = None, None, None
        last_id, last_size = None, 0
        while not self.stop_event.is_set():
            with self.lock:
                enabled, generation, registry = self.speaker_colors, self.speaker_generation, self.speaker_registry
            if not enabled or generation == failed_generation:
                tracker = None
                tracker_generation = None
                self.stop_event.wait(0.2)
                continue
            if tracker_generation != generation:
                try:
                    tracker = SpeakerTracker(registry=registry)
                except Exception:
                    log.exception("Speaker model initialization failed; captions remain available")
                    failed_generation = generation
                    self._component_status("speakers", "人声配色不可用，请关闭后重新开启；字幕仍可使用", True,
                                           speaker_generation=generation)
                    continue
                tracker_generation = generation
                last_id, last_size = None, 0
            try:
                window = self.speaker_queue.get()
            except Empty:
                continue
            if not window.final and (time.monotonic() - window.ended > 4 or
                    (window.utterance_id == last_id and window.audio.size - last_size < 16000)):
                continue
            try:
                results = tracker.analyze_window(window)
                last_id, last_size = window.utterance_id, window.audio.size
                with self.lock:
                    if self.stop_event.is_set():
                        return
                    if not self.speaker_colors or generation != self.speaker_generation:
                        continue
                    self.speaker_windows.update(results)
                    while len(self.speaker_windows) > 96:
                        del self.speaker_windows[next(iter(self.speaker_windows))]
                    changed = False
                    for key, (_, turns) in results.items():
                        if sum(t.end - t.start for t in turns if t.overlap) >= 0.25:
                            audio_window = self.speaker_audio.get(key)
                            if audio_window is not None:
                                self._schedule_overlap(audio_window)
                        chunk = self.source_cache.chunks.get(key)
                        if not chunk or not chunk.words or chunk.separated:
                            continue
                        runs = align_speakers(chunk.words, turns)
                        full_text = "".join(run.text for run in runs)
                        # Forced-window overlap may already have trimmed the source prefix.
                        if full_text.endswith(chunk.text):
                            runs = slice_runs(runs, len(full_text) - len(chunk.text))
                            if self.source_cache.relabel(chunk.utterance_id, runs):
                                changed = True
                                self.pending_repairs.add(key)
                    if changed:
                        self._queue_transcript()
                self._component_status("speakers", speaker_generation=generation)
            except Exception:
                log.exception("Speaker analysis failed; continuing with default caption color")

    def _schedule_overlap(self, window):
        """Called under lock. Coalesce costly refinements without losing finals."""
        previous = self.overlap_scheduled.get(window.utterance_id)
        if previous and (window.sequence <= previous[0] or
                         (not window.final and window.audio.size - previous[1] < 24000)):
            return
        self.overlap_scheduled[window.utterance_id] = (window.sequence, window.audio.size)
        self.overlap_queue.put(window.utterance_id, (window, self.speaker_generation))

    def _separate(self):
        separator = model = tracker = None
        worker_generation = failed_generation = None
        while not self.stop_event.is_set():
            with self.lock:
                enabled, generation, registry = self.speaker_colors, self.speaker_generation, self.speaker_registry
            if not enabled or failed_generation == generation:
                separator = model = tracker = None
                self.stop_event.wait(0.2)
                continue
            try:
                window, scheduled_generation = self.overlap_queue.get()
            except Empty:
                continue
            if scheduled_generation != generation:
                continue
            try:
                if worker_generation != generation:
                    separator = SpeechSeparator()
                    # A separate CPU recognizer keeps overlap refinement from
                    # locking the main recognizer or allocating another GPU model.
                    model = load_recognizer(device="cpu", cpu_threads=2)
                    tracker = SpeakerTracker(registry=registry)
                    worker_generation = generation
                with self.lock:
                    current = self.source_cache.chunks.get(window.utterance_id)
                    language = current.source if current else None
                sources = separator.separate(window.audio)
                chunk = self._recognize_separated(window, sources, model, tracker, language)
                if chunk is not None:
                    self._apply_separation(chunk, window, generation)
                self._component_status("overlap", speaker_generation=generation)
            except Exception:
                log.exception("Overlapping speech refinement failed; main captions remain available")
                if worker_generation != generation:
                    failed_generation = generation
                self._component_status("overlap", "重叠人声分离暂时失败，普通字幕仍可使用", True,
                                       speaker_generation=generation)

    def _recognize_separated(self, window, sources, model, tracker, language):
        streams = []
        excluded = set()
        mixture_rms = float(np.sqrt(np.mean(window.audio ** 2)))
        for samples in sources:
            if self.stop_event.is_set():
                return None
            if float(np.sqrt(np.mean(samples ** 2))) < max(0.001, mixture_rms * 0.04):
                continue
            segments, info = model.transcribe(np.ascontiguousarray(samples), language=language,
                beam_size=3 if window.final else 1, vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 200, "speech_pad_ms": 100},
                condition_on_previous_text=False, temperature=(0.0, 0.2, 0.4),
                word_timestamps=False, max_new_tokens=160)
            segments = tuple(segments)
            text = " ".join(segment.text.strip() for segment in segments if segment.text.strip())
            if not text:
                continue
            words = tuple(TimedWord(word.word, float(word.start), float(word.end))
                          for segment in segments for word in (getattr(segment, "words", None) or ()))
            start = max(0, min((word.start for word in words), default=0))
            end = min(len(samples) / 16000, max((word.end for word in words), default=len(samples) / 16000))
            identity = tracker.identify_audio(samples[round(start * 16000):round(end * 16000)], excluded=excluded)
            if identity is None:
                continue
            excluded.add(identity)
            streams.append((start, identity, text, info.language))
        # Partial extraction must not discard the other person's words.
        if len(streams) != 2 or streams[0][1] == streams[1][1]:
            return None
        texts = [re.sub(r"\W+", "", item[2]).casefold() for item in streams]
        if len(texts[0]) >= 8 and texts[0] == texts[1]:
            return None  # residual leakage, not two independently recovered sentences
        streams.sort(key=lambda item: (item[0], item[1]))
        runs = tuple(CaptionRun((" " if i else "") + text, identity)
                     for i, (_, identity, text, _) in enumerate(streams))
        return SourceChunk(window.utterance_id, "".join(run.text for run in runs), language or streams[0][3],
                           window.sequence, window.final, window.started, window.ended, runs, (), True)

    def _apply_separation(self, chunk, window, generation):
        with self.lock:
            if self.stop_event.is_set() or not self.speaker_colors or generation != self.speaker_generation:
                return False
            if window.sequence <= self.overlap_applied.get(window.utterance_id, -1):
                return False
            current = self.source_cache.chunks.get(chunk.utterance_id)
            if current is not None:
                # Main ASR can already be ahead. Do not starve separated output
                # forever while a growing window emits new drafts every 550 ms.
                chunk = replace(chunk, sequence=max(chunk.sequence, current.sequence))
            if not self.source_cache.update(chunk, window.overlap_previous, allow_same_sequence=True):
                return False
            self.overlap_applied[window.utterance_id] = window.sequence
            self._queue_transcript(repair_id=chunk.utterance_id)
            return True

    def _translate(self):
        while not self.stop_event.is_set():
            with self.lock:
                path, generation = self.model_path, self.model_generation
            self._translate_once(path, generation)
            # A load failure can be recovered by selecting another model.
            while generation == self.model_generation and not self.stop_event.wait(0.2):
                pass

    def _translate_once(self, path, generation):
        translator = None
        try:
            translator = Translator(model_path=path) if path is not None else Translator()
            translator.warmup()
        except Exception as exc:
            if translator is not None:
                translator.close()
            log.exception("Offline translation initialization failed")
            if generation == self.model_generation:
                self._component_status("translation", str(exc) if isinstance(exc, TranslationError) else "翻译模型初始化异常，请右键重新连接", True)
            return
        if generation == self.model_generation and not self.stop_event.is_set():
            self._component_status("translation")
        self.translation_stall_seconds = 30 if translator.device == "CPU" else 8
        delivered = (-1, -1, -1, -1)
        try:
            while not self.stop_event.is_set() and generation == self.model_generation:
                try:
                    job = self.text_queue.get()
                except Empty:
                    continue
                if generation != self.model_generation:
                    # The old worker may have been waiting in get() when the
                    # new model's job arrived. Preserve the latest snapshot.
                    with self.lock:
                        if self.last_job:
                            self.text_queue.put("recent-transcript", self.last_job)
                    break
                if job.revision != self.revision:
                    continue
                with self.lock:
                    self.translation_active_since = time.monotonic()
                    self.stall_notified = False
                try:
                    def cancelled():
                        latest = self.last_job
                        return (self.stop_event.is_set() or generation != self.model_generation or job.revision != self.revision or
                                (latest is not None and latest.utterance_id > job.utterance_id and
                                 latest.ended - job.ended > 1.5))
                    styled = None
                    if job.segments and any(part.runs for part in job.segments):
                        translations, styled = self._translate_runs(translator, job, cancelled)
                    elif job.segments:
                        def progress(index, text):
                            if not cancelled():
                                self._publish(job, (job.segments[index],), (text,), complete=False)

                        translations = translator.translate_segments(
                            tuple(part.text for part in job.segments), job.target, job.source,
                            context=job.context, should_cancel=cancelled,
                            on_progress=progress, prioritize_latest=True,
                        )
                    else:
                        translations = (translator.translate(job.text, job.target, job.source,
                                                              context=job.context, should_cancel=cancelled),)
                    order = (job.revision, job.utterance_id, job.sequence, job.cache_version)
                    if cancelled() or order <= delivered:
                        continue
                    segments = job.segments or (SourceChunk(job.utterance_id, job.text, job.source,
                                                           job.sequence, job.final, job.started, job.ended),)
                    self._publish(job, segments, translations, complete=True, runs=styled)
                    delivered = order
                    self.translation_status.emit("", False, job.revision)
                except TranslationCancelled:
                    continue
                except UntranslatedOutput as exc:
                    if job.final and job.revision == self.revision:
                        self.translation_status.emit(str(exc), True, job.revision)
                except TranslationError as exc:
                    log.warning("Offline translation failed: %s", type(exc.__cause__).__name__)
                    if job.revision == self.revision:
                        self.translation_status.emit(str(exc), True, job.revision)
                except Exception:
                    log.exception("Unexpected translation job failure; continuing with later speech")
                    if job.revision == self.revision:
                        self.translation_status.emit("这次翻译异常，正在继续处理后续语音…", True, job.revision)
                finally:
                    with self.lock:
                        self.translation_active_since = None
        finally:
            translator.close()

    def _translate_runs(self, translator, job, cancelled):
        """Translate speaker turns independently, retaining the original paragraph layout."""
        flat, owners, ranges = [], [], []
        for index, part in enumerate(job.segments):
            start = len(flat)
            for run in part.runs or (CaptionRun(part.text),):
                if run.text.strip():
                    flat.append(run)
                    owners.append(index)
            ranges.append((start, len(flat)))
        values = [None] * len(flat)

        def assemble(start, end):
            if any(value is None for value in values[start:end]):
                return None, ()
            styled = []
            for i in range(start, end):
                if styled:
                    styled.append(CaptionRun(" "))
                styled.append(CaptionRun(values[i].strip(), flat[i].speaker))
            return "".join(run.text for run in styled), tuple(styled)

        def progress(index, text):
            values[index] = text
            owner = owners[index]
            translated, styled = assemble(*ranges[owner])
            if translated and not cancelled():
                self._publish(job, (job.segments[owner],), (translated,), complete=False, runs=(styled,))

        result = translator.translate_segments(tuple(run.text for run in flat), job.target, job.source,
            context=job.context, should_cancel=cancelled, on_progress=progress, prioritize_latest=True)
        values[:] = result
        assembled = [assemble(start, end) for start, end in ranges]
        return tuple(text for text, _ in assembled), tuple(styled for _, styled in assembled)

    def _publish(self, job, segments, translations, complete, runs=None):
        """Publish validated sentences without waiting for a whole rolling window."""
        with self.lock:
            if self.stop_event.is_set() or job.revision != self.revision:
                return
            for segment in segments:
                current = self.source_cache.chunks.get(segment.utterance_id)
                if current and (current.text != segment.text or current.runs != segment.runs):
                    return
            if not complete:
                existing = dict(self.translation_cache.snapshot())
                # Do not roll a previously displayed paragraph back to its first
                # sentence while its changed tail is still being translated.
                if all(len(existing.get(segment.utterance_id, "")) >= len(text)
                       for segment, text in zip(segments, translations)) and (any(
                           len(existing.get(segment.utterance_id, "")) > len(text)
                           for segment, text in zip(segments, translations)) or runs is None or all(
                           self.translation_cache.runs.get(segment.utterance_id) == styled
                           for segment, styled in zip(segments, runs))):
                    return
            retained = {part.utterance_id for part in self.source_cache.snapshot()} or {part.utterance_id for part in segments}
            self.translation_cache.update(segments, translations, retained, runs)
            if complete:
                self.pending_repairs.difference_update(part.utterance_id for part in segments)
            now = time.monotonic()
            # The window owns the visual line limit; retain enough text to
            # immediately reveal more lines or use a smaller font without ASR.
            visible_runs = self.translation_cache.tail_runs(self.translation_cache.max_chars)
            update = CaptionUpdate("".join(run.text for run in visible_runs), job.revision, job.utterance_id,
                                   job.sequence, job.final and complete, max(0, now - job.ended),
                                   max(0, now - job.started), self.source_cache.char_count,
                                   self.translation_cache.char_count, len(self.translation_cache.chunks),
                                   visible_runs)
            signature = (update.text, update.revision, update.final, update.runs)
            if signature == self.last_published:
                return
            self.last_published = signature
            self.last_caption_at = now
        self.caption.emit(update)
        log.info("Caption cache: source=%d translated=%d segments=%d lag=%.3fs",
                 update.source_chars, update.translation_chars, update.translated_segments, update.lag_seconds)
