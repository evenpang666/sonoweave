"""Shared GPU admission control. Estimates leave room for inference and desktop use."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import struct
import subprocess
import threading

MIB = 1024 ** 2
GPU_HEADROOM = 768 * MIB
MODEL_LOAD_LOCK = threading.RLock()


@dataclass(frozen=True)
class GPU:
    index: int  # Logical CUDA ordinal, after CUDA_VISIBLE_DEVICES.
    free: int
    total: int
    name: str


def available_gpus():
    """Query only at model load/recovery, never on each subtitle or token."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,uuid,memory.free,memory.total,name", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3, check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        rows = [tuple(part.strip() for part in line.split(",", 4))
                for line in result.stdout.splitlines() if line.strip()]
        visible = os.environ.get("CUDA_VISIBLE_DEVICES")
        if visible is not None:
            selected = []
            for key in visible.split(","):
                match = next((row for row in rows if key.strip() == row[0] or
                              (key.strip().startswith("GPU-") and row[1].startswith(key.strip()))), None)
                if match is None or match in selected:
                    break
                selected.append(match)
            rows = selected
        return tuple(GPU(i, int(row[2]) * MIB, int(row[3]) * MIB, row[4])
                     for i, row in enumerate(rows))
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return ()


def best_gpu():
    return max(available_gpus(), key=lambda gpu: gpu.free, default=None)


def is_memory_error(exc):
    """Recover allocation failures; do not hide corrupt models or invalid input."""
    text = str(exc).lower()
    return any(mark in text for mark in (
        "out of memory", "out_of_memory", "cudaerrormemoryallocation", "cublas_status_alloc_failed",
        "failed to allocate", "unable to allocate", "not enough memory", "insufficient memory",
        "alloc_buffer: failed", "cudnn_status_alloc_failed"))


def whisper_compute_types(gpu, model_bytes):
    # Small's fp16 weights plus encoder/decoder workspace. Larger overrides scale.
    fp16 = max(1200 * MIB, model_bytes * 2)
    int8 = max(850 * MIB, model_bytes * 1.25)
    if gpu is None:  # Monitoring unavailable: try CUDA, with real allocation fallback.
        return ("float16", "int8_float16")
    usable = gpu.free - GPU_HEADROOM
    if usable >= fp16:
        return ("float16", "int8_float16")
    return ("int8_float16",) if usable >= int8 else ()


def gguf_block_count(path):
    """Read scalar GGUF metadata without loading weights or a second Llama instance."""
    sizes = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
    with Path(path).open("rb") as stream:
        def number(fmt):
            return struct.unpack(fmt, stream.read(struct.calcsize(fmt)))[0]

        def string():
            length = number("<Q")
            if length > 1024 * 1024:
                raise ValueError("Invalid GGUF metadata string")
            return stream.read(length).decode("utf-8")

        def skip(kind):
            if kind == 8:
                stream.seek(number("<Q"), 1)
            elif kind == 9:
                element, count = number("<I"), number("<Q")
                if element in sizes:
                    stream.seek(sizes[element] * count, 1)
                elif element == 8 and count <= 1000000:
                    for _ in range(count):
                        skip(element)
                else:
                    raise ValueError("Invalid GGUF array")
            else:
                stream.seek(sizes[kind], 1)

        if stream.read(4) != b"GGUF" or number("<I") not in (2, 3):
            raise ValueError("Invalid GGUF header")
        number("<Q")  # tensor count
        count = number("<Q")
        for _ in range(min(count, 65536)):
            key, kind = string(), number("<I")
            if key.endswith(".block_count") and kind == 4:
                blocks = number("<I")
                if 0 < blocks <= 4096:
                    return blocks
                break
            skip(kind)
    raise ValueError("GGUF block count is missing")


def translation_layers(path, context_size, gpu):
    """Prefer all layers, then a bounded GPU/CPU split, then CPU."""
    try:
        blocks = gguf_block_count(path)
    except (OSError, ValueError, KeyError, struct.error, UnicodeError):
        blocks = 32  # Conservative fallback for nonstandard GGUF overrides.
    if gpu is None:
        return (-1, max(1, blocks // 2), 0)
    workspace = 512 * MIB + context_size * 128 * 1024
    usable = gpu.free - GPU_HEADROOM - workspace
    weights = Path(path).stat().st_size * 1.1
    if usable >= weights:
        initial = -1
    else:
        initial = max(0, min(blocks, int(blocks * usable / max(1, weights))))
        if initial < min(4, blocks):
            return (0,)
    reduced = max(1, (blocks if initial == -1 else initial) // 2)
    return tuple(dict.fromkeys((initial, reduced, 0)))
