"""Explicit, one-time online provisioning. The desktop app never downloads."""
import os
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
os.environ["HF_HOME"] = str(ROOT / ".cache" / "huggingface")
os.environ["HF_HUB_DISABLE_XET"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = "120"
os.environ.pop("HF_HUB_OFFLINE", None)

from huggingface_hub import hf_hub_download

for filename in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
    print("Preparing Whisper:", filename, flush=True)
    hf_hub_download("Systran/faster-whisper-base", filename,
                    revision="ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66", local_dir=ROOT / "models" / "base")
print("Preparing Qwen3-1.7B Q4_K_M (about 1.1 GB)…", flush=True)
hf_hub_download("unsloth/Qwen3-1.7B-GGUF", "Qwen3-1.7B-Q4_K_M.gguf",
                revision="d7f544eead698dbd1f15126ef60b45a1e1933222", local_dir=ROOT / "models" / "translation")
sample = ROOT / ".cache" / "jfk.flac"
if "--test-sample" in sys.argv and not sample.exists():
    sample.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen("https://raw.githubusercontent.com/SYSTRAN/faster-whisper/master/tests/data/jfk.flac", timeout=30) as response:
        sample.write_bytes(response.read())
print("Ready. Recognition and translation can now run without a network connection.")
