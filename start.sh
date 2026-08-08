#!/usr/bin/env bash
set -euo pipefail
umask 077

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python_bin="${project_root}/.venv/bin/python"

# [2026-08-08] start.sh 是 bai-agent 的透传壳：无参数默认进入 chat；
# 显式命令原样透传；无显式命令时在全局选项后补 chat，因为 chat 专属参数
# 必须位于 chat 子命令之后（例如 bash start.sh --debug-prompts）。
command="chat"
saw_command=0
forward_args=()
pending_args=()
has_help=0
resume_pending=0
discard_pending=0
original_args=("$@")

for argument in "$@"; do
    case "$argument" in
        --resume-pending) resume_pending=1 ;;
        --discard-pending) discard_pending=1 ;;
        -h|--help) has_help=1 ;;
    esac
done

# 在读取凭据前拒绝互斥组合；存在 --help 时交给 bai-agent 先行退出，
# 与原生 argparse 行为保持一致。
if (( resume_pending && discard_pending && ! has_help )); then
    printf '%s\n' '--resume-pending and --discard-pending are mutually exclusive.' >&2
    exit 2
fi

while (( $# )); do
    argument="$1"
    shift
    if (( saw_command )); then
        forward_args+=("$argument")
        continue
    fi
    case "$argument" in
        --config-dir|--data-dir)
            forward_args+=("$argument")
            if (( $# )); then
                forward_args+=("$1")
                shift
            fi
            ;;
        --json)
            forward_args+=("$argument")
            ;;
        -*)
            # 尚未出现命令时的其它选项：默认按 chat 参数处理。
            pending_args+=("$argument")
            ;;
        *)
            command="$argument"
            saw_command=1
            forward_args+=("${pending_args[@]}" "$argument")
            pending_args=()
            ;;
    esac
done
if (( ! saw_command )); then
    if (( has_help )); then
        # 无显式命令且出现 --help 时原样透传，与 bai-agent 原生
        # 行为一致：显示完整帮助而非注入 chat 后的子命令帮助。
        forward_args=("${original_args[@]}")
    else
        forward_args+=(chat "${pending_args[@]}")
    fi
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

if [[ "$command" == chat && "$has_help" -eq 0 && -z "${DEEPSEEK_API_KEY:-}" ]]; then
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

"$python_bin" -m bai_agent "${forward_args[@]}"
