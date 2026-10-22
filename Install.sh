#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
trap 'echo "Environment setup failed; the app was not started. Retry Install.sh (docs/linux.md)." >&2' ERR
if [[ $(uname -s) != Linux || $(uname -m) != x86_64 ]]; then
    echo "This installer supports Linux x86_64." >&2
    exit 1
fi
backend=auto
ensure=false
case ${1:-} in
    "") ;;
    --cpu) backend=cpu ;;
    --cuda) backend=cuda ;;
    --vulkan) backend=vulkan ;;
    --ensure) backend=auto; ensure=true ;;
    *) echo "Usage: bash Install.sh [--cpu | --cuda | --vulkan]" >&2; exit 2 ;;
esac
if (( $# > 1 )); then echo "Too many installation arguments." >&2; exit 2; fi
if [[ $ensure == true && -x .venv-linux/bin/python ]] && \
    .venv-linux/bin/python tools/bootstrap.py --check --backend auto; then
    exit 0
fi

runtime="$PWD/.runtime/linux"
mkdir -p "$runtime/uv" "$PWD/.cache"
export UV_PYTHON_INSTALL_DIR="$runtime/python"
export UV_CACHE_DIR=${UV_CACHE_DIR:-"$PWD/.cache/uv"}
export UV_PYTHON_INSTALL_BIN=0 UV_PYTHON_INSTALL_REGISTRY=0
uv_bin=${SONOWEAVE_UV:-}
compatible_uv() {
    local version major minor patch
    version=$("$1" --version 2>/dev/null) || return 1
    [[ $version =~ ^uv\ ([0-9]+)\.([0-9]+)\.([0-9]+) ]] || return 1
    major=${BASH_REMATCH[1]}; minor=${BASH_REMATCH[2]}; patch=${BASH_REMATCH[3]}
    (( major > 0 || minor > 8 || (minor == 8 && patch >= 22) ))
}
if [[ -n $uv_bin ]]; then
    compatible_uv "$uv_bin" || { echo "SONOWEAVE_UV must point to uv >= 0.8.22." >&2; exit 1; }
else
    for candidate in "$runtime/uv/uv" "$(command -v uv || true)"; do
        if [[ -n $candidate ]] && compatible_uv "$candidate"; then uv_bin=$candidate; break; fi
    done
    if [[ -z $uv_bin ]]; then
        echo "Downloading uv..."
        installer=$(mktemp "$runtime/uv-install.XXXXXX")
        if command -v curl >/dev/null; then
            curl --fail --location --retry 3 https://astral.sh/uv/0.13.4/install.sh --output "$installer"
        elif command -v wget >/dev/null; then
            wget -O "$installer" https://astral.sh/uv/0.13.4/install.sh
        else
            echo "Install curl or wget to download uv." >&2
            exit 1
        fi
        UV_UNMANAGED_INSTALL="$runtime/uv" sh "$installer"
        uv_bin="$runtime/uv/uv"
        compatible_uv "$uv_bin" || { echo "uv installation failed." >&2; exit 1; }
    fi
fi
"$uv_bin" --no-config python install --no-bin --no-registry 3.12
python_bin=$("$uv_bin" --no-config python find --managed-python --no-project 3.12)
"$python_bin" tools/bootstrap.py --uv "$uv_bin" --backend "$backend"
