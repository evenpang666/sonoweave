from __future__ import annotations

from dataclasses import replace
import logging
from queue import Empty
import threading
import time

from PySide6.QtCore import QObject, Signal

from .audio import capture_loopback
from .config import LANGUAGES
from .recognition import load_recognizer
from .streaming import CaptionUpdate, LatestQueue, SourceChunk, TextJob, TranscriptCache, TranslationCache
from .translation import TranslationCancelled, TranslationError, Translator, UntranslatedOutput

log = logging.getLogger(__name__)


class CaptionEngine(QObject):
    caption = Signal(object)
    status = Signal(str, bool)
    translation_status = Signal(str, bool, int)

    def __init__(self, target):
        super().__init__()
        self.target = target
        self.revision = 0
        self.stop_event = threading.Event()
        self.audio_queue = LatestQueue(maxsize=3)
        self.text_queue = LatestQueue(maxsize=1)
        self.source_cache = TranscriptCache()
        self.translation_cache = TranslationCache()
        self.lock = threading.Lock()
        self.last_job = None
        self.threads = []
        self.ready = set()
        self.errors = {}

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
        language = None
        utterance_id = None
        while not self.stop_event.is_set():
            try:
                window = self.audio_queue.get()
            except Empty:
                continue
            if not window.final and time.monotonic() - window.ended > 4:
                continue
            if window.utterance_id != utterance_id:
                utterance_id, language = window.utterance_id, None
            try:
                segments, info = model.transcribe(
                    window.audio, beam_size=1, language=language, vad_filter=True,
                    vad_parameters={"min_silence_duration_ms": 200, "speech_pad_ms": 100},
                    condition_on_previous_text=False, temperature=0,
                    max_new_tokens=160, without_timestamps=True,
                )
                parts = [segment.text.strip() for segment in segments if segment.no_speech_prob < 0.6]
                text = " ".join(part for part in parts if part).strip()
                if not text or self.stop_event.is_set():
                    continue
                if language is None and getattr(info, "language_probability", 1) >= 0.65:
                    language = info.language
                source = language or info.language
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

    def _enqueue_audio(self, window):
        self.audio_queue.put(window.utterance_id, window)

    def _translate(self):
        try:
            translator = Translator()
            translator.warmup()
        except TranslationError as exc:
            log.exception("Offline translation initialization failed")
            self._component_status("translation", str(exc), True)
            return
        self._component_status("translation")
        delivered = (-1, -1, -1)
        try:
            while not self.stop_event.is_set():
                try:
                    job = self.text_queue.get()
                except Empty:
                    continue
                if job.revision != self.revision:
                    continue
                try:
                    cancelled = lambda: self.stop_event.is_set() or job.revision != self.revision
                    if job.segments:
                        translations = translator.translate_segments(
                            tuple(part.text for part in job.segments), job.target, job.source,
                            context=job.context, should_cancel=cancelled,
                        )
                    else:
                        translations = (translator.translate(job.text, job.target, job.source,
                                                              context=job.context, should_cancel=cancelled),)
                    order = (job.revision, job.utterance_id, job.sequence)
                    with self.lock:
                        if cancelled() or order <= delivered:
                            continue
                        segments = job.segments or (SourceChunk(job.utterance_id, job.text, job.source,
                                                               job.sequence, job.final, job.started, job.ended),)
                        retained = {part.utterance_id for part in self.source_cache.snapshot()} or {part.utterance_id for part in segments}
                        self.translation_cache.update(segments, translations, retained)
                        text = self.translation_cache.tail()
                        delivered = order
                        now = time.monotonic()
                        update = CaptionUpdate(text, job.revision, job.utterance_id, job.sequence, job.final,
                                               max(0, now - job.ended), max(0, now - job.started),
                                               self.source_cache.char_count, self.translation_cache.char_count,
                                               len(self.translation_cache.chunks))
                    self.translation_status.emit("", False, job.revision)
                    self.caption.emit(update)
                    log.info("Caption cache: source=%d translated=%d segments=%d lag=%.3fs",
                             update.source_chars, update.translation_chars, update.translated_segments, update.lag_seconds)
                except TranslationCancelled:
                    continue
                except UntranslatedOutput as exc:
                    if job.final and job.revision == self.revision:
                        self.translation_status.emit(str(exc), True, job.revision)
                except TranslationError as exc:
                    log.warning("Offline translation failed: %s", type(exc.__cause__).__name__)
                    if job.revision == self.revision:
                        self.translation_status.emit(str(exc), True, job.revision)
        finally:
            translator.close()
