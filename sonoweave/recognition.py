from __future__ import annotations

import logging
import gc
import ctypes
import os
import re
from pathlib import Path
import sys

import numpy as np

from .config import model_location
from .resources import MODEL_LOAD_LOCK, best_gpu, is_memory_error, whisper_compute_types

log = logging.getLogger(__name__)
_dll_handles = []
_cuda_paths = set()


class LanguageTracker:
    """Carry a reliable language across short windows; recheck real changes."""

    def __init__(self):
        self.language = None
        self.utterance_id = None
        self.last_ended = None
        self.next_probe = 0.0
        self.candidate = None
        self.votes = 0

    def begin(self, window):
        if window.utterance_id != self.utterance_id:
            if self.last_ended is not None and window.started - self.last_ended > 20:
                self.language = None
            self.utterance_id = window.utterance_id
            self.next_probe = window.started + 2.5
            self.candidate, self.votes = None, 0
        self.last_ended = window.ended

    def should_probe(self, window):
        return (self.language is not None and window.audio.size >= 40000 and
                window.ended >= self.next_probe)

    def observe(self, language, probability, window):
        self.next_probe = window.ended + 3
        # Very short fragments and carried half-second overlaps are unreliable
        # language evidence, even if Whisper reports a confident probability.
        if window.audio.size < 40000 or probability < 0.8:
            return
        if language == self.language:
            self.candidate, self.votes = None, 0
            return
        self.votes = self.votes + 1 if self.candidate == language else 1
        self.candidate = language
        if self.language is None or probability >= 0.95 or self.votes >= 2:
            self.language = language
            self.candidate, self.votes = None, 0
            log.info("Source language stabilized: %s", language)


class AdaptiveDecoding:
    """Escalate a stuck greedy hypothesis before waiting for a final window."""

    def __init__(self):
        self.utterance_id = None
        self.last_text = ""
        self.last_progress = 0.0
        self.refining = False

    def beam(self, window):
        if window.utterance_id != self.utterance_id:
            self.utterance_id = window.utterance_id
            self.last_text = ""
            self.last_progress = window.started
            self.refining = False
        if window.ended - self.last_progress >= 1.5:
            self.refining = True
        return 3 if window.final or self.refining else 1

    def observe(self, text, window):
        normalized = re.sub(r"\W+", "", text).casefold()
        if normalized and normalized != self.last_text:
            self.last_text = normalized
            self.last_progress = window.ended


def configure_cuda():
    os.environ.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
    if sys.platform != "win32":
        return
    paths = []
    bundle = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else None
    if bundle is not None:
        paths.append(bundle)
    for name, value in os.environ.items():
        if name.startswith("CUDA_PATH"):
            paths.append(Path(value) / "bin")
    toolkit = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "NVIDIA GPU Computing Toolkit" / "CUDA"
    if toolkit.is_dir():
        paths.extend(sorted(toolkit.glob("v*/bin"), reverse=True))
    # CUDA wheels and frozen bundles can carry their own CUDA runtime DLLs.
    for root in (Path(sys.prefix) / "Lib/site-packages", Path(getattr(sys, "_MEIPASS", ""))):
        paths.extend((root / "llama_cpp/lib", root / "nvidia/cublas/bin", root / "nvidia/cudnn/bin"))
    for path in paths:
        if path.is_dir() and str(path) not in _cuda_paths:
            try:
                _dll_handles.append(os.add_dll_directory(str(path)))
                _cuda_paths.add(str(path))
            except OSError:
                pass
    # cuBLAS is loaded by name by CTranslate2, so Windows may otherwise choose
    # a system toolkit first. Load the bundled pair by absolute path first.
    if bundle is not None:
        for name in ("cublasLt64_12.dll", "cublas64_12.dll"):
            library = bundle / name
            key = "loaded:" + str(library)
            if library.is_file() and key not in _cuda_paths:
                try:
                    _dll_handles.append(ctypes.WinDLL(str(library)))
                    _cuda_paths.add(key)
                except OSError:
                    log.warning("Bundled CUDA library could not be loaded: %s", name)


class AdaptiveRecognizer:
    """Consume lazy Whisper output before publishing, retrying GPU OOM on CPU."""

    def __init__(self, recognizer, cpu_factory):
        self.recognizer = recognizer
        self.cpu_factory = cpu_factory

    def __getattr__(self, name):
        return getattr(self.recognizer, name)

    def _call(self, method, *args, **kwargs):
        for attempt in range(2):
            try:
                value = getattr(self.recognizer, method)(*args, **kwargs)
                if method == "transcribe":
                    segments, info = value
                    return tuple(segments), info
                return value
            except Exception as exc:
                if attempt or self.model.device != "cuda" or not is_memory_error(exc):
                    raise
            # Exit the exception handler before releasing CUDA allocations.
            log.warning("CUDA recognition ran out of memory; retrying the same audio on CPU")
            with MODEL_LOAD_LOCK:
                self.close()
                self.recognizer = self.cpu_factory()

    def transcribe(self, *args, **kwargs):
        return self._call("transcribe", *args, **kwargs)

    def detect_language(self, *args, **kwargs):
        return self._call("detect_language", *args, **kwargs)

    def close(self):
        if self.recognizer is not None:
            self.recognizer.model.unload_model()
            self.recognizer = None
            gc.collect()


def load_recognizer(device=None, cpu_threads=None):
    device = device or os.environ.get("SONOWEAVE_ASR_DEVICE", "auto")
    if device != "cpu":
        configure_cuda()
    from faster_whisper import WhisperModel
    import ctranslate2

    path = model_location()
    if not (Path(path) / "model.bin").is_file():
        raise RuntimeError("缺少语音模型，请重新启动以下载，或运行 tools/prepare_models.py")
    def cpu_factory():
        model = WhisperModel(path, device="cpu", compute_type="int8", local_files_only=True,
                             cpu_threads=cpu_threads or min(6, max(2, (os.cpu_count() or 4) // 2)))
        log.info("Offline recognition ready: Whisper %s, CPU int8", Path(path).name)
        return model

    with MODEL_LOAD_LOCK:
        try:
            cuda_count = ctranslate2.get_cuda_device_count() if device != "cpu" else 0
        except Exception:
            cuda_count = 0
        if cuda_count > 0:
            gpu = best_gpu()
            for compute in whisper_compute_types(gpu, (Path(path) / "model.bin").stat().st_size):
                model = None
                try:
                    model = WhisperModel(path, device="cuda", device_index=gpu.index if gpu else 0,
                                         compute_type=compute, local_files_only=True)
                    list(model.transcribe(np.zeros(48000, dtype=np.float32), language="en",
                                          beam_size=3, vad_filter=False, max_new_tokens=2)[0])
                    log.info("Offline recognition ready: Whisper %s, CUDA %s", Path(path).name, compute)
                    return AdaptiveRecognizer(model, cpu_factory)
                except Exception as exc:
                    memory_error = is_memory_error(exc)
                    log.warning("CUDA recognition initialization failed (%s)", type(exc).__name__)
                if model is not None:
                    model.model.unload_model()
                    model = None
                gc.collect()
                if not memory_error:
                    break
        return AdaptiveRecognizer(cpu_factory(), cpu_factory)
