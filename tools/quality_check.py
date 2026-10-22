"""Repeatable, public translation probes; optional offline synthetic ASR comparison."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import socket
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")

from sonoweave.recognition import load_recognizer
from sonoweave.translation import Translator
from sonoweave.config import translation_model_location

CASES = [
    ("gonna color outside the lines to get the job done", ("He wanted a promotion, so he had to find creative ways to grow the economy.",), ("常规", "规矩", "界限", "规则", "框架", "界", "规范")),
    ("They really knocked it out of the park economically.", (), ("经济",)),
    ("Deng Xiaoping was a pragmatist, not a hardliner.", (), ("邓小平", "务实")),
    ("He stood beside the bank.", ("They were fishing by the river.",), ("河",)),
    ("He stood beside the bank.", ("He wanted to withdraw money from his account.",), ("银行",)),
    ("The temperature is 37.5 degrees, not 35 degrees.", (), ("37.5", "35")),
    ("The company did not approve the plan.", (), ("没", "未", "不")),
    ("The tax revenue rose by 12 percent, while the budget remained unchanged.", (), ("12", "预算")),
    ("But I", (), ("但",)),
    ("not going to be us.", ("Communist countries were falling in Europe.", "He said that is"), ("我们",)),
    ("Economic development was a hard target, something you can easily measure with a number.", ("Officials were assessed on a scorecard with performance indicators.",), ("硬", "指标")),
    ("So the committee changed the incentive structure, and this will shape the next thirty to forty years.", (), ("激励", "三十", "四十")),
]


def no_network(*args, **kwargs):
    raise AssertionError("Runtime attempted a network connection")


def word_error_rate(reference, hypothesis):
    def words(text):
        return re.findall(r"[a-z0-9]+", text.lower())
    expected, actual = words(reference), words(hypothesis)
    previous = list(range(len(actual) + 1))
    for i, word in enumerate(expected, 1):
        current = [i]
        for j, other in enumerate(actual, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (word != other)))
        previous = current
    return previous[-1] / max(1, len(expected))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "quality-check-v04.json")
    parser.add_argument("--compare-asr", action="store_true")
    args = parser.parse_args()
    if args.model:
        os.environ["SONOWEAVE_TRANSLATE_MODEL"] = str(args.model.resolve())
    report = dict(network_blocked=True, translations=[], asr=[])
    with patch.object(socket.socket, "connect", no_network), patch.object(socket, "create_connection", no_network):
        translator = Translator()
        warmup_started = time.monotonic()
        translator.warmup()
        report.update(model=translation_model_location().name,
                      translation_device=translator.device,
                      warmup_seconds=round(time.monotonic() - warmup_started, 3))
        try:
            for source, context, probes in CASES:
                started = time.monotonic()
                try:
                    result = translator.translate(source, "zh-CN", "en", context=context)
                    row = dict(source=source, context=context, translation=result,
                               probe_matches=[probe for probe in probes if probe in result])
                except Exception as exc:
                    row = dict(source=source, context=context, error=type(exc).__name__)
                row["seconds"] = round(time.monotonic() - started, 3)
                report["translations"].append(row)
                print(json.dumps(row, ensure_ascii=True), flush=True)
        finally:
            translator.close()
        if args.compare_asr:
            sample = ROOT / ".cache" / "asr-synthetic.wav"
            assert sample.is_file(), "Run powershell -File tools/make_asr_fixture.ps1 first"
            reference = (ROOT / ".cache" / "asr-synthetic-reference.txt").read_text(encoding="utf-8-sig")
            # This fixture spells numbers out for speech synthesis. Accept the
            # equivalent numeric spelling in Whisper's transcript when scoring.
            normalized_reference = reference.replace("thirty seven point five", "37.5").replace("thirty five", "35")
            for name in ("base", "small"):
                os.environ["SONOWEAVE_MODEL"] = str(ROOT / "models" / name)
                recognizer = load_recognizer()
                start = time.monotonic()
                segments, _ = recognizer.transcribe(str(sample), language="en", beam_size=3, condition_on_previous_text=False)
                recognized = " ".join(segment.text.strip() for segment in segments)
                row = dict(model=name, reference=reference.strip(), recognized=recognized,
                           normalized_reference=normalized_reference.strip(),
                           word_error_rate=round(word_error_rate(normalized_reference, recognized), 4),
                           inference_seconds=round(time.monotonic() - start, 3))
                report["asr"].append(row)
                print(json.dumps(row, ensure_ascii=True), flush=True)
                # Release CTranslate2 weights before loading the other candidate.
                recognizer.model.unload_model()
                del recognizer
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
