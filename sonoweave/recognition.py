from __future__ import annotations

import logging
import os
from pathlib import Path
import sys

import numpy as np

from .config import model_location

log = logging.getLogger(__name__)
_dll_handles = []


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
            log.info("Offline recognition ready: Whisper base, CUDA float16")
            return model
        except Exception:
            log.warning("CUDA recognition unavailable; falling back to CPU")
    model = WhisperModel(path, device="cpu", compute_type="int8", local_files_only=True,
                         cpu_threads=min(6, max(2, (os.cpu_count() or 4) // 2)))
    log.info("Offline recognition ready: Whisper base, CPU int8")
    return model
