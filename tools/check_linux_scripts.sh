#!/usr/bin/env bash
# Exercise a machine without uv or Python using stubs, without system changes.
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
mkdir -p "$root/.cache"
work=$(mktemp -d "$root/.cache/linux-scripts.XXXXXX")
project="$work/project with spaces"
mkdir -p "$project/tools" "$work/bin" "$work/elsewhere"
cp -- "$root/Install.sh" "$root/Start.sh" "$root/PrepareModels.sh" "$root/Build.sh" "$project/"
cp -- "$root/tools/bootstrap.py" "$project/tools/"
export TEST_CALLS="$work/calls" TEST_PYTHON="$work/managed python" TEST_UV_SOURCE="$work/uv-source"
export TEST_INSTALLER="$work/uv-installer"
export PATH="$work/bin:$PATH"
unset SONOWEAVE_UV
cat > "$work/bin/uname" <<'SH'
#!/usr/bin/env bash
case $1 in -s) echo Linux ;; -m) echo x86_64 ;; esac
SH
# Hide any host uv; installer must download the stub standalone uv instead.
cat > "$work/bin/uv" <<'SH'
#!/usr/bin/env bash
echo 'uv 0.1.0'
SH
cat > "$work/bin/curl" <<'SH'
#!/usr/bin/env bash
while (( $# )); do
    if [[ $1 == --output ]]; then cp -- "$TEST_INSTALLER" "$2"; exit 0; fi
    shift
done
exit 2
SH
cat > "$TEST_INSTALLER" <<'SH'
cp -- "$TEST_UV_SOURCE" "$UV_UNMANAGED_INSTALL/uv"
chmod +x "$UV_UNMANAGED_INSTALL/uv"
printf 'uv installed locally\n' >> "$TEST_CALLS"
SH
cat > "$TEST_UV_SOURCE" <<'SH'
#!/usr/bin/env bash
if [[ ${1:-} == --version ]]; then echo 'uv 0.13.4'; exit 0; fi
printf 'uv' >> "$TEST_CALLS"; printf '<%s>' "$@" >> "$TEST_CALLS"; printf '\n' >> "$TEST_CALLS"
if [[ ${1:-} == --no-config ]]; then shift; fi
if [[ $1 == python && $2 == find ]]; then printf '%s\n' "$TEST_PYTHON"; fi
SH
cat > "$TEST_PYTHON" <<'SH'
#!/usr/bin/env bash
printf '<%s>' "$@" >> "$TEST_CALLS"; printf '\n' >> "$TEST_CALLS"
if [[ ${1:-} == tools/bootstrap.py ]]; then
    if [[ ${2:-} == --check ]]; then
        [[ -f .venv-linux/.sonoweave-ready.json && ${BROKEN_ENV:-0} != 1 ]]
        exit $?
    fi
    if [[ ${FAIL_SETUP:-0} == 1 ]]; then exit 7; fi
    backend=${5:-vulkan}
    if [[ $backend == auto ]]; then backend=$(cat .venv-linux/.sonoweave-ready.json 2>/dev/null || echo vulkan); fi
    mkdir -p .venv-linux/bin
    cp -- "$TEST_PYTHON" .venv-linux/bin/python
    chmod +x .venv-linux/bin/python
    printf '%s\n' "$backend" > .venv-linux/.sonoweave-ready.json
fi
SH
chmod +x "$work/bin/"* "$TEST_PYTHON" "$TEST_UV_SOURCE"
cd -- "$work/elsewhere"
for script in "$project/"*.sh; do bash -n "$script"; done
# Start must bootstrap everything automatically, preserving paths and arguments.
bash "$project/Start.sh" --screenshot "an image with spaces.png"
grep -Fxq 'uv installed locally' "$TEST_CALLS"
grep -Fq '<python><install><--no-bin><--no-registry><3.12>' "$TEST_CALLS"
grep -Fxq '<main.py><--screenshot><an image with spaces.png>' "$TEST_CALLS"
if grep -Fq 'prepare_models.py' "$TEST_CALLS"; then echo "Unexpected model download" >&2; exit 1; fi
bash "$project/Install.sh" --cpu
[[ $(cat "$project/.venv-linux/.sonoweave-ready.json") == cpu ]]
: > "$TEST_CALLS"
bash "$project/Start.sh" --preview
grep -Fxq '<main.py><--preview>' "$TEST_CALLS"
if grep -Fq 'uv<' "$TEST_CALLS"; then echo "Healthy launch tried to install" >&2; exit 1; fi
[[ $(cat "$project/.venv-linux/.sonoweave-ready.json") == cpu ]]
bash "$project/PrepareModels.sh" --model both
grep -Fxq '<tools/prepare_models.py><--model><both>' "$TEST_CALLS"
: > "$TEST_CALLS"
if BROKEN_ENV=1 FAIL_SETUP=1 bash "$project/Start.sh" 2>/dev/null; then echo "Setup failure was ignored" >&2; exit 1; fi
if grep -Fq '<main.py>' "$TEST_CALLS"; then echo "App launched after setup failure" >&2; exit 1; fi
mkdir -p "$project/dist/SonoWeave"
cat > "$project/dist/SonoWeave/SonoWeave" <<'SH'
#!/usr/bin/env bash
printf 'bundle<%s>\n' "$*" >> "$TEST_CALLS"
SH
chmod +x "$project/dist/SonoWeave/SonoWeave"
bash "$project/Start.sh" --preview
grep -Fxq 'bundle<--preview>' "$TEST_CALLS"
echo "Automatic uv / Python / dependency launch flow OK (stubbed environment)"
