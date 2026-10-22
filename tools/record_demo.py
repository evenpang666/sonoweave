"""Capture eight seconds of the real caption widget; no audio is saved.

Encode the PNG frames into a GIF separately, using the bundled Pillow runtime.
"""
import json
import os
from pathlib import Path
import socket
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(SONOWEAVE_HOME=str(ROOT / ".runtime" / "demo"),
                  HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")

from sonoweave.desktop import configure_qt_platform
configure_qt_platform()
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication
from sonoweave.config import Settings
from sonoweave.engine import CaptionEngine
from sonoweave.window import CaptionWindow


def no_network(*args, **kwargs):
    raise AssertionError("Recording must use local models")


def main():
    destination = ROOT / ".cache" / "readme-demo"
    destination.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    window = CaptionWindow(Settings(destination / "settings.json"))
    window.show()
    engine = CaptionEngine("zh-CN", speaker_colors=False)
    frames, captions = [], []
    recording_at = None
    finished = False

    def capture():
        if recording_at is None:
            return
        elapsed = time.monotonic() - recording_at
        if elapsed >= 8:
            finish()
            return
        path = destination / f"frame-{len(frames):04d}.png"
        if not window.grab().save(str(path)):
            raise RuntimeError("Could not save demo frame")
        frames.append(dict(path=path.name, at=round(elapsed, 4)))

    def finish():
        nonlocal finished
        if finished:
            return
        finished = True
        timer.stop()
        engine.stop()
        metadata = dict(duration_seconds=8, fps_requested=10, real_system_audio=True,
                        network_blocked=True, audio_saved=False, speaker_colors=False,
                        frames=frames, captions=captions)
        (destination / "recording.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        window.close()
        app.quit()

    def caption(update):
        nonlocal recording_at
        window.set_caption(update.text, update.final, update.runs)
        if recording_at is None:
            recording_at = time.monotonic()
            capture()
            timer.start(100)
        captions.append(dict(at=round(time.monotonic() - recording_at, 4),
                             display_text=window.caption_label.text(), final=update.final))

    timer = QTimer()
    timer.setTimerType(Qt.TimerType.PreciseTimer)
    timer.timeout.connect(capture)
    health = QTimer()
    health.timeout.connect(engine.check_health)
    health.start(1000)
    engine.status.connect(window.set_status)
    engine.translation_status.connect(lambda text, error, revision: window.set_translation_status(text, error))
    engine.caption.connect(caption)
    QTimer.singleShot(45000, finish)
    with patch.object(socket.socket, "connect", no_network), patch.object(socket, "create_connection", no_network):
        engine.start()
        app.exec()
        engine.stop()
        engine.wait_stopped()
    if len(frames) < 60 or len({row["display_text"] for row in captions}) < 2:
        raise RuntimeError("Not enough real subtitle changes for a live demo")
    print(json.dumps(dict(frames=len(frames), caption_updates=len(captions), directory=str(destination))))


if __name__ == "__main__":
    main()
