"""Offline device verification for source and frozen builds; never captures audio."""
from dataclasses import asdict
import ctypes
import json
from pathlib import Path
import sys
import time

from .recognition import load_recognizer
from .resources import best_gpu
from .translation import Translator


def resource_report():
    recognizer = translator = None
    start = time.monotonic()
    try:
        recognizer = load_recognizer()
        translator = Translator()
        # Exercise decoding as well as weight loading, without a microphone.
        text = translator.translate("The local translation model is ready.", "zh-CN", "en")
        gpu = best_gpu()
        result = dict(asr_device=recognizer.model.device, asr_compute_type=recognizer.model.compute_type,
                      translation_device=translator.device, gpu_layers=translator.gpu_layers,
                      gpu=asdict(gpu) if gpu else None, translated=bool(text),
                      elapsed_seconds=round(time.monotonic() - start, 3), cuda_libraries={})
        if sys.platform == "win32":
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetModuleHandleW.restype = ctypes.c_void_p
            buffer = ctypes.create_unicode_buffer(32768)
            for name in ("cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll", "ggml-cuda.dll"):
                handle = kernel.GetModuleHandleW(name)
                if handle and kernel.GetModuleFileNameW(ctypes.c_void_p(handle), buffer, len(buffer)):
                    result["cuda_libraries"][name] = buffer.value
        return result
    finally:
        if translator is not None:
            translator.close()
        if recognizer is not None:
            recognizer.close()


def write_resource_report(path):
    try:
        report, status = resource_report(), 0
    except Exception as exc:
        report, status = {"error": type(exc).__name__, "message": str(exc)}, 1
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return status
