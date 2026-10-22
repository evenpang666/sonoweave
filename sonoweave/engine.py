from __future__ import annotations

from dataclasses import replace
import logging
from queue import Empty
import threading
import time

from PySide6.QtCore import QObject, Signal

from .audio import capture_loopback
from .config import LANGUAGES
from .recognition import AdaptiveDecoding, LanguageTracker, load_recognizer
from .streaming import CaptionUpdate, LatestQueue, SourceChunk, TextJob, TranscriptCache, TranslationCache
from .translation import TranslationCancelled, TranslationError, Translator, UntranslatedOutput

log = logging.getLogger(__name__)


class CaptionEngine(QObject):
    caption = Signal(object)
    status = Signal(str, bool)
    translation_status = Signal(str, bool, int)

    def __init__(self, target, model_path=None):
        super().__init__()
        self.target = target
        self.model_path = model_path
        self.model_generation = 0
        self.revision = 0
        self.stop_event = threading.Event()
        self.audio_queue = LatestQueue(maxsize=3)
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
        for name, function in (("system-audio", self._capture), ("recognition", self._recognize), ("translation", self._translate)):
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

    def _component_status(self, name, text="", error=False):
        with self.lock:
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
            try:
                if language_tracker.should_probe(window):
                    detected, probability, _ = model.detect_language(audio=window.audio[-128000:])
                    language_tracker.observe(detected, probability, window)
                language = language_tracker.language
                segments, info = model.transcribe(
                    window.audio, beam_size=decoding.beam(window), language=language, vad_filter=True,
                    vad_parameters={"min_silence_duration_ms": 200, "speech_pad_ms": 100},
                    condition_on_previous_text=False, temperature=0,
                    max_new_tokens=160, without_timestamps=True,
                )
                # faster-whisper already combines no-speech probability with
                # decoder confidence. A second probability-only filter drops
                # valid speech it deliberately retained, leaving captions stale.
                parts = [segment.text.strip() for segment in segments]
                text = " ".join(part for part in parts if part).strip()
                decoding.observe(text, window)
                if not text or self.stop_event.is_set():
                    continue
                if language is None:
                    language_tracker.observe(info.language, getattr(info, "language_probability", 0), window)
                source = info.language
                with self.lock:
                    chunk = SourceChunk(window.utterance_id, text, source, window.sequence,
                                        window.final, window.started, window.ended)
                    if not self.source_cache.update(chunk, window.overlap_previous):
                        continue
                    recent = self.source_cache.recent()
                    context = self.source_cache.context_before(recent[0].utterance_id)
                    job = TextJob("\n".join(part.text for part in recent), source, self.target, self.revision,
                                  window.utterance_id, window.sequence, window.final, context,
                                  window.started, window.ended, recent, self.source_cache.version)
                    self.last_job = job
                    self.text_queue.put("recent-transcript", job)
                self._component_status("recognition")
            except Exception:
                log.exception("Speech recognition failed")
                self._component_status("recognition", "语音识别暂时失败，正在继续聆听…", True)
            finally:
                with self.lock:
                    self.recognition_active_since = None

    def _enqueue_audio(self, window):
        self.audio_queue.put(window.utterance_id, window)

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
        delivered = (-1, -1, -1)
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
                    if job.segments:
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
                    order = (job.revision, job.utterance_id, job.sequence)
                    if cancelled() or order <= delivered:
                        continue
                    segments = job.segments or (SourceChunk(job.utterance_id, job.text, job.source,
                                                           job.sequence, job.final, job.started, job.ended),)
                    self._publish(job, segments, translations, complete=True)
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

    def _publish(self, job, segments, translations, complete):
        """Publish validated sentences without waiting for a whole rolling window."""
        with self.lock:
            if self.stop_event.is_set() or job.revision != self.revision:
                return
            if not complete:
                existing = dict(self.translation_cache.snapshot())
                # Do not roll a previously displayed paragraph back to its first
                # sentence while its changed tail is still being translated.
                if all(len(existing.get(segment.utterance_id, "")) >= len(text)
                       for segment, text in zip(segments, translations)):
                    return
            retained = {part.utterance_id for part in self.source_cache.snapshot()} or {part.utterance_id for part in segments}
            self.translation_cache.update(segments, translations, retained)
            now = time.monotonic()
            update = CaptionUpdate(self.translation_cache.tail(), job.revision, job.utterance_id,
                                   job.sequence, job.final and complete, max(0, now - job.ended),
                                   max(0, now - job.started), self.source_cache.char_count,
                                   self.translation_cache.char_count, len(self.translation_cache.chunks))
            signature = (update.text, update.revision, update.final)
            if signature == self.last_published:
                return
            self.last_published = signature
            self.last_caption_at = now
        self.caption.emit(update)
        log.info("Caption cache: source=%d translated=%d segments=%d lag=%.3fs",
                 update.source_chars, update.translation_chars, update.translated_segments, update.lag_seconds)
