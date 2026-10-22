"""Temporary files owned by one application session, separate from model storage."""
import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory

log = logging.getLogger(__name__)


class SessionCache:
    def __init__(self, root):
        self.root = (Path(root) / "cache").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.directory = TemporaryDirectory(prefix="session-", dir=self.root)
        self.path = Path(self.directory.name).resolve()
        self.hf_home = str(self.path / "huggingface")
        os.environ.setdefault("HF_HOME", self.hf_home)

    def close(self):
        # Never remove a model directory, another session or a user HF_HOME.
        if self.path.parent != self.root or not self.path.name.startswith("session-"):
            raise ValueError("Invalid session cache path")
        if os.environ.get("HF_HOME") == self.hf_home:
            del os.environ["HF_HOME"]
        try:
            self.directory.cleanup()
            try:
                self.root.rmdir()  # Only remove the parent if it is now empty.
            except OSError:
                pass
        except OSError:
            log.warning("Could not remove session temporary files", exc_info=True)
