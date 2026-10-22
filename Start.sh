#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ ! -f main.py && -x dist/SonoWeave/SonoWeave ]]; then
    exec dist/SonoWeave/SonoWeave "$@"
fi
if [[ ! -f main.py ]]; then
    echo "Cannot find main.py or dist/SonoWeave/SonoWeave." >&2
    exit 1
fi
bash Install.sh --ensure
exec .venv-linux/bin/python main.py "$@"
