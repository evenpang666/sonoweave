"""Pace a known local waveform through the real streaming ASR/translation engine.

No sound is played. Model startup is reported separately from live latency.
"""
from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import socket
import statistics
import sys
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", SONOWEAVE_HOME=str(ROOT / ".runtime"))
if "--cpu" in sys.argv:
    os.environ.update(SONOWEAVE_ASR_DEVICE="cpu", SONOWEAVE_TRANSLATE_DEVICE="cpu")

import numpy as np
from PySide6.QtCore import QCoreApplication
from faster_whisper.audio import decode_audio
import sonoweave.engine as implementation
from sonoweave.audio import FRAME_SAMPLES, SAMPLE_RATE, StreamingSegmenter
from sonoweave import __version__
from sonoweave.config import model_location, translation_model_location
from sonoweave.recognition import load_recognizer
from sonoweave.translation import Translator


def no_network(*args, **kwargs):
    raise AssertionError("Runtime attempted a network connection")


def main():
    app = QCoreApplication([])
    report = {"version": __version__, "asr_model": Path(model_location()).name,
              "translation_model": translation_model_location().name,
              "real_system_audio": False, "network_blocked": True,
              "input": "jfk.flac, paced at original speed; no speaker playback", "updates": []}
    with patch.object(socket.socket, "connect", no_network), patch.object(socket, "create_connection", no_network):
        load_start = time.monotonic()
        model = load_recognizer()
        translator = Translator()
        # The engine performs warmup and only reports ready afterwards.
        report["asr_device"] = model.model.device
        report["translation_device"] = translator.device
        waveform = decode_audio(str(ROOT / ".cache" / "jfk.flac"), sampling_rate=SAMPLE_RATE)
        waveform = np.concatenate((waveform, np.zeros(SAMPLE_RATE // 2, dtype=np.float32)))
        ready, done = threading.Event(), threading.Event()
        final_window = []

        def capture(stop, on_audio, on_status):
            on_status("ready", False)
            if not ready.wait(60):
                return
            segmenter = StreamingSegmenter()
            start = time.monotonic()
            report["stream_started"] = start
            for offset in range(0, waveform.size, FRAME_SAMPLES):
                if stop.is_set():
                    return
                deadline = start + (offset + FRAME_SAMPLES) / SAMPLE_RATE
                stop.wait(max(0, deadline - time.monotonic()))
                window = segmenter.feed(waveform[offset:offset + FRAME_SAMPLES])
                if window is not None:
                    if window.final:
                        final_window[:] = [window.utterance_id, window.sequence]
                    on_audio(window)
            done.set()

        engine = implementation.CaptionEngine("zh-CN")

        def status(text, error):
            if error:
                raise RuntimeError(text)
            if "正在聆听" in text and not ready.is_set():
                report["startup_seconds"] = round(time.monotonic() - load_start, 3)
                ready.set()

        def caption(update):
            row = asdict(update)
            row["since_stream_start_seconds"] = round(time.monotonic() - report["stream_started"], 3)
            report["updates"].append(row)
            print(json.dumps(row, ensure_ascii=True), flush=True)

        engine.status.connect(status)
        engine.caption.connect(caption)
        with patch.object(implementation, "load_recognizer", lambda: model), \
             patch.object(implementation, "Translator", lambda: translator), \
             patch.object(implementation, "capture_loopback", capture):
            engine.start()
            deadline = time.monotonic() + 100
            while time.monotonic() < deadline:
                app.processEvents()
                updates = report["updates"]
                if done.is_set() and updates and final_window:
                    last = updates[-1]
                    if last["final"] and [last["utterance_id"], last["sequence"]] == final_window:
                        break
                time.sleep(0.01)
            else:
                raise TimeoutError("Streaming engine failed to finalize the sample")
            engine.stop()
            for thread in engine.threads:
                thread.join(3)
        assert len(report["updates"]) >= 3
        assert any(not row["final"] for row in report["updates"])
        assert report["updates"][-1]["final"]
        report["first_caption_seconds"] = report["updates"][0]["since_stream_start_seconds"]
        report["median_processing_lag_seconds"] = round(statistics.median(row["lag_seconds"] for row in report["updates"]), 3)
        report.pop("stream_started", None)
    name = "streaming-cpu.json" if "--cpu" in sys.argv else "streaming-gpu.json"
    output = Path(sys.argv[sys.argv.index("--output") + 1]) if "--output" in sys.argv else ROOT / "artifacts" / name
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "updates"}), flush=True)


if __name__ == "__main__":
    main()
