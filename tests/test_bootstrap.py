import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from tools.bootstrap import Bootstrap, setup_lock


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "project with spaces"
    (root / "tools").mkdir(parents=True)
    for name in ("requirements-base.txt", "requirements.txt", "requirements-cpu.txt", "tools/bootstrap.py"):
        (root / name).write_text(name, encoding="utf-8")
    return Bootstrap(root, uv=tmp_path / "uv with spaces.exe", platform="win32")


def mark_ready(setup, backend="cpu"):
    setup.python.parent.mkdir(parents=True, exist_ok=True)
    setup.python.touch()
    setup.stamp.write_text(json.dumps({"fingerprint": setup.fingerprint(), "backend": backend}), encoding="utf-8")


def test_readiness_rejects_missing_corrupt_changed_and_broken_dependencies(setup, monkeypatch):
    monkeypatch.setattr(setup, "probe", lambda: True)
    assert not setup.ready()
    mark_ready(setup)
    assert setup.ready() and setup.ready("cpu") and not setup.ready("vulkan")
    (setup.root / "requirements-base.txt").write_text("changed", encoding="utf-8")
    assert not setup.ready()
    mark_ready(setup)
    monkeypatch.setattr(setup, "probe", lambda: False)
    assert not setup.ready()
    setup.stamp.write_text("invalid", encoding="utf-8")
    assert not setup.ready()


def test_healthy_environment_needs_no_uv_or_network(setup, monkeypatch):
    mark_ready(setup)
    monkeypatch.setattr(setup, "probe", lambda: True)
    monkeypatch.setattr(setup, "command", lambda *args: pytest.fail("no dependency commands on healthy startup"))
    monkeypatch.setattr(setup, "ensure_system_dependencies", lambda *args: pytest.fail("no system changes"))
    setup.install("auto")
    assert setup.state()["backend"] == "cpu"


def test_install_scopes_wheel_index_and_writes_marker_only_after_probe(setup, monkeypatch):
    commands = []
    monkeypatch.setattr(setup, "command", lambda *args: commands.append(args))
    monkeypatch.setattr(setup, "ensure_system_dependencies", lambda backend: None)

    def prepare():
        setup.python.parent.mkdir(parents=True)
        setup.python.touch()

    monkeypatch.setattr(setup, "prepare_environment", prepare)
    monkeypatch.setattr(setup, "probe", lambda: len(commands) == 2)
    setup.install("cpu")
    assert commands[0] == ("pip", "install", "--python", setup.python, "-r", "requirements-base.txt", "--reinstall")
    assert "https://abetlen.github.io/llama-cpp-python/whl/cpu" in commands[1]
    assert "llama-cpp-python==0.3.36" in commands[1]
    assert setup.ready() and setup.state()["backend"] == "cpu"
    assert not (setup.root / "models").exists()


def test_failed_install_invalidates_marker_and_retains_cpu_selection(setup, monkeypatch):
    mark_ready(setup, "vulkan")
    # Force repair while keeping a previous valid backend selection on disk.
    monkeypatch.setattr(setup, "probe", lambda: False)
    monkeypatch.setattr(setup, "prepare_environment", lambda: None)
    monkeypatch.setattr(setup, "ensure_system_dependencies", lambda backend: None)
    monkeypatch.setattr(setup, "command", lambda *args: (_ for _ in ()).throw(subprocess.CalledProcessError(7, args)))
    with pytest.raises(subprocess.CalledProcessError):
        setup.install("cpu")
    assert not setup.stamp.exists() and not setup.ready()
    assert setup.backend_path.read_text(encoding="utf-8") == "cpu"
    selections = []
    monkeypatch.setattr(setup, "ensure_system_dependencies", lambda backend: selections.append(backend))
    with pytest.raises(subprocess.CalledProcessError):
        setup.install("auto")
    assert selections == ["cpu"]


def test_old_environment_is_archived_without_touching_models(setup, monkeypatch):
    setup.python.parent.mkdir(parents=True)
    setup.python.write_text("old interpreter", encoding="utf-8")
    model = setup.root / "models/translation/model.gguf"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"keep weights")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1))
    commands = []
    monkeypatch.setattr(setup, "command", lambda *args: commands.append(args))
    setup.prepare_environment()
    backups = list((setup.root / ".cache/previous-environments").iterdir())
    assert len(backups) == 1
    assert (backups[0] / "Scripts/python.exe").read_text(encoding="utf-8") == "old interpreter"
    assert model.read_bytes() == b"keep weights"
    assert commands[0][0:2] == ("venv", "--python")
    assert commands[0][-1] == setup.environment


def test_os_lock_releases_and_rejects_concurrent_setup(tmp_path):
    path = tmp_path / "setup.lock"
    with setup_lock(path):
        with pytest.raises(RuntimeError, match="Another installation"):
            with setup_lock(path, timeout=0):
                pytest.fail("overlapping setup must wait")
    with setup_lock(path, timeout=0):
        pass


def test_linux_installs_only_missing_client_libraries_without_audio_service_changes(tmp_path, monkeypatch):
    setup = Bootstrap(tmp_path, platform="linux")
    monkeypatch.delenv("SONOWEAVE_SKIP_SYSTEM_DEPS", raising=False)
    monkeypatch.setattr("tools.bootstrap.shutil.which", lambda command: f"/bin/{command}")
    monkeypatch.setattr("tools.bootstrap.os.geteuid", lambda: 1000, raising=False)
    commands = []

    def run(args, **kwargs):
        commands.append(args)
        missing = args[0] == "dpkg-query" and args[-1] in ("fonts-noto-cjk", "libvulkan1")
        return SimpleNamespace(returncode=1 if missing else 0, stdout="" if missing else "install ok installed")

    monkeypatch.setattr(subprocess, "run", run)
    setup.ensure_system_dependencies("vulkan")
    assert commands[-2] == ["sudo", "apt-get", "update"]
    assert commands[-1] == ["sudo", "apt-get", "install", "-y", "fonts-noto-cjk", "libvulkan1"]
    assert not any("pipewire-pulse" in command or "pulseaudio" in command for command in commands)


def test_probe_failure_never_marks_environment_ready(setup, monkeypatch):
    monkeypatch.setattr(setup, "probe", lambda: False)
    monkeypatch.setattr(setup, "prepare_environment", lambda: None)
    monkeypatch.setattr(setup, "ensure_system_dependencies", lambda backend: None)
    monkeypatch.setattr(setup, "command", lambda *args: None)
    with pytest.raises(RuntimeError, match="Dependency verification failed"):
        setup.install("cpu")
    assert not setup.stamp.exists()


def test_upgrade_preserves_legacy_linux_cpu_choice(tmp_path, monkeypatch):
    setup = Bootstrap(tmp_path, uv="uv", platform="linux")
    setup.environment.mkdir()
    (setup.environment / ".sonoweave-backend").write_text("cpu\n", encoding="utf-8")
    monkeypatch.setattr(setup, "ready", lambda *args: False)
    selections = []

    def stop_after_selection(backend):
        selections.append(backend)
        raise RuntimeError("selection checked")

    monkeypatch.setattr(setup, "ensure_system_dependencies", stop_after_selection)
    with pytest.raises(RuntimeError, match="selection checked"):
        setup.install("auto")
    assert selections == ["cpu"]
