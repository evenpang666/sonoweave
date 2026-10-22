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

TRANSLATION_MODELS = {
    "4b": {"label": "4B · 质量优先（默认，2.74 GB）", "filename": "Qwen3.5-4B-Q4_K_M.gguf",
           "repo": "unsloth/Qwen3.5-4B-GGUF", "revision": "e87f176479d0855a907a41277aca2f8ee7a09523",
           "bytes": 2740937888, "sha256": "00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4"},
    "2b": {"label": "2B · 速度优先（1.28 GB）", "filename": "Qwen3.5-2B-Q4_K_M.gguf",
           "repo": "unsloth/Qwen3.5-2B-GGUF", "revision": "f6d5376be1edb4d416d56da11e5397a961aca8ae",
           "bytes": 1280835840, "sha256": "aaf42c8b7c3cab2bf3d69c355048d4a0ee9973d48f16c731c0520ee914699223"},
}


def models_dir() -> Path:
    if os.environ.get("SONOWEAVE_MODELS_DIR"):
        return Path(os.environ["SONOWEAVE_MODELS_DIR"])
    if getattr(sys, "frozen", False):
        return data_dir() / "models"
    return Path(__file__).resolve().parents[1] / "models"


def _model_path(relative: Path) -> Path:
    path = models_dir() / relative
    # Continue using already provisioned portable releases without re-downloading.
    if not path.exists() and getattr(sys, "frozen", False) and not os.environ.get("SONOWEAVE_MODELS_DIR"):
        legacy = Path(sys.executable).parent / "models" / relative
        if legacy.exists():
            return legacy
    return path


def data_dir() -> Path:
    override = os.environ.get("SONOWEAVE_HOME")
    root = Path(override) if override else Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "SonoWeave"
    root.mkdir(parents=True, exist_ok=True)
    return root


def model_location() -> str:
    configured = os.environ.get("SONOWEAVE_MODEL")
    if configured:
        return configured
    return str(_model_path(Path("small")))


def translation_model_location(model: str | None = None) -> Path:
    configured = os.environ.get("SONOWEAVE_TRANSLATE_MODEL")
    if configured and model is None:
        return Path(configured)
    selected = model or "4b"
    return _model_path(Path("translation") / TRANSLATION_MODELS[selected]["filename"])


class Settings:
    def __init__(self, path: Path | None = None):
        self.path = path if path is not None else data_dir() / "settings.json"
        self.target = "zh-CN"
        self.model = "4b"
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and value.get("target") in LANGUAGES:
                self.target = value["target"]
            if isinstance(value, dict) and value.get("model") in TRANSLATION_MODELS:
                self.model = value["model"]
        except (OSError, ValueError, TypeError):
            pass

    def save_target(self, target: str) -> None:
        if target not in LANGUAGES:
            raise ValueError("Unsupported target language")
        self._save(target, self.model)
        self.target = target

    def save_model(self, model: str) -> None:
        if model not in TRANSLATION_MODELS:
            raise ValueError("Unsupported translation model")
        self._save(self.target, model)
        self.model = model

    def _save(self, target, model):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"target": target, "model": model}, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path)
