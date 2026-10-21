"""Copy installed dependency license notices into a distributable directory."""
from importlib.metadata import distributions
from pathlib import Path
import shutil
import sys

target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("licenses")
for distribution in distributions():
    for item in distribution.files or []:
        if not any(word in str(item).lower() for word in ("license", "copying", "notice")):
            continue
        source = Path(distribution.locate_file(item))
        if source.is_file() and source.suffix.lower() not in (".py", ".pyc", ".pyd"):
            destination = target / distribution.metadata["Name"] / str(item).replace("../", "")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
