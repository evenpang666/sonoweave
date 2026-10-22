#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ $(uname -s) != Linux || $(uname -m) != x86_64 ]]; then
    echo "Build the Linux bundle on Linux x86_64." >&2
    exit 1
fi
python=.venv-linux/bin/python
bash Install.sh --ensure
"$python" -m ensurepip
# Preserve the runtime backend selected by Install.sh --ensure.
"$python" -m pip --disable-pip-version-check install 'pyinstaller>=6.10,<7'
"$python" tools/build_bundle.py
cp -- README.md LICENSE THIRD_PARTY.md dist/SonoWeave/
mkdir -p dist/SonoWeave/docs/assets
cp -- docs/*.md dist/SonoWeave/docs/
cp -- docs/assets/* dist/SonoWeave/docs/assets/
"$python" -m pip freeze > dist/SonoWeave/requirements-installed.txt
"$python" tools/collect_licenses.py dist/SonoWeave/licenses
if [[ -d licenses ]]; then cp -R -- licenses/. dist/SonoWeave/licenses/; fi
echo "Ready: dist/SonoWeave/SonoWeave (models download automatically on first use)"
