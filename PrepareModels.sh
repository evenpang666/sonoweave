#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
bash Install.sh --ensure
exec .venv-linux/bin/python tools/prepare_models.py "$@"
