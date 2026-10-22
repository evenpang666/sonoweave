"""Check the packaged UI, local model loading, and loopback initialization."""
from pathlib import Path
import os
import subprocess
import sys
import time

root = Path(__file__).resolve().parents[1]
folder = root / ".cache" / f"bundle-test-{time.time_ns()}"
folder.mkdir(parents=True, exist_ok=True)
env = os.environ.copy()
env.update(QT_QPA_PLATFORM="offscreen", SONOWEAVE_HOME=str(folder), HF_HOME=str(folder / "huggingface"))
exe = root / "dist" / "SonoWeave" / "SonoWeave.exe"
image = folder / "preview.png"
subprocess.run([str(exe), "--screenshot", str(image)], env=env, check=True, timeout=30)
assert image.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
print("BUNDLED UI OK", flush=True)
if "--preview-only" in sys.argv:
    raise SystemExit(0)
process = subprocess.Popen([str(exe)], env=env)
try:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"App exited prematurely: {process.returncode}")
        log = folder / "app.log"
        if log.exists():
            contents = log.read_text(encoding="utf-8")
            ready = "正在聆听系统声音" in contents
            captions = "Caption cache:" in contents
            if ready and ("--require-captions" not in sys.argv or captions):
                print("BUNDLED MODEL + SYSTEM AUDIO OK", flush=True)
                if captions:
                    print("BUNDLED LIVE TRANSLATION CACHE OK", flush=True)
                break
            if "ERROR" in contents:
                raise RuntimeError(contents)
        time.sleep(0.2)
    else:
        raise TimeoutError("Packaged audio engine did not initialize")
finally:
    process.terminate()
    process.wait(timeout=10)
