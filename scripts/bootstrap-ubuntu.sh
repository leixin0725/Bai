#!/usr/bin/env bash
set -euo pipefail
umask 077

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python_command="${PYTHON_BIN:-python3}"
install_target="$project_root"

usage() {
    cat <<'EOF'
Usage: bash scripts/bootstrap-ubuntu.sh [--dev]

Creates .venv with Python 3.12-3.14 and installs Bai Agent.
Use --dev to include the test and development dependencies.
Set PYTHON_BIN to select another interpreter.
EOF
}

case "${1:-}" in
    "") ;;
    --dev) install_target="${project_root}[dev]" ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
esac
if (( $# > 1 )); then
    printf '%s\n' 'Only one option is accepted.' >&2
    usage >&2
    exit 2
fi

if ! command -v "$python_command" >/dev/null 2>&1; then
    printf 'Python command not found: %s\n' "$python_command" >&2
    exit 2
fi

python_version="$($python_command -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
case "$python_version" in
    3.12|3.13|3.14) ;;
    *)
        printf 'Unsupported Python %s; Bai Agent requires Python 3.12-3.14.\n' "$python_version" >&2
        exit 2
        ;;
esac

if ! "$python_command" -m venv "${project_root}/.venv"; then
    printf '%s\n' 'Unable to create .venv. On Ubuntu, install the venv package first:' >&2
    printf '%s\n' '  sudo apt-get update && sudo apt-get install -y python3-venv' >&2
    exit 2
fi

venv_python="${project_root}/.venv/bin/python"
"$venv_python" -m pip install --upgrade pip
"$venv_python" -m pip install -e "$install_target"

printf 'Bai Agent is ready in %s using Python %s.\n' "${project_root}/.venv" "$python_version"
printf '%s\n' 'Validate: .venv/bin/python -m bai_agent --config-dir config --data-dir data doctor'
printf '%s\n' 'Start:    bash start.sh'
