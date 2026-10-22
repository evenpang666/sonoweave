import os
from pathlib import Path

import pytest

from sonoweave import config
from sonoweave.desktop import configure_qt_platform


def test_linux_data_uses_xdg_and_settings_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(config.sys, "platform", "linux")
    monkeypatch.delenv("SONOWEAVE_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "windows"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert config.data_dir() == tmp_path / "xdg/sonoweave"
    settings = config.Settings()
    settings.save_model("2b")
    settings.save_target("ja")
    restored = config.Settings()
    assert (restored.target, restored.model) == ("ja", "2b")


@pytest.mark.parametrize("xdg", ["", "relative/path"])
def test_linux_data_defaults_home_and_rejects_relative_xdg(tmp_path, monkeypatch, xdg):
    monkeypatch.setattr(config.sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("SONOWEAVE_HOME", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", xdg)
    assert config.data_dir() == tmp_path / ".local/share/sonoweave"


def test_linux_data_override_and_models_override(tmp_path, monkeypatch):
    monkeypatch.setattr(config.sys, "platform", "linux")
    monkeypatch.setenv("SONOWEAVE_HOME", str(tmp_path / "portable"))
    monkeypatch.setenv("SONOWEAVE_MODELS_DIR", str(tmp_path / "weights"))
    assert config.data_dir() == tmp_path / "portable"
    assert config.models_dir() == tmp_path / "weights"


def test_windows_data_location_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(config.sys, "platform", "win32")
    monkeypatch.delenv("SONOWEAVE_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "ignored"))
    assert config.data_dir() == tmp_path / "SonoWeave"


@pytest.mark.parametrize("platform,display,wayland,explicit,expected", [
    ("linux", ":0", "wayland-0", None, "xcb"),
    ("linux", ":0", "wayland-0", "wayland", "wayland"),
    ("linux", ":0", "wayland-0", "offscreen", "offscreen"),
    ("linux", "", "wayland-0", None, None),
    ("linux", ":0", "", None, None),
    ("win32", ":0", "wayland-0", None, None),
])
def test_qt_uses_xwayland_only_when_available_and_not_overridden(
        monkeypatch, platform, display, wayland, explicit, expected):
    monkeypatch.setattr("sonoweave.desktop.sys.platform", platform)
    monkeypatch.setenv("DISPLAY", display)
    monkeypatch.setenv("WAYLAND_DISPLAY", wayland)
    if explicit:
        monkeypatch.setenv("QT_QPA_PLATFORM", explicit)
    else:
        monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    configure_qt_platform()
    assert os.environ.get("QT_QPA_PLATFORM") == expected
