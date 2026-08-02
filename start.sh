#!/usr/bin/env bash
set -euo pipefail
umask 077

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python_bin="${project_root}/.venv/bin/python"

debug_prompts=0
resume_pending=0
discard_pending=0

usage() {
    cat <<'EOF'
Usage: bash start.sh [--debug-prompts] [--resume-pending | --discard-pending]
EOF
}

for argument in "$@"; do
    case "$argument" in
        --debug-prompts) debug_prompts=1 ;;
        --resume-pending) resume_pending=1 ;;
        --discard-pending) discard_pending=1 ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown option: %s\n' "$argument" >&2; usage >&2; exit 2 ;;
    esac
done

if (( resume_pending && discard_pending )); then
    printf '%s\n' '--resume-pending and --discard-pending are mutually exclusive.' >&2
    exit 2
fi

if [[ ! -x "$python_bin" ]]; then
    printf 'Virtual environment not found: %s\n' "$python_bin" >&2
    printf '%s\n' 'Run: bash scripts/bootstrap-ubuntu.sh' >&2
    exit 2
fi

cd -- "$project_root"

injected_key=0
cleanup() {
    if (( injected_key )); then
        unset DEEPSEEK_API_KEY
    fi
}
trap cleanup EXIT HUP INT TERM

if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
    if [[ ! -t 0 ]]; then
        printf '%s\n' 'DEEPSEEK_API_KEY is not set and stdin is not an interactive terminal.' >&2
        exit 2
    fi
    read -r -s -p 'DeepSeek API Key: ' DEEPSEEK_API_KEY
    printf '\n'
    if [[ -z "$DEEPSEEK_API_KEY" ]]; then
        printf '%s\n' 'DeepSeek API Key cannot be empty.' >&2
        exit 2
    fi
    export DEEPSEEK_API_KEY
    injected_key=1
fi

chat_args=(
    -m bai_agent
    --config-dir config
    --data-dir data
    chat
)
if (( debug_prompts )); then
    chat_args+=(--debug-prompts)
fi
if (( resume_pending )); then
    chat_args+=(--resume-pending)
fi
if (( discard_pending )); then
    chat_args+=(--discard-pending)
fi

"$python_bin" "${chat_args[@]}"
