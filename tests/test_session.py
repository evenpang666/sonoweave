from pathlib import Path
import os
import subprocess
import sys

from sonoweave.session import SessionCache


def test_exit_removes_only_owned_temporary_cache(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_HOME", raising=False)
    model = tmp_path / "models" / "model.bin"
    model.parent.mkdir()
    model.write_bytes(b"keep-model")
    settings = tmp_path / "settings.json"
    settings.write_text('{"font_size":16}')
    session = SessionCache(tmp_path)
    second_session = tmp_path / "cache" / "session-other"
    second_session.mkdir()
    (second_session / "keep").write_text("another running session")
    transient = Path(session.hf_home) / "temporary-metadata"
    transient.parent.mkdir()
    transient.write_text("discard")
    session.close()
    session.close()
    assert not session.path.exists()
    assert model.read_bytes() == b"keep-model"
    assert settings.read_text() == '{"font_size":16}'
    assert (second_session / "keep").is_file()


def test_user_cache_location_is_not_deleted_or_reconfigured(tmp_path, monkeypatch):
    external = tmp_path / "user-huggingface"
    external.mkdir()
    (external / "model.bin").write_bytes(b"user-model")
    monkeypatch.setenv("HF_HOME", str(external))
    session = SessionCache(tmp_path)
    session.close()
    assert os.environ["HF_HOME"] == str(external)
    assert (external / "model.bin").read_bytes() == b"user-model"
    assert not (tmp_path / "cache").exists()


def test_normal_application_exit_cleans_active_engine_and_temporary_files(tmp_path):
    script = '''
import os
from pathlib import Path
from unittest.mock import patch
import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
import main
from sonoweave.audio import AudioWindow
from sonoweave.engine import CaptionEngine
from sonoweave.streaming import SourceChunk

engines = []
class TestEngine(CaptionEngine):
    def start(self):
        engines.append(self)
        self._enqueue_audio(AudioWindow(1, 1, np.zeros(16000), True, 0, 1))
        self.source_cache.update(SourceChunk(1, "Private text", "en", 1, True, 0, 1))
        self.translation_cache.update(self.source_cache.snapshot(), ("Private translation",), {1})
        self.speaker_registry.identify([1, 0], 2)
        transient = Path(os.environ["HF_HOME"]) / "metadata"
        transient.parent.mkdir(parents=True)
        transient.write_text("discard")
        with self.lock:
            self._queue_transcript()
        QTimer.singleShot(0, QApplication.instance().quit)

with patch("sonoweave.models.ensure_models", return_value=Path("fake-model.gguf")), \\
     patch("sonoweave.engine.CaptionEngine", TestEngine):
    assert main.main() == 0
assert len(engines) == 1
engine = engines[0]
assert not engine.source_cache.snapshot()
assert not engine.translation_cache.snapshot()
assert not engine.speaker_registry.prototypes
assert engine.last_job is None
assert not engine.audio_queue.items and not engine.text_queue.items
root = Path(os.environ["SONOWEAVE_HOME"])
assert not (root / "cache").exists()
assert not (root / "app.lock").exists()
print("SESSION EXIT OK")
'''
    env = os.environ.copy()
    env.pop("HF_HOME", None)
    env.update(SONOWEAVE_HOME=str(tmp_path), QT_QPA_PLATFORM="offscreen")
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SESSION EXIT OK" in result.stdout
