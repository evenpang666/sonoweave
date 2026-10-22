from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from sonoweave.diagnostics import resource_report


def test_device_report_closes_models_if_translation_fails(monkeypatch):
    recognizer = Mock(model=SimpleNamespace(device="cpu", compute_type="int8"))
    translator = Mock()
    translator.translate.side_effect = RuntimeError("test failure")
    monkeypatch.setattr("sonoweave.diagnostics.load_recognizer", lambda: recognizer)
    monkeypatch.setattr("sonoweave.diagnostics.Translator", lambda: translator)
    with pytest.raises(RuntimeError, match="test failure"):
        resource_report()
    recognizer.close.assert_called_once()
    translator.close.assert_called_once()
