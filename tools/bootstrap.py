"""Shared, standard-library-only environment setup, invoked by managed Python."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

PYTHON_VERSION = (3, 12)
ROOT = Path(__file__).resolve().parents[1]
BACKEND_INDEX = "https://abetlen.github.io/llama-cpp-python/whl/{}"
LINUX_PACKAGES = (
    "libpulse0", "libpulse-dev", "pulseaudio-utils", "libgl1", "libegl1",
    "libxcb-cursor0", "libxkbcommon-x11-0", "libxcb-icccm4", "libxcb-keysyms1",
    "libxcb-image0", "libxcb-render-util0", "libxcb-xinerama0", "xwayland", "fonts-noto-cjk",
)
PROBE = """
import sys
assert sys.version_info[:2] == (3, 12)
from PySide6.QtWidgets import QApplication
from faster_whisper import WhisperModel
from sherpa_onnx import OfflineSpeakerDiarization, SpeakerEmbeddingExtractor
from onnxruntime import InferenceSession
from llama_cpp import Llama, llama_supports_gpu_offload
from numpy import asarray
from requests import Session
from filelock import FileLock
from importlib.util import find_spec
from pathlib import Path
soundcard = find_spec('soundcard')
assert soundcard and soundcard.origin and Path(soundcard.origin).is_file()
backend = 'mediafoundation' if sys.platform == 'win32' else 'pulseaudio'
for suffix in ('.py', '.py.h'):
    assert Path(soundcard.origin).with_name(backend + suffix).is_file()
if sys.platform == 'linux':
    from pulsectl import Pulse
from importlib.metadata import version
assert version('PySide6-Essentials') == '6.8.3'
assert version('llama-cpp-python') == '0.3.36'
"""


@contextmanager
def setup_lock(path, timeout=1200):
    """OS locks are released on crashes; an abandoned file never blocks setup."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, 2)
        if not handle.tell():
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + timeout
        announced = False
        while True:
            try:
                handle.seek(0)
                if sys.platform == "win32":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Another installation is still running; retry after it finishes.")
                if not announced:
                    print("Waiting for the other environment setup...", flush=True)
                    announced = True
                time.sleep(0.5)
        try:
            yield
        finally:
            handle.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


class Bootstrap:
    def __init__(self, root=ROOT, uv=None, platform=None):
        self.root = Path(root).resolve()
        self.platform = platform or sys.platform
        self.tag = "windows" if self.platform == "win32" else "linux"
        self.runtime = self.root / ".runtime" / self.tag
        self.environment = self.root / (".venv" if self.platform == "win32" else ".venv-linux")
        self.python = self.environment / ("Scripts/python.exe" if self.platform == "win32" else "bin/python")
        self.stamp = self.environment / ".sonoweave-ready.json"
        self.backend_path = self.runtime / "backend"
        self.uv = str(uv) if uv else None

    def fingerprint(self):
        digest = hashlib.sha256()
        for name in ("requirements-base.txt", "requirements.txt", "requirements-cpu.txt", "tools/bootstrap.py"):
            digest.update(name.encode())
            digest.update((self.root / name).read_bytes())
        digest.update(f"{self.platform}:{PYTHON_VERSION}".encode())
        return digest.hexdigest()

    def state(self):
        try:
            value = json.loads(self.stamp.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def probe(self):
        try:
            return subprocess.run([str(self.python), "-c", PROBE], cwd=self.root,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  timeout=45, check=False).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def ready(self, backend="auto"):
        state = self.state()
        return (self.python.is_file() and state.get("fingerprint") == self.fingerprint()
                and state.get("backend") in ("cpu", "vulkan")
                and (backend == "auto" or state["backend"] == backend) and self.probe())

    def command(self, *args):
        subprocess.run([self.uv, "--no-config", *map(str, args)], cwd=self.root, check=True)

    def ensure_system_dependencies(self, backend):
        if self.platform != "linux" or os.environ.get("SONOWEAVE_SKIP_SYSTEM_DEPS") == "1":
            return
        packages = list(LINUX_PACKAGES)
        if backend == "vulkan":
            packages += ["libvulkan1", "mesa-vulkan-drivers"]
        if shutil.which("apt-get") and shutil.which("dpkg-query"):
            missing = []
            for package in packages:
                result = subprocess.run(["dpkg-query", "-W", "-f=${Status}", package],
                                        capture_output=True, text=True, check=False)
                if result.returncode or result.stdout.strip() != "install ok installed":
                    missing.append(package)
            if missing:
                prefix = [] if os.geteuid() == 0 else ["sudo"]
                if prefix and not shutil.which("sudo"):
                    raise RuntimeError("System libraries are missing. Install sudo or see docs/linux.md.")
                print("Installing Linux system libraries (sudo may request your password)...", flush=True)
                subprocess.run([*prefix, "apt-get", "update"], check=True)
                subprocess.run([*prefix, "apt-get", "install", "-y", *missing], check=True)
        else:
            libraries = ["pulse", "GL", "EGL", "xcb-cursor", "xkbcommon-x11", "xcb-icccm", "xcb-image"]
            if backend == "vulkan":
                libraries.append("vulkan")
            missing = [name for name in libraries if not ctypes.util.find_library(name)]
            if missing:
                raise RuntimeError("Install Linux libraries for your distribution: " + ", ".join(missing)
                                   + ". Automatic system packages currently support Debian/Ubuntu; see docs/linux.md.")

    def prepare_environment(self):
        reusable = False
        if self.python.is_file():
            check = ("import sys; from pathlib import Path; "
                     "assert sys.version_info[:2] == (3, 12); "
                     f"assert Path(sys.base_prefix).resolve().is_relative_to(Path({str(self.runtime / 'python')!r}).resolve())")
            try:
                reusable = subprocess.run([str(self.python), "-c", check], cwd=self.root,
                                         capture_output=True, timeout=10, check=False).returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                pass
        if reusable:
            return
        if self.environment.exists():
            if self.environment.is_symlink():
                raise RuntimeError("Virtual environment is a symlink; move it aside before installing.")
            backup = self.root / ".cache" / "previous-environments" / f"{self.tag}-{time.time_ns()}"
            backup.parent.mkdir(parents=True, exist_ok=True)
            self.environment.rename(backup)
            print(f"Previous environment saved to {backup}", flush=True)
        self.command("venv", "--python", sys.executable, self.environment)

    def install(self, backend="auto"):
        if not self.uv:
            raise RuntimeError("uv executable is required")
        with setup_lock(self.runtime / "setup.lock"):
            try:
                requested = self.backend_path.read_text(encoding="utf-8").strip()
            except OSError:
                try:
                    requested = (self.environment / ".sonoweave-backend").read_text(encoding="utf-8").strip()
                except OSError:
                    requested = "vulkan"
            selected = (self.state().get("backend", requested) if backend == "auto" else backend)
            if selected not in ("cpu", "vulkan"):
                selected = "vulkan"
            if self.ready(selected):
                print(f"Environment ready (Python 3.12, {selected}).", flush=True)
                return
            self.backend_path.write_text(selected, encoding="utf-8")
            self.ensure_system_dependencies(selected)
            self.prepare_environment()
            previous = self.state().get("backend")
            # A failed/partial installation must never leave a valid ready marker.
            self.stamp.unlink(missing_ok=True)
            requirements = "requirements-cpu.txt" if selected == "cpu" else "requirements.txt"
            print(f"Installing application dependencies ({selected})...", flush=True)
            # Keep the wheel index scoped to llama.cpp. uv's first-index policy
            # would otherwise hide ordinary PyPI packages behind the wheel index.
            repair = ["--reinstall"] if not self.probe() else []
            self.command("pip", "install", "--python", self.python, "-r", "requirements-base.txt", *repair)
            if previous != selected or not self.probe():
                repair_llama = ["--reinstall"] if not self.probe() else ["--reinstall-package", "llama-cpp-python"]
                self.command("pip", "install", "--python", self.python, *repair_llama,
                             "--index", BACKEND_INDEX.format(selected),
                             "--only-binary", "llama-cpp-python", "llama-cpp-python==0.3.36")
            if not self.probe():
                raise RuntimeError("Dependency verification failed. Retry Install; check system libraries and GPU drivers.")
            temporary = self.stamp.with_suffix(".tmp")
            temporary.write_text(json.dumps({"fingerprint": self.fingerprint(), "backend": selected,
                                             "python": "3.12", "requirements": requirements}), encoding="utf-8")
            temporary.replace(self.stamp)
            print(f"Environment ready (Python 3.12, {selected}). Models download on first use.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--uv", type=Path)
    parser.add_argument("--backend", choices=("auto", "cpu", "vulkan"), default="auto")
    args = parser.parse_args()
    setup = Bootstrap(uv=args.uv)
    if args.check:
        return 0 if setup.ready(args.backend) else 1
    try:
        if sys.version_info[:2] != PYTHON_VERSION:
            raise RuntimeError("Run Install.bat / bash Install.sh to provision managed Python 3.12.")
        setup.install(args.backend)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Environment setup failed: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
