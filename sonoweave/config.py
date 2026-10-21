from __future__ import annotations

import json
import os
from pathlib import Path
import sys

LANGUAGES = {
    "zh-CN": "简体中文", "zh-TW": "繁體中文", "en": "English",
    "ja": "日本語", "ko": "한국어", "fr": "Français", "de": "Deutsch",
    "es": "Español", "pt": "Português", "ru": "Русский", "it": "Italiano",
    "ar": "العربية", "hi": "हिन्दी", "th": "ไทย", "vi": "Tiếng Việt",
    "id": "Bahasa Indonesia", "tr": "Türkçe", "uk": "Українська",
}


def data_dir() -> Path:
    override = os.environ.get("SONOWEAVE_HOME")
    root = Path(override) if override else Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "SonoWeave"
    root.mkdir(parents=True, exist_ok=True)
    return root


def model_location() -> str:
    configured = os.environ.get("SONOWEAVE_MODEL")
    if configured:
        return configured
    root = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    bundled = root / "models" / "base"
    return str(bundled)


def translation_model_location() -> Path:
    configured = os.environ.get("SONOWEAVE_TRANSLATE_MODEL")
    if configured:
        return Path(configured)
    root = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    return root / "models" / "translation" / "Qwen3-1.7B-Q4_K_M.gguf"


class Settings:
    def __init__(self, path: Path | None = None):
        self.path = path if path is not None else data_dir() / "settings.json"
        self.target = "zh-CN"
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and value.get("target") in LANGUAGES:
                self.target = value["target"]
        except (OSError, ValueError, TypeError):
            pass

    def save_target(self, target: str) -> None:
        if target not in LANGUAGES:
            raise ValueError("Unsupported target language")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"target": target}, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path)
        self.target = target
