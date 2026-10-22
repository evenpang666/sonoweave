#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ -x dist/SonoWeave/SonoWeave ]]; then
    exec dist/SonoWeave/SonoWeave "$@"
fi
bash Install.sh --ensure
exec .venv-linux/bin/python main.py "$@"
