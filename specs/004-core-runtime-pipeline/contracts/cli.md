# Contract: chat 循环、输入合并与状态命令（阶段 1）

> 2026-08-08。本契约补充既有 `specs/001-persistent-memory-agent/contracts/cli.md` 的 `chat` 部分；其他命令与全局选项不变。

## 1. `chat` 启动

```powershell
python -m bai_agent --config-dir config --data-dir data chat [--resume-pending|--discard-pending] [--debug-prompts]
```

- 启动顺序不变：写锁 → 配置与记忆验证 →（pending 恢复/丢弃策略）→ 进入运行时外壳。
- `--resume-pending`：恢复轮作为首个 `chat_input` 处理项进入管道，语义与现有恢复一致。
- `--discard-pending` / 默认丢弃：行为与现有实现一致，然后进入循环。
- 提示词调试 TUI 的 TTY 预检与逐请求审批流程不变。

## 2. 输入与合并

- stdin 非 TTY（管道）：整批内容（至 EOF）为一次输入动作，只产生一次处理与一条回复。
- 交互式 TTY：缓冲区连片到达的多行合并为一次动作；缓冲区空时当前行独立成动作；零等待、无时间窗口。
- 精确输入 `:status` 被拦截为状态命令：不写入 raw 记录、不进入模型调用；输出 `RuntimeStatus` 稳定 JSON。
- 以 `:` 开头的其他输入按普通对话内容处理（不预留命令空间）。
- 每次对话动作开始前的配置重载失败：先向 stderr 输出明确警告（分组、字段、失败原因与继续使用的配置 revision），再按旧快照继续处理该动作；不得吞掉或仅被动记录（禁止静默回退）。

## 3. 退出码

- 正常 EOF 退出：0。
- 第一次 SIGINT（Ctrl+C）：优雅停止后 130（与现有 `KeyboardInterrupt → 130` 一致）。
- 第二次 SIGINT：立即中止当前处理项后 130。
- SIGTERM：优雅停止后 0。
- 启动/配置/记忆/凭据错误：沿用现有错误码（4=写锁、5=raw/memory、6=provider、7=安全事件、其余 2/3）。

## 4. `:status` 输出示例

```json
{
  "ok": true,
  "session_state": "processing",
  "queue_depth": 2,
  "current_item_id": "item-...",
  "tasks": [{"task_id": "task-...", "name": "curation", "status": "success"}],
  "health": "ok",
  "last_reload": {"revision": "sha256:...", "ok": true, "error": null},
  "pending_turn_id": null,
  "counters": {"chat_turns": 3, "events": 0, "tasks_succeeded": 1, "tasks_failed": 0},
  "uptime_seconds": 12.5
}
```

- 输出为排序稳定 JSON；不含正文、记忆内容或凭据。
