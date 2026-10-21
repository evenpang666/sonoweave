"""Exercise the real default-output loopback and both in-memory text caches.

No audio is recorded to disk. Optional text diagnostics stay in ignored .cache.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import socket
import statistics
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(QT_QPA_PLATFORM="offscreen", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                  SONOWEAVE_HOME=str(ROOT / ".runtime"))

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication
from sonoweave.config import Settings
from sonoweave.engine import CaptionEngine
from sonoweave.window import CaptionWindow


def no_network(*args, **kwargs):
    raise AssertionError("Runtime attempted a network connection")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=90)
    parser.add_argument("--label", default="live")
    args = parser.parse_args()
    app = QApplication([])
    if not QFontDatabase.families():
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in ("segoeui.ttf", "msyh.ttc", "msyhbd.ttc"):
            QFontDatabase.addApplicationFont(str(fonts / name))
    window = CaptionWindow(Settings(ROOT / ".cache" / "live-test-settings.json"))
    window.show()
    engine = CaptionEngine("zh-CN")
    started = time.monotonic()
    ready_at = None
    updates = []
    statuses = []
    translation_errors = []
    history = []
    report = {"real_system_audio": True, "network_blocked": True, "audio_saved": False}

    def status(text, error):
        nonlocal ready_at
        window.set_status(text, error)
        if not statuses or statuses[-1]["text"] != text:
            statuses.append(dict(text=text, error=error, at=round(time.monotonic() - started, 3)))
            print("STATUS", json.dumps(statuses[-1], ensure_ascii=True), flush=True)
        if "正在聆听" in text and not error and ready_at is None:
            ready_at = time.monotonic()

    def caption(update):
        window.set_caption(update.text, update.final)
        row = asdict(update)
        row["at"] = round(time.monotonic() - started, 3)
        with engine.lock:
            row["source_snapshot"] = [asdict(chunk) for chunk in engine.source_cache.snapshot()[-3:]]
        updates.append(row)
        if len(updates) % 10 == 0:
            print("CACHE", json.dumps({key: row[key] for key in (
                "source_chars", "translation_chars", "translated_segments", "lag_seconds", "at")}), flush=True)

    engine.status.connect(status)
    engine.caption.connect(caption)

    def translation_status(text, error, revision):
        window.set_translation_status(text, error)
        if error:
            translation_errors.append(dict(text=text, revision=revision,
                                           at=round(time.monotonic() - started, 3)))

    engine.translation_status.connect(translation_status)
    with patch.object(socket.socket, "connect", no_network), patch.object(socket, "create_connection", no_network):
        engine.start()
        deadline = started + args.seconds + 60
        last_sample = started
        try:
            while time.monotonic() < deadline:
                app.processEvents()
                now = time.monotonic()
                if now - last_sample >= 1:
                    with engine.lock:
                        history.append(dict(at=round(now - started, 3), source_chars=engine.source_cache.char_count,
                                            source_segments=len(engine.source_cache.chunks),
                                            source_version=engine.source_cache.version, update_count=len(updates),
                                            translated_chars=engine.translation_cache.char_count,
                                            translated_segments=len(engine.translation_cache.chunks)))
                    last_sample = now
                if ready_at is not None and now - ready_at >= args.seconds:
                    break
                time.sleep(0.01)
            else:
                raise TimeoutError("Models did not become ready")
        finally:
            engine.stop()
            for thread in engine.threads:
                thread.join(5)
        app.processEvents()
    with engine.lock:
        sources = [asdict(chunk) for chunk in engine.source_cache.snapshot()]
        translations = engine.translation_cache.snapshot()
    destination = ROOT / ".cache" / f"{args.label}-test"
    destination.mkdir(parents=True, exist_ok=True)
    window.grab().save(str(destination / "captions.png"))
    window.hide()
    steady = [row["lag_seconds"] for row in updates if ready_at is not None and row["at"] >= ready_at - started + 5]
    steady_updates = [row for row in updates if ready_at is not None and row["at"] >= ready_at - started + 5]
    gaps = [later["at"] - earlier["at"] for earlier, later in zip(steady_updates, steady_updates[1:])]
    max_stall = 0
    waiting_since = None
    reference_chars = 0
    previous = None
    for sample in history:
        if ready_at is None or sample["at"] < ready_at - started + 5:
            continue
        if previous is None or sample["update_count"] != previous["update_count"]:
            waiting_since = None
            reference_chars = sample["source_chars"]
        elif sample["source_version"] != previous["source_version"] and waiting_since is None:
            waiting_since = sample["at"]
        if waiting_since is not None and sample["source_chars"] - reference_chars >= 80:
            max_stall = max(max_stall, sample["at"] - waiting_since)
        previous = sample
    report.update(duration_seconds=round(time.monotonic() - started, 2),
                  ready_seconds=round(ready_at - started, 2) if ready_at else None,
                  update_count=len(updates), source_segments=len(sources), translated_segments=len(translations),
                  source_chars=sum(len(row["text"]) for row in sources),
                  translated_chars=sum(len(text) for _, text in translations),
                  median_steady_processing_lag=round(statistics.median(steady), 3) if steady else None,
                  p95_steady_processing_lag=round(sorted(steady)[min(len(steady) - 1, int(len(steady) * 0.95))], 3) if steady else None,
                  max_steady_processing_lag=round(max(steady), 3) if steady else None,
                  max_steady_caption_gap_seconds=round(max(gaps), 3) if gaps else None,
                  translation_error_count=len(translation_errors),
                  max_stall_with_growing_source_seconds=round(max_stall, 3),
                  statuses=statuses, translation_errors=translation_errors, history=history, updates=updates,
                  source_cache=sources, translation_cache=translations)
    (destination / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {key: value for key, value in report.items() if key not in (
        "statuses", "translation_errors", "history", "updates", "source_cache", "translation_cache")}
    print("RESULT", json.dumps(summary), flush=True)
    print("PRIVATE_DIAGNOSTICS", destination, flush=True)
    assert report["source_segments"] >= 3, "Not enough speech received to test cross-sentence caching"
    assert report["translated_segments"] >= 3 and len(updates) >= 5, "Translation cache did not accumulate"
    assert max_stall < 10, "Source kept growing but translated subtitles stalled"


if __name__ == "__main__":
    main()
