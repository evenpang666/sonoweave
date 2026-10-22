import hashlib
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from sonoweave.config import Settings, translation_model_location
from sonoweave.models import DownloadCancelled, ModelFile, ensure_file, ensure_models


PAYLOAD = b"test-model-payload"
SPEC = ModelFile("test/repo", "pinned-commit", "model.gguf", len(PAYLOAD), hashlib.sha256(PAYLOAD).hexdigest())


class Response:
    def __init__(self, chunks, status=200, headers=None):
        self.chunks, self.status_code, self.headers = chunks, status, headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        for chunk in self.chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk


def test_settings_default_4b_and_switch_preserves_language(tmp_path):
    path = tmp_path / "settings.json"
    settings = Settings(path)
    assert settings.model == "4b"
    settings.save_target("ja")
    settings.save_model("2b")
    settings.save_target("fr")
    assert (Settings(path).model, Settings(path).target) == ("2b", "fr")
    with pytest.raises(ValueError):
        settings.save_model("unknown")


def test_valid_existing_weight_never_connects(tmp_path, monkeypatch):
    path = tmp_path / SPEC.filename
    path.write_bytes(PAYLOAD)
    download = Mock(side_effect=AssertionError("network used"))
    monkeypatch.setattr("sonoweave.models.requests.get", download)
    assert ensure_file(path, SPEC) == path
    download.assert_not_called()


def test_resume_download_validates_before_publishing(tmp_path, monkeypatch):
    path = tmp_path / SPEC.filename
    partial = path.with_suffix(".gguf.part")
    partial.write_bytes(PAYLOAD[:5])
    download = Mock(return_value=Response([PAYLOAD[5:]], 206, {"Content-Range": f"bytes 5-{len(PAYLOAD)-1}/{len(PAYLOAD)}"}))
    monkeypatch.setattr("sonoweave.models.requests.get", download)
    ensure_file(path, SPEC)
    assert path.read_bytes() == PAYLOAD and not partial.exists()
    assert download.call_args.kwargs["headers"]["Range"] == "bytes=5-"
    assert "/resolve/pinned-commit/" in download.call_args.args[0]


def test_server_ignoring_range_restarts_instead_of_appending(tmp_path, monkeypatch):
    path = tmp_path / SPEC.filename
    path.with_suffix(".gguf.part").write_bytes(b"old")
    monkeypatch.setattr("sonoweave.models.requests.get", lambda *a, **k: Response([PAYLOAD]))
    ensure_file(path, SPEC)
    assert path.read_bytes() == PAYLOAD


def test_corrupt_same_size_weight_is_replaced_only_after_valid_download(tmp_path, monkeypatch):
    path = tmp_path / SPEC.filename
    path.write_bytes(b"x" * len(PAYLOAD))
    monkeypatch.setattr("sonoweave.models.requests.get", lambda *a, **k: Response([b"y" * len(PAYLOAD)]))
    with pytest.raises(RuntimeError, match="校验失败"):
        ensure_file(path, SPEC)
    assert path.read_bytes() == b"x" * len(PAYLOAD)
    assert not path.with_suffix(".gguf.part").exists()


def test_dropped_connection_resumes_and_cancellation_keeps_partial(tmp_path, monkeypatch):
    path = tmp_path / SPEC.filename
    responses = iter((Response([PAYLOAD[:4], requests.ConnectionError()]),
                      Response([PAYLOAD[4:]], 206, {"Content-Range": f"bytes 4-{len(PAYLOAD)-1}/{len(PAYLOAD)}"})))
    monkeypatch.setattr("sonoweave.models.requests.get", lambda *a, **k: next(responses))
    ensure_file(path, SPEC)
    assert path.read_bytes() == PAYLOAD
    path.unlink()
    partial = path.with_suffix(".gguf.part")
    partial.write_bytes(PAYLOAD[:4])
    with pytest.raises(DownloadCancelled):
        ensure_file(path, SPEC, cancelled=lambda: True)
    assert partial.read_bytes() == PAYLOAD[:4]


def test_wrong_range_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / SPEC.filename
    path.with_suffix(".gguf.part").write_bytes(PAYLOAD[:4])
    monkeypatch.setattr("sonoweave.models.requests.get", lambda *a, **k: Response([PAYLOAD], 206, {"Content-Range": "bytes 0-3/4"}))
    with pytest.raises(RuntimeError, match="断点响应无效"):
        ensure_file(path, SPEC)
    assert not path.exists()


def test_preparation_downloads_only_selected_translation_model(tmp_path, monkeypatch):
    monkeypatch.setenv("SONOWEAVE_MODELS_DIR", str(tmp_path))
    monkeypatch.delenv("SONOWEAVE_MODEL", raising=False)
    calls = []
    monkeypatch.setattr("sonoweave.models.ensure_file", lambda path, spec, *args: calls.append((path, spec)))
    result = ensure_models("2b")
    assert len(calls) == 5
    assert calls[-1][1].filename == "Qwen3.5-2B-Q4_K_M.gguf"
    assert result == translation_model_location("2b")
    assert all("4B" not in spec.filename for _, spec in calls)


def test_frozen_download_directory_is_writable_user_data(tmp_path, monkeypatch):
    monkeypatch.setattr("sonoweave.config.sys.frozen", True, raising=False)
    monkeypatch.setattr("sonoweave.config.sys.executable", str(tmp_path / "ProgramFiles" / "SonoWeave.exe"))
    monkeypatch.setenv("SONOWEAVE_HOME", str(tmp_path / "userdata"))
    monkeypatch.delenv("SONOWEAVE_MODELS_DIR", raising=False)
    assert translation_model_location("2b") == tmp_path / "userdata/models/translation/Qwen3.5-2B-Q4_K_M.gguf"
