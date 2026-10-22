"""Exercise the actual batch entry point without installing or starting models."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows batch launcher")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project with spaces"
    root.mkdir()
    shutil.copy2(ROOT / "Start.bat", root)
    (root / "Install.bat").write_text(
        '@echo off\necho checked>environment-checked\nexit /b 0\n', encoding="ascii")
    # Use venv redirectors so both the source interpreter and fake old bundle
    # run headlessly; no compiler, network, global installation or real UI.
    for folder, name in ((root / ".venv/Scripts", "pythonw.exe"),
                         (root / "dist/SonoWeave", "SonoWeave.exe")):
        folder.mkdir(parents=True)
        shutil.copy2(Path(sys.executable).with_name("pythonw.exe"), folder / name)
        (folder.parent / "pyvenv.cfg").write_text(
            f"home = {sys.base_prefix}\ninclude-system-site-packages = false\n", encoding="utf-8")
    return root


def launch(root, *args):
    return subprocess.run([os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "Start.bat", *args],
                          cwd=root, capture_output=True, text=True, timeout=15)


def wait_result(path):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                pass
        time.sleep(0.05)
    pytest.fail(f"Launcher did not produce {path.name}")


def test_checkout_prefers_source_over_old_bundle_and_preserves_arguments(project):
    (project / "main.py").write_text(
        "import json, pathlib, sys\n"
        "pathlib.Path('source-result.json').write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n",
        encoding="utf-8")
    result = launch(project, "--screenshot", "image with spaces.png")
    assert result.returncode == 0, result.stdout + result.stderr
    assert wait_result(project / "source-result.json") == ["--screenshot", "image with spaces.png"]
    assert (project / "environment-checked").is_file()


def test_bundle_fallback_without_source_skips_environment_setup(project):
    code = "import pathlib; pathlib.Path('bundle-result.json').write_text('true')"
    result = launch(project, "-c", code)
    assert result.returncode == 0, result.stdout + result.stderr
    assert wait_result(project / "bundle-result.json") is True
    assert not (project / "environment-checked").exists()


def test_setup_failure_never_starts_source_or_old_bundle(project):
    (project / "main.py").write_text("raise AssertionError('must not start')", encoding="ascii")
    # Return immediately instead of pausing for input in this noninteractive test.
    (project / "Install.bat").write_text('@echo off\nexit /b 7\n', encoding="ascii")
    result = launch(project)
    assert result.returncode == 1
    assert "Environment setup failed" in result.stdout


def test_missing_source_and_bundle_returns_error(project):
    (project / "dist/SonoWeave/SonoWeave.exe").unlink()
    result = launch(project)
    assert result.returncode == 1
    assert "Cannot find main.py" in result.stdout
    assert not (project / "environment-checked").exists()
