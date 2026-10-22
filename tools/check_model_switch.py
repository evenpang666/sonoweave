"""Exercise real startup, provisioning and 4B->2B reload without capturing any audio."""
from pathlib import Path
import json
import os
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
folder = ROOT / ".cache" / f"model-switch-{time.time_ns()}"
folder.mkdir(parents=True)
os.environ.update(QT_QPA_PLATFORM="offscreen", SONOWEAVE_HOME=str(folder),
                  SONOWEAVE_MODELS_DIR=str(ROOT / "models"))

from PySide6 import QtWidgets
from PySide6.QtCore import QTimer
import sonoweave.engine as implementation
from sonoweave.window import CaptionWindow
from sonoweave import __version__

report = {"version": __version__, "system_audio_captured": False, "model_sequence": [], "errors": []}


def no_capture(stop, on_audio, on_status):
    on_status("ready", False)
    stop.wait()


implementation.capture_loopback = no_capture


class TestApplication(QtWidgets.QApplication):
    def __init__(self, *args):
        super().__init__(*args)
        self.started = time.monotonic()
        self.stage = "4b"
        QTimer.singleShot(100, self.inspect)

    def inspect(self):
        try:
            window = next(widget for widget in self.allWidgets() if isinstance(widget, CaptionWindow))
            if window._base_status[1]:
                raise RuntimeError(window._base_status[0])
            if "正在聆听系统声音" in window.status_label.text():
                report["model_sequence"].append(window.settings.model)
                if self.stage == "4b":
                    assert window.settings.model == "4b"
                    self.stage = "2b"
                    window.choose_model("2b")
                else:
                    assert window.settings.model == "2b"
                    report["settings_persisted"] = json.loads((folder / "settings.json").read_text(encoding="utf-8"))["model"] == "2b"
                    self.quit()
                    return
            if time.monotonic() - self.started > 100:
                raise TimeoutError("Model switching did not complete")
        except Exception as exc:
            report["errors"].append(str(exc))
            self.quit()
            return
        QTimer.singleShot(100, self.inspect)


QtWidgets.QApplication = TestApplication
import main
sys.argv = ["main.py"]
main.main()
log = (folder / "app.log").read_text(encoding="utf-8")
report["both_real_models_loaded"] = all(f"Offline translation ready: Qwen3.5-{size}B-Q4_K_M.gguf" in log for size in (4, 2))
report["passed"] = (not report["errors"] and report["model_sequence"] == ["4b", "2b"]
                    and report["settings_persisted"] and report["both_real_models_loaded"])
destination = ROOT / "artifacts" / "model-switch-v05.json"
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=True), flush=True)
raise SystemExit(0 if report["passed"] else 1)
