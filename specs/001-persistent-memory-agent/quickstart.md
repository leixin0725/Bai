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

Linux/macOS：

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

模型失败时，已经确认的用户输入仍保留为 pending turn。普通启动只报告待恢复状态；确认再次调用模型时显式执行：

```powershell
python -m bai_agent --config-dir config --data-dir data chat --resume-pending
```

该命令复用原 `turn_id`，不会重复追加用户记录。

### 3.1 本地提示调试（2026-07-20）

在真实交互式终端运行：

```bash
python -m bai_agent --config-dir config --data-dir data chat --debug-prompts
```

每个 curation/chat/tool continuation/retry 都先展示唯一物化后的最终 provider 载荷及来源；逐次按 `A` 批准，或按 `R` 拒绝并确认 raw、长期记忆、pending 与轮前一致。批准后界面先清除再发送；普通 provider 失败只产生一条 USER pending，随后用既有 `--resume-pending`。自动验收不调用真实 DeepSeek：

调用标题必须显示 turn/flow/call sequence/purpose/persona/state/provider/model/config revision/attempt/status。Curation、chat、tool continuation 依真实顺序逐项批准；retry 是同一逻辑 call 的新 attempt，不与失败项合并。交互 TTY 可用 `NO_COLOR=1` 验收纯文本等价标签；管道或重定向必须得到 `DEBUG_TTY_REQUIRED`，不能当作无色降级。

```bash
pytest tests/contract/test_model_call_gateway.py tests/integration/test_prompt_trace_single_call.py -q
pytest tests/integration/test_prompt_trace_multi_call.py tests/contract/test_prompt_tui_presentation.py -q
pytest tests/unit/test_context_estimation.py tests/integration/test_prompt_trace_actual_usage.py -q
```

估算字段使用 `≈`：`input = sum(parts) + protocol overhead`，`peak = input + max_output_tokens`。能力取自配置中的 `deepseek-v4-flash` 1M context/384K output cap；两个 profile 仍预留 8192 输出且保持原有生成参数。合法实际 usage 只在 TUI 清除后的普通输出显示；缺失、负数或不守恒 usage 显示不可用。

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

`.github/workflows/compatibility.yml` 在 Windows、Ubuntu、macOS 上分别以 Python 3.13 和 3.14 安装项目，验证模块入口、权限结果归一化、本地原子替换、UTF-8 和全部非性能功能。真实 DeepSeek smoke test 必须使用显式 marker、隔离数据和最小配额，不进入默认 CI。

## 10. Windows 参考性能复现

性能 fixture 含 10,000 条永久原始记录、1,000 条长期记忆和配置上限内的 48 条近期直接原文。显式开启后执行至少 100 次全新 Python 进程：

```powershell
$env:BAI_RUN_WINDOWS_REFERENCE = "1"
$env:BAI_REFERENCE_MEMORY = "记录参考机内存规格"
$env:BAI_REFERENCE_STORAGE = "记录参考机存储规格"
pytest tests\performance\test_startup.py -m performance -q -s
```

计时从进程创建到配置、原始索引、长期 YAML、覆盖概览和首轮 `PromptContext` 可用；报告 OS、CPU、内存、存储、Python、缓存策略和 nearest-rank p95。门槛为 3 秒且网络调用为 0，只在指定 Windows 参考环境判定，其他平台只跑功能矩阵。

## 11. 凭据事件处置

常规检查：

```powershell
pytest tests\integration\test_repository_secret_safety.py
python -m bai_agent --data-dir data security incident check
```

若凭据可能进入工作树、可达 Git 历史、生成制品、日志或运行数据，立即停止聊天和整理。按照[全仓库凭据泄露事件处置流程](../../docs/security-incident-response.md)撤销/轮换凭据，扫描工作树与全部可达历史，检查运行数据和制品，并提供四项处置证据后再显式解除门禁。不得仅删除当前文件就继续运行。

> [2026-07-19] 本 quickstart 与当前 CLI、性能基线和六组合兼容矩阵同步。
