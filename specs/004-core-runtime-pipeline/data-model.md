# Data Model: 核心运行时与消息管道

> 2026-08-08。所有新增实体均为进程内瞬态 DTO，不落盘、不进入 raw 记录或长期记忆；对话轮次继续使用既有 `RawRecord`/`LongTermDocument` 持久化。DTO 采用项目既有 `FrozenModel`（pydantic）风格。

## 1. PipelineItem（处理项）

管道消费的最小工作单元；所有输入统一为处理项后进入同一 FIFO。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `item_id` | str（`item-*`） | 唯一标识，创建时生成 |
| `kind` | PipelineItemKind | `chat_input` / `timer_event` / `system_event` |
| `payload` | dict[str, Any] | 各 kind 的载荷；`chat_input` 含 `text` 与 `source_boundary`；事件含 `event_kind` 与可选数据 |
| `submitted_at` | datetime（aware UTC） | 进入管道时间（注入时钟） |
| `sequence` | int | 单调递增到达序号，用于顺序可观测与幂等断言 |

### 校验规则

- `kind` 必须属于枚举；`chat_input.payload["text"]` 必须是非空 str。
- `sequence` 由管道单调分配，不允许调用方传入。
- `submitted_at` 只由注入时钟产生，不允许重试时重新采样。

## 2. ConversationAction（对话动作）

一次处理的最小单元；由输入读取器从一次输入动作构建。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `action_id` | str | 唯一标识 |
| `lines` | tuple[str, ...] | 组成该动作的一行或多行（非空） |
| `text` | str | `lines` 按换行拼接后的完整正文 |
| `source_boundary` | InputBoundary | `pipe_eof`（管道整批）或 `buffer_empty`（TTY 缓冲连片） |

### 状态与规则

- `lines` 至少 1 行；合并后正文不得截断或改写。
- 交互式 TTY 下缓冲区已空时，当前累积内容立即提交（零等待）；不设置时间阈值。
- 管道（非 TTY）以 EOF 为整批边界，整批只产生一个动作。

## 3. BackgroundTaskRecord（后台任务）

最小执行器记录的任务状态。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `task_id` | str（`task-*`） | 唯一标识 |
| `name` | str | 任务名（如 `curation`、`maintenance`） |
| `status` | TaskStatus | `waiting` / `running` / `success` / `failure` |
| `created_at` | datetime | 提交时间 |
| `started_at` | datetime \| None | 开始执行时间 |
| `finished_at` | datetime \| None | 结束时间 |
| `error` | str \| None | 失败原因（成功/等待中为 None） |

### 状态转换

```text
waiting ──→ running ──→ success
             │
             └──→ failure
```

- 只允许这一条转换路径；无取消、无重试、无持久化。
- `failure` 必须保留非空 `error`；`success`/`waiting` 的 `error` 必须为 None。
- 串行执行：任意时刻最多一个 `running`。

## 4. RuntimeStatus（运行状态快照）

`chat` 会话内 `:status` 的输出模型。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `session_state` | SessionState | `idle` / `processing` / `stopping` |
| `queue_depth` | int | 等待中的处理项数量（不含当前项） |
| `current_item_id` | str \| None | 正在处理的处理项 |
| `tasks` | tuple[BackgroundTaskRecord, ...] | 后台任务（保留本进程内已结束记录） |
| `health` | HealthState | `ok` / `warning`；有最近重载失败或任务失败时为 `warning` |
| `last_reload` | ReloadStatus | 最近一次配置重载的 revision、结果、错误（如有） |
| `pending_turn_id` | str \| None | raw 尾部 pending 轮次（只读查询，不修改） |
| `counters` | dict[str, int] | 已处理 chat/事件数、任务成功/失败数 |
| `uptime_seconds` | float | 外壳启动以来的秒数 |

### 一致性规则

- `session_state == processing` 时 `current_item_id` 必须非空；`idle`/`stopping` 时为空。
- `queue_depth` 与管道真实等待数一致；统计在事件完成后更新，查询返回一致快照。
- 快照不包含处理项正文、模型输出、记忆正文或任何凭据。

## 5. ConfigGroup（配置分组，只读元数据）

用于校验与错误定位的分组表；**不改变**现有整份 `ConfigSnapshot` 的加载与原子切换。

| 分组 ID | 文件 | 现有校验函数 |
| --- | --- | --- |
| `agent` | `agent.toml` | `validate_agent` + `validate_debug_prompt` |
| `providers` | `providers.toml` | Provider/profile 能力校验 |
| `states` | `states.toml` | 状态与人格引用校验 |
| `tools` | `tools.toml` | 工具清单校验 |
| `logging` | `logging.toml` | 日志配置校验 |
| `history_timestamps` | `history_timestamps.toml` | `validate_history_timestamps` |
| `personas` | `personas/*.md` | 非空、职责唯一、引用解析 |
| `prompts` | `prompts/*.md` | 模板校验（`validate_template`） |

### 规则

- 分组用于错误信息定位（`分组 ID + 字段`）与 `config validate` 状态输出。
- 生效语义保持整份快照原子：任一分组非法 → 整份重载失败 → 保持最后有效快照；运行不中断，修复后下一动作重试。

## 6. 与既有实体关系

```text
PipelineItem(kind=chat_input) ──构建──> ConversationAction ──> SingleTurnController.run_turn ──> RawRecord / LongTermDocument
PipelineItem(kind=timer_event|system_event) ──> 注册的事件处理函数（默认空）
BackgroundExecutor.submit ──> BackgroundTaskRecord（进程内）
RuntimeShell ──持有──> Pipeline + Executor + Application + RuntimeStatus
```

- 管道/执行器/状态只依赖 `domain` DTO 与 `SystemClock`，不依赖存储与 provider 实现。
- `RuntimeShell` 是唯一持有生命周期（启动/停止/释放写锁）的入口；`cli.py` 只装配它。
