"""Real offline inference checks, with Python socket connections blocked."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(SONOWEAVE_HOME=str(ROOT / ".runtime"), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")

from sonoweave.recognition import load_recognizer
from sonoweave.translation import Translator
from sonoweave.config import model_location, translation_model_location


def no_network(*args, **kwargs):
    raise AssertionError("Runtime attempted a network connection")


def main():
    report = {"network_blocked": True, "translations": [], "asr_model": Path(model_location()).name,
              "translation_model": translation_model_location().name}
    with patch.object(socket.socket, "connect", no_network), patch.object(socket, "create_connection", no_network):
        start = time.perf_counter()
        recognizer = load_recognizer()
        report["asr_device"] = recognizer.model.device
        sample = ROOT / ".cache" / "jfk.flac"
        assert sample.is_file(), "Run prepare_models.py --test-sample first"
        segments, info = recognizer.transcribe(str(sample), beam_size=1, vad_filter=True)
        text = " ".join(segment.text.strip() for segment in segments)
        assert "country" in text.lower(), text
        report["recognized"] = text
        report["asr_load_and_infer_seconds"] = time.perf_counter() - start
        translator = Translator()
        start = time.perf_counter()
        translator.warmup()
        report["translation_warmup_seconds"] = time.perf_counter() - start
        report["translation_device"] = translator.device
        cases = [
            ("He stood beside the bank.", "en", "zh-CN", ("We were fishing by the river.",)),
            ("He stood beside the bank.", "en", "zh-CN", ("He needed to withdraw money from his account.",)),
            ("I did not say he stole the money.", "en", "zh-CN", ()),
            ("The temperature is 37.5 degrees, not 35 degrees.", "en", "zh-CN", ()),
            ("这个模型完全在本机运行。", "zh", "en", ()),
            ("今日は天気がいいですね。", "ja", "zh-CN", ()),
            ("Das Modell funktioniert ohne Internet.", "de", "zh-CN", ()),
            ("The model runs entirely offline.", "en", "ja", ()),
            (text, info.language, "zh-CN", ()),
            ("They really knocked it out of the park economically.", "en", "zh-CN", ()),
            ("But I", "en", "zh-CN", ()),
            ("not going to be us.", "en", "zh-CN", ("Communist countries were falling in Europe.", "He said that is")),
            ("Economic development was a hard target, something you can easily measure with a number.", "en", "zh-CN", ("Officials were assessed on a scorecard with performance indicators.",)),
        ]
        try:
            for text, source, target, context in cases:
                start = time.perf_counter()
                translated = translator.translate(text, target, source, context=context)
                row = dict(source=text, target=target, context=context, translation=translated,
                           seconds=round(time.perf_counter() - start, 3))
                report["translations"].append(row)
                print(json.dumps(row, ensure_ascii=True), flush=True)
        finally:
            translator.close()
        river, finance = [item["translation"] for item in report["translations"][:2]]
        assert river != finance and any(char in river for char in ("河", "岸")) and "银行" in finance, (river, finance)
        assert any(char in report["translations"][2]["translation"] for char in ("没", "不", "未", "非"))
        assert "37.5" in report["translations"][3]["translation"]
        idiom, fragment, pronoun, term = [row["translation"] for row in report["translations"][9:13]]
        assert "公园" not in idiom and any(word in idiom for word in ("出色", "预期", "优", "成功", "卓越")), idiom
        assert "我" in fragment and "不认为" not in fragment, fragment
        assert any(word in pronoun for word in ("我们", "咱们")) and "美国" not in pronoun, pronoun
        report["semantic_checks"] = {
            "idiom_not_literal": "公园" not in idiom,
            "incomplete_subject_preserved": "我" in fragment,
            "pronoun_not_country": "美国" not in pronoun,
            "performance_term": "硬" in term or "指标" in term,
        }
    destination = ROOT / "artifacts" / "offline-check.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("OFFLINE ASR + CONTEXTUAL TRANSLATION OK", flush=True)
    failed = [name for name, passed in report["semantic_checks"].items() if not passed]
    if failed:
        print("KNOWN SEMANTIC FAILURES:", ", ".join(failed), flush=True)


if __name__ == "__main__":
    main()
