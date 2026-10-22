"""Compare subtitle publication cadence on identical previously observed translations."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from sonoweave.engine import CaptionEngine
from sonoweave.streaming import SourceChunk, TextJob
from tools.caption_metrics import caption_metrics


def replay(report, interval):
    engine = CaptionEngine("zh-CN")
    engine.caption_rewrite_interval = interval
    rows, now = [], [0]

    def caption(value):
        row = asdict(value)
        row.update(at=now[0], translation_snapshot=engine.translation_cache.snapshot(),
                   visible_change=not rows or value.text != rows[-1]["text"])
        rows.append(row)

    engine.caption.connect(caption)
    next_health = 1.0
    with patch("sonoweave.engine.time.monotonic", lambda: now[0]):
        for index, row in enumerate(report["updates"]):
            while next_health <= row["at"]:
                now[0] = next_health
                engine.check_health(next_health)
                next_health += 1.0
            now[0] = row["at"]
            pairs = row["translation_snapshot"]
            parts = tuple(SourceChunk(key, f"Source {key}", "en", index + 1,
                                     row["final"], now[0] - 1, now[0]) for key, _ in pairs)
            for part in parts:
                engine.source_cache.update(part)
            job = TextJob("", "en", "zh-CN", 0, row["utterance_id"], index + 1,
                          row["final"], (), now[0] - 1, now[0], parts)
            engine._publish(job, parts, tuple(text for _, text in pairs), complete=True)
        # A final correction must flush the newest cached text even inside the interval.
        now[0] += .001
        from dataclasses import replace
        engine._publish(replace(job, final=True), parts, tuple(text for _, text in pairs), complete=True)
    return dict(interval=interval, metrics=caption_metrics(rows), final_cache=engine.translation_cache.snapshot(),
                final_display=rows[-1]["text"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    app = QApplication([])
    source = json.loads(args.report.read_text(encoding="utf-8"))
    before, after = replay(source, 0), replay(source, 1.2)
    assert before["final_cache"] == after["final_cache"]
    assert before["final_display"] == after["final_display"]
    result = dict(input=str(args.report), identical_translation_input=True,
                  final_text_identical=True, before=before, after=after)
    destination = args.report.parent / "publication-replay.json"
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    for value in (before, after):
        print(json.dumps(dict(interval=value["interval"], **{k: v for k, v in value["metrics"].items()
                                                            if k != "edits"})))


if __name__ == "__main__":
    main()
