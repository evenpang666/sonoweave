from __future__ import annotations

import logging
import os
import re
from pathlib import Path
import sys

import numpy as np

from .config import model_location

log = logging.getLogger(__name__)
_dll_handles = []


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
    if sys.platform != "win32":
        return
    paths = []
    for name, value in os.environ.items():
        if name.startswith("CUDA_PATH"):
            paths.append(Path(value) / "bin")
    toolkit = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "NVIDIA GPU Computing Toolkit" / "CUDA"
    if toolkit.is_dir():
        paths.extend(sorted(toolkit.glob("v*/bin"), reverse=True))
    for path in paths:
        if path.is_dir():
            try:
                _dll_handles.append(os.add_dll_directory(str(path)))
            except OSError:
                pass


def load_recognizer():
    from faster_whisper import WhisperModel
    import ctranslate2

    path = model_location()
    if not (Path(path) / "model.bin").is_file():
        raise RuntimeError("缺少离线语音模型，请先运行 PrepareModels.bat")
    configure_cuda()
    device = os.environ.get("SONOWEAVE_ASR_DEVICE", "auto")
    if device != "cpu" and ctranslate2.get_cuda_device_count() > 0:
        try:
            model = WhisperModel(path, device="cuda", compute_type="float16", local_files_only=True)
            list(model.transcribe(np.zeros(4800, dtype=np.float32), language="en",
                                  beam_size=1, vad_filter=False, max_new_tokens=2)[0])
            log.info("Offline recognition ready: Whisper %s, CUDA float16", Path(path).name)
            return model
        except Exception:
            log.warning("CUDA recognition unavailable; falling back to CPU")
    model = WhisperModel(path, device="cpu", compute_type="int8", local_files_only=True,
                         cpu_threads=min(6, max(2, (os.cpu_count() or 4) // 2)))
    log.info("Offline recognition ready: Whisper %s, CPU int8", Path(path).name)
    return model
