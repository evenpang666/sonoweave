"""Shared PyInstaller configuration, including dynamically loaded CUDA libraries."""
import os
from pathlib import Path
import sys


def cuda_binaries(platform=sys.platform, environ=None):
    env = os.environ if environ is None else environ
    windows = platform == "win32"
    directories = []
    for key, value in env.items():
        if key.startswith("CUDA_PATH"):
            directories.append(Path(value) / ("bin" if windows else "lib64"))
    directories.extend(Path(value) for value in env.get("PATH" if windows else "LD_LIBRARY_PATH", "").split(os.pathsep) if value)
    if windows:
        site = Path(sys.prefix) / "Lib/site-packages"
    else:
        site = Path(sys.prefix) / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
        directories.extend((Path("/usr/local/cuda/lib64"), Path("/usr/lib/x86_64-linux-gnu")))
    for package in ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc", "nvjitlink"):
        directories.append(site / "nvidia" / package / ("bin" if windows else "lib"))
    patterns = (("cublas*64_12.dll", "cudnn*64_9.dll", "cudart64_12.dll", "nvrtc*64_12*.dll", "nvJitLink_12*.dll") if windows else
                ("libcublas*.so.12*", "libcudnn*.so.9*", "libcudart.so.12*", "libnvrtc*.so.12*", "libnvJitLink.so.12*"))
    found = {}
    for directory in directories:
        for pattern in patterns:
            for path in sorted(directory.glob(pattern)):
                if path.is_file():
                    found.setdefault(path.name.lower(), (str(path), "."))
    # Never copy the NVIDIA display driver (nvcuda.dll / libcuda.so).
    return list(found.values())


def build_arguments():
    args = ["--noconfirm", "--windowed", "--onedir", "--name", "SonoWeave",
            "--collect-data", "soundcard", "--collect-data", "certifi"]
    packages = ["faster_whisper", "ctranslate2", "sherpa_onnx", "onnxruntime", "llama_cpp"]
    if sys.platform == "linux":
        packages.append("pulsectl")
    for package in packages:
        args.extend(("--collect-all", package))
    for source, destination in cuda_binaries():
        args.extend(("--add-binary", source + os.pathsep + destination))
    for key, value in os.environ.items():
        license_path = Path(value) / "EULA.txt" if key.startswith("CUDA_PATH") else None
        if license_path is not None and license_path.is_file():
            args.extend(("--add-data", str(license_path) + os.pathsep + "licenses/NVIDIA-CUDA"))
    return [*args, "main.py"]


if __name__ == "__main__":
    import PyInstaller.__main__
    PyInstaller.__main__.run(build_arguments())
