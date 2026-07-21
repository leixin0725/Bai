# Quickstart: 持久记忆聊天 Agent

本文给出当前实现的安装、运行、维护和验收路径。Bai Agent 只有一条连续历史，不提供 session、thread 或选择旧对话的操作。

## 1. 开发环境

Python 3.13/3.14 均受支持。PowerShell：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

原生 Linux（主要支持环境；macOS 不在范围）：

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

依赖及 Python 范围以 `pyproject.toml` 为准，业务模块不会在运行时安装依赖。

## 2. 配置与外部凭据

可维护内容统一位于：

```text
config/agent.toml                 # 路径、窗口、预算、运行限制
config/providers.toml             # Provider 与模型 profile
config/states.toml                # 状态和有序状态人格
config/tools.toml                 # 工具启用、权限与边界
config/logging.toml               # 安全日志设置
config/personas/chat.md           # 基础聊天人格
config/personas/memory_curator.md # 记忆整理人格
config/personas/states/*.md       # 状态人格
config/prompts/*.md               # 带变量声明的提示模板
```

由秘密管理器向 Agent 进程注入 `DEEPSEEK_API_KEY`。不要把真实值写入仓库文件、shell 命令历史、测试 fixture、提示词或记忆；`providers.toml` 只保存环境变量名。

注入后验证引用图：

```powershell
python -m bai_agent config validate --config-dir config
python -m bai_agent --config-dir config --data-dir data doctor
```

输出只含 revision、职责、状态、模板和启用工具，不回显 Key 或提示正文。修改人格、状态、模板或模型参数后再次验证；新配置只在下一轮边界生效，历史记录不会被重写。

## 3. 聊天与 pending 恢复

```powershell
python -m bai_agent --config-dir config --data-dir data chat
```

逐行输入，使用 EOF 或 Ctrl+C 退出。再次运行同一命令会直接继承全部记忆，不会询问对话 ID。每轮持久化顺序是：用户输入落盘、模型调用、Assistant 输出落盘、向终端显示输出。

模型失败时，用户输入仍保留为单条 pending turn。普通启动会原子丢弃该尾部 pending 并进入新输入；确认再次调用模型时显式执行：

```powershell
python -m bai_agent --config-dir config --data-dir data chat --resume-pending
```

该命令复用原 `turn_id`，不会重复追加用户记录。也可显式丢弃后进入新输入：

```powershell
python -m bai_agent --config-dir config --data-dir data chat --discard-pending
```

`--resume-pending` 与 `--discard-pending` 互斥；没有 pending 时默认、resume 和 discard 都直接等待新输入。

### 3.1 本地提示调试（2026-07-20）

在真实交互式终端运行：

```bash
python -m bai_agent --config-dir config --data-dir data chat --debug-prompts
```

每个 curation/chat/tool continuation/retry 都先展示唯一物化后的最终 provider 载荷及来源；逐次按 `A` 批准，或按 `R` 拒绝并确认 raw、长期记忆、pending 与轮前一致。批准后界面先清除再发送；普通 provider 失败只产生一条 USER pending，只有显式 `--resume-pending` 重发，默认或 `--discard-pending` 放弃。自动验收不调用真实 DeepSeek：

调用标题必须显示 turn/flow/call sequence/purpose/persona/state/provider/model/config revision/attempt/status。Curation、chat、tool continuation 依真实顺序逐项批准；retry 是同一逻辑 call 的新 attempt，不与失败项合并。交互 TTY 可用 `NO_COLOR=1` 验收纯文本等价标签；管道或重定向必须得到 `DEBUG_TTY_REQUIRED`，不能当作无色降级。

`Ctrl+C` 在 TUI 中先按拒绝路径撤销 fresh PREPARED 或删除 resumed pending，再以 130 退出；EOF/终端丢失不批准。启动时取得 WriterLease 后先收敛三态 journal：PREPARED 丢弃、READY_PENDING 发布唯一 pending、READY_TO_COMMIT 发布完整轮次；随后才应用默认丢弃/显式丢弃/显式恢复策略。冲突时禁止新输入与 provider。调试开关不写配置，普通重启恢复为关闭。

```bash
pytest tests/contract/test_model_call_gateway.py tests/integration/test_prompt_trace_single_call.py -q
pytest tests/integration/test_prompt_trace_multi_call.py tests/contract/test_prompt_tui_presentation.py -q
pytest tests/unit/test_context_estimation.py tests/integration/test_prompt_trace_actual_usage.py -q
pytest tests/contract/test_cli_prompt_debug.py tests/integration/test_turn_transaction_security.py -q
pytest tests/performance/test_prompt_trace_release.py tests/integration/test_prompt_debug_runtime_lifecycle.py -q
```

估算字段使用 `≈`：`input = sum(parts) + protocol overhead`，`peak = input + max_output_tokens`。能力取自配置中的 `deepseek-v4-flash` 1M context/384K output cap；两个 profile 仍预留 8192 输出且保持原有生成参数。合法实际 usage 只在 TUI 清除后的普通输出显示；缺失、负数或不守恒 usage 显示不可用。

### 3.2 近期聊天时间段（2026-07-20）

默认配置文件：

```toml
schema_version = 1
display_timezone = "Asia/Shanghai"
long_gap_minutes = 30
continuous_segment_refresh_minutes = 120
split_on_local_date_change = true
```

运行：

```bash
pytest tests/unit/test_temporal_annotation.py tests/unit/test_temporal_annotation_properties.py -q
pytest tests/contract/test_prompt_temporal_context.py tests/integration/test_temporal_chat_context.py tests/unit/test_temporal_prompt_budget.py -q
```

验收时固定检查：密集消息只有首 marker；相邻 gap 恰好 30 分钟时新分段；连续短间隔在最近 marker 后恰好 120 分钟时刷新；跨 `Asia/Shanghai` 自然日和时间倒退时分段；同刻事件保持一段。输出使用完整日期、分钟和 offset。`current_input` 使用本轮 provisional USER record 的既定 `created_at`，其 marker 位于可见 untrusted boundary 内而正文保持 user instruction；人格及状态指令保持无标记。原始 `role: content` 是历史 marker 后的逐字连续子串。

配置修改只在下一份完整快照边界生效，缺失或无效文件必须在请求前失败。生成 marker 不写入 raw/长期记忆；直接检查 `data/memory/` 构建前后字节可证明无持久化副作用。

参数单位与边界：`long_gap_minutes` 为 `1..1440` 分钟；`continuous_segment_refresh_minutes` 为 `1..10080` 分钟且必须大于等于 gap；`split_on_local_date_change` 只能是布尔值；`display_timezone` 只能是 `zoneinfo`/`tzdata` 可解析的 IANA 名称。不要使用 `Local`、Windows 时区名或 `UTC+8` 固定偏移。

若 reload 报错，先保留当前进程与记忆文件，修复错误中点名的 `history_timestamps.toml` 字段，再离线验证并重试下一轮；不需要重置或迁移 raw/YAML。验证只检查凭据环境变量是否存在，可使用明确无效的测试占位值，不会发起网络请求：

```powershell
$env:DEEPSEEK_API_KEY = "invalid-placeholder-only"
python -m bai_agent config validate --config-dir config
pytest tests/integration/test_temporal_config_reload.py tests/integration/test_packaging.py -q
```

POSIX shell 对应使用 `DEEPSEEK_API_KEY=invalid-placeholder-only python -m bai_agent config validate --config-dir config`。项目依赖 `tzdata>=2026.3`，Windows 无系统 IANA 数据库时也必须把固定 UTC instant 转成与 Ubuntu 相同的 `Asia/Shanghai` 日期、分钟和 `+08:00`。

## 4. 记忆组织与完整覆盖

`data/memory/raw/*.jsonl` 永久保存所有确认的用户/Assistant 原文，分段只影响物理存储。`data/memory/long_term.yaml` 在同一个 revision 内保存：

- 记忆整理前沿；
- `MemoryCoverageOverview` 和连续 coverage spans；
- 长期记忆及其 `source_refs`；
- 每个来源的原始记录 ID 与内容哈希。

近期直接窗口大小来自 `config/agent.toml`。仅当最旧完整轮次将离开窗口时，`memory_curator` 才批量整理一次；空提取也必须扩展 coverage span。每条原始记录始终恰好处于“已由连续 span 表示”或“仍完整直接注入”范围，出现缺口会在模型调用前失败。

检查权威记录、来源和覆盖：

```powershell
python -m bai_agent --config-dir config --data-dir data memory validate
```

成功 JSON 包含 `raw_records`、`long_term_items`、`curated_through_sequence`、`coverage_spans`、`coverage_gaps: 0`、`dangling_sources: 0` 和 `direct_range`。

聊天提示中的三个历史区块独立标注：

- `memory_overview` 使用全部 coverage records 的最早—最晚 `SOURCE_RANGE`；
- `long_term_memories` 按既有相关性顺序逐项使用全部 `source_refs` 的 `SOURCE_RANGE`；
- `recent_records` 使用每条 raw 的 EVENT 点时间。

运行：

```bash
pytest tests/unit/test_memory_temporal_projection.py tests/unit/test_temporal_prompt_budget.py -q
pytest tests/integration/test_temporal_chat_context.py tests/integration/test_long_term_store.py -q
```

验收来源引用乱序、重复、范围相等/重叠和时间倒退时仍保持选择顺序；来源缺失、hash 不匹配、coverage 错误和非法时间全部在 provider 前失败。现行 schema v1 不使用 YAML 记忆的整理时间降级，未知格式也不进入 `RECORDED`。测试同时比较构建前后 raw/YAML/last-valid 字节，确认无需迁移且 marker 不持久化。

### 4.1 记忆整理的三个时间区块

整理提示分别标注 `batch_records`、`existing_memories`、`current_overview`；每个非空区块拥有自己的首 marker。历史项保持一项一行 canonical JSON，marker 位于 JSON 前一行，批次元数据、边界指令和输出 schema 不进入时间线。`memory_candidates` 与 `overview_update` 的返回结构不变。

```powershell
pytest tests/integration/test_temporal_curation_context.py tests/integration/test_curation_workflow.py tests/integration/test_curation_transaction_proposal.py -q
pytest tests/unit/test_prompt_provenance.py tests/unit/test_context_estimation.py tests/unit/test_context_estimation_properties.py -q
```

验收时应使用跨三个区块的重复正文，逐个检查最终 prompt 的绝对 `[start,end)` span 可回读对应 marker/JSON 且互不重叠；破损来源必须在 provider 调用前失败。JSON 示例可直接用 `json.loads()` 解析，且其中不得出现时间 marker 字段。

### 4.2 工具调用与结果时间

工具调用批次使用成功模型响应的接受时刻，工具结果使用结果已可发送的完成时刻。运行时 metadata 不进入 DTO/canonical JSON；模型侧仍是相邻的 `assistant(tool_calls)`→`tool(tool_call_id)`，marker 只位于各自 `content` 前。多轮 continuation 每次整体重建同一个 `tool_history`，不会把上一轮已经标注的字符串再次标注。

```powershell
pytest tests/contract/test_temporal_tool_protocol.py tests/integration/test_temporal_tool_continuation.py -q
pytest tests/contract/test_deepseek_tool_calls.py tests/integration/test_prompt_debug_equivalence.py tests/unit/test_prompt_provenance.py -q
pytest tests/contract/test_memory_source_tool.py tests/contract/test_prompt_temporal_context.py -q
```

验收时逐字段比较 call id/name/arguments/order、tool_call_id 与去掉可选 marker 后的 canonical result body。直接执行 `memory_source_query` 的输入、分页、权限、错误与返回 JSON 必须继续通过原 golden 测试，且不得出现时间字段。

## 5. 来源查询

从 `long_term.yaml` 选择 `memory_id`：

```powershell
python -m bai_agent --config-dir config --data-dir data memory source mem-UUID
```

结果按 `global_sequence` 返回来源原文并支持游标分页，不暴露存储路径。`memory_source_query` 对聊天人格、整理人格和获准的辅助人格使用同一 Schema、权限和错误语义。未调用工具时，来源原文不会因长期记忆被自动注入；工具结果只属于发起查询的当前 flow。

## 6. 记忆重置

重置命令不调用 Provider，也不需要 API Key。先停止 Agent，再按需要执行：

```powershell
# [2026-07-19] 保留全部原始聊天和近期直接窗口，只清空长期派生记忆。
python -m bai_agent --config-dir config --data-dir data memory reset long-term

# [2026-07-19] 清空原始聊天、近期窗口、长期记忆、覆盖概览和整理前沿，恢复首次启动状态。
python -m bai_agent --config-dir config --data-dir data memory reset all
```

两条命令使用显式作用域后立即执行，不再询问确认。`long-term` 会保留 coverage spans 作为已处理范围索引，但把可注入概览改为中性文本，避免下一轮从旧原文立即重新生成刚删除的长期事实；`all` 还会清除原始分段和可能含旧正文的原子临时副本。安全事件状态位于记忆根之外，不随任何记忆重置删除。

成功输出只报告重置前后的原始记录数、长期条目数、coverage span 数和整理前沿，不回显记忆正文；若损坏文档无法可信统计旧长期条目，对应重置前计数为 `-1`。若聊天实例仍持有写锁，命令以 `WRITER_LOCKED` 拒绝执行。

## 7. 人工维护、备份与恢复

人工操作前：

1. 停止所有 Agent 进程，避免与单写者锁竞争。
2. 复制整个 `data/memory/` 到受保护位置；不要只备份 YAML，因为来源依赖永久 JSONL。
3. 用 UTF-8 文本编辑器修改 `long_term.yaml`。新条目必须有唯一 `memory_id`、至少一个真实 `source_ref`、有效哈希和 `created_by: manual`；不要直接改整理前沿或 coverage spans。
4. 执行 `memory validate`，确认权限、Schema、关系图、来源哈希和完整覆盖均有效后再启动聊天。

有效人工变更在下次加载时进入新 revision，并尽量保留 YAML 注释和顺序。无效格式、悬空来源、摘要不符、重复 ID、关系环或前沿修改会被拒绝；主文件原样保留，程序可读取 `.state/long_term.last-valid.yaml`，但处于只读回退时禁止自动整理。

恢复备份时同样停止 Agent、整体恢复 `data/memory/`，并运行：

```powershell
python -m bai_agent --config-dir config --data-dir data memory validate
python -m bai_agent --config-dir config --data-dir data doctor
```

POSIX 预期目录为 `0700`、文件为 `0600`；Windows 预期 DACL 仅允许当前用户、SYSTEM 和 Administrators。程序会尽力收紧本地路径，网络共享、符号链接/junction 或无法验证的权限会 fail-closed。

## 8. Provider、工具、状态与自主循环扩展

DeepSeek 通过 Provider-neutral DTO 接入。新增供应商时实现 `ModelProvider` adapter 并复用 Provider 契约测试，不能把 SDK 对象带入 Controller、Memory 或 Tool 层。

新增工具时在注册器声明本地 input/output JSON Schema、安全 annotations、启用开关和获准人格，并保留 deadline、轮数、结果大小和无正文审计限制。状态解析器只能返回可信配置中已定义的人格 ID 与顺序。自主循环默认 `disabled`；测试 Runner 也必须受迭代、deadline、token/成本、人工停止、取消和幂等检查点约束。

## 9. 自动化与兼容性矩阵

根据[项目宪章](../../.specify/memory/constitution.md)，每次重大更新都要在对应实现阶段同步维护受影响的 README、quickstart、运行手册、配置说明、公共契约和当前功能制品，不能只在最终润色阶段补文档。计划必须列出文档影响、更新内容与验证方式；无影响时记录 `N/A` 及理由。

提交前应实际执行受影响文档中的安全本地命令，核对路径和相对链接可达、示例与当前参数及输出语义一致，并运行下面的适用自动化门禁。重大更新的代码和对应文档必须位于同一个原子提交中。

默认本地门禁不需要真实 Provider 调用：

```powershell
pytest
pytest tests\unit tests\contract
pytest tests\integration tests\fault_injection
python -m bai_agent --data-dir .tmp\validation security incident check
git diff --check
```

`.github/workflows/compatibility.yml` 在固定 Ubuntu 24.04 上以 Python 3.13/3.14 运行主要功能门禁，并在 Windows runner 上运行次要兼容门禁；macOS 不在范围。真实 DeepSeek smoke test 必须使用显式 marker、隔离数据和最小配额，不进入默认 CI。

## 10. Ubuntu 24.04 提示 TUI 性能复现

在原生 Ubuntu 24.04、Python 3.13、80×24 `xterm-256color` 中执行：

```bash
TERM=xterm-256color pytest tests/performance/test_prompt_tui_latency.py -q -s
```

计时从 frozen request、来源和估算就绪到标题、身份、上下文摘要完成 mounted；首次冷启动单独记录，强制门禁只计算随后 30 次同进程启动的 p95，要求不超过 500 ms。Python 3.14 进入 Ubuntu 主要功能矩阵但不承担该固定性能门禁；Windows 仅做次要功能兼容，macOS 不在范围。

## 11. 凭据事件处置

常规检查：

```powershell
pytest tests\integration\test_repository_secret_safety.py
python -m bai_agent --data-dir data security incident check
```

若凭据可能进入工作树、可达 Git 历史、生成制品、日志或运行数据，立即停止聊天和整理。按照[全仓库凭据泄露事件处置流程](../../docs/security-incident-response.md)撤销/轮换凭据，扫描工作树与全部可达历史，检查运行数据和制品，并提供四项处置证据后再显式解除门禁。不得仅删除当前文件就继续运行。

> [2026-07-19] 本 quickstart 与当前 CLI、性能基线和六组合兼容矩阵同步。
