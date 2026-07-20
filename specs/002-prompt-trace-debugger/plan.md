# Implementation Plan: 提示词追踪调试工具

**Branch**: `002-prompt-trace-debugger` | **Date**: 2026-07-19 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-prompt-trace-debugger/spec.md`

## Summary

为 Bai Agent 增加仅由启动参数启用的本地类 TUI 调试门禁：每次模型调用经 provider `prepare()` 和唯一 `materialize_sdk_kwargs()` 后展示将要发送的完整提示承载内容、逐段来源和上下文占用估算，只有用户逐次批准后才关闭正文 TUI 并发送同一不可变载荷；`send_once()` 在 `finally` 释放发送载荷。唯一 `ModelCallGateway` 统一聊天、记忆整理、工具续接、重试和未来辅助人格调用，三态轮次事务以 `PREPARED`、`READY_PENDING`、`READY_TO_COMMIT` 分别处理明确拒绝丢弃、普通 provider/网络失败发布单条 USER pending、成功发布完整轮次。

## Technical Context

**Language/Version**: Python `>=3.13,<3.15`

**Primary Dependencies**: 现有 `openai`、`pydantic`、`ruamel.yaml`、`filelock`；新增 Textual `>=8.2,<9`，用于跨平台全屏类 TUI、滚动长文本、键盘/按钮批准和无头测试。Textual 自带 Rich 渲染能力，不再增加第二套终端 UI 依赖。

**Storage**: 保留 JSONL 原始记录和 YAML 长期记忆；新增单个私有、原子替换的 `data/memory/.state/turn-transaction.json` 三态轮次事务日志。该日志只保存恢复所需的暂存用户记录（含本轮用户输入）、基线身份、安全失败码和待发布结果；除该暂存输入外，不保存组装后的最终模型请求、其他提示片段、来源追踪、认证信息或已拒绝标记。

**Testing**: `pytest`、`pytest-asyncio`、`hypothesis`、`respx`，加 Textual `App.run_test()`/Pilot 无头交互测试；使用伪 provider、SDK 参数捕获和 HTTP mock 验证显示载荷与实际出站载荷逐字段一致。

**Core Business Logic**: BR-001 由唯一物化载荷摘要、逐字段 SDK/HTTP 对比及 Unicode/空内容/工具定义测试覆盖；BR-002 由来源完整性、聚合来源、运行时来源和未知来源阻断测试覆盖；BR-003 由聊天/整理/工具续接/重试的调用序列与逐次批准测试覆盖；BR-004 由输入总量守恒、容量未知、估算不可用、峰值与阈值测试覆盖；BR-005 由有色、交互式无色和窄终端测试覆盖，非 TTY 单独 fail closed；BR-006 由调试开关请求等价、批准摘要绑定和失败关闭测试覆盖；BR-007 由凭据门禁、无持久追踪、批准后 TUI 清除、sender `finally` 释放及 1,000 次调用残留测试覆盖；BR-008 由各批准点拒绝、轮次事务状态机和逐持久化步骤故障注入恢复测试覆盖；BR-009 由 provider/网络失败发布单条 pending、重启与 `--resume-pending`、reject 不形成 pending 测试覆盖；BR-010 由当前工具只读审计和 fake 写工具事务/补偿能力门禁测试覆盖。每条规则均包含正常、边界和关键失败路径。

**Comment Impact**: 新增模型网关、来源映射、估算、TUI 和轮次事务中的设计意图/恢复不变量注释，统一使用简体中文并以 `[2026-07-20]` 或实际实现日期/版本标记；仅在现有注释因调用路径或持久化语义改变而过时时更新，并保留版本痕迹。

**Sensitive Credentials**: DeepSeek API Key 继续只从 `DEEPSEEK_API_KEY` 环境变量注入。认证头在 provider 发送层生成，不进入 `PreparedProviderRequest`、来源、TUI、日志或事务日志；展示前和发送前都执行现有凭据检测/安全事件门禁。文档、测试和夹具只使用明确无效的占位符，并运行仓库及可达 Git 历史凭据扫描。

**Git Milestones**: 六个阶段提交与任务检查点一一对应：(1) Foundation（Setup + Foundational）；(2) US1 完整请求、来源、批准/拒绝和唯一网关；(3) US2 多调用身份、顺序与视觉表达；(4) US3 上下文估算、实际用量、模型迁移与 Linux 性能门禁；(5) US4 TTY、恢复、释放与等价性硬化；(6) Final 规模、安全、可用性、兼容性和最终审计。每个阶段只在代码、同批文档和验证结果齐备后原子提交。

**Documentation Impact**: 更新 `README.md`（启用、批准、隐私、交互式无色、非 TTY、上下文字段、拒绝与普通失败语义）；更新 `specs/001-persistent-memory-agent/quickstart.md` 和 `contracts/{cli,configuration,model-and-tools,storage}.md`（启动命令、`deepseek-v4-flash` 元数据、唯一网关、三态轮次事务与恢复）；必要时同步 `start.ps1` 的启动参数透传；更新 `.github/workflows/compatibility.yml`（Ubuntu 24.04/Python 3.13/3.14 主矩阵、Windows 次矩阵、移除 macOS、Ubuntu 手动性能作业及 `[2026-07-20]` 中文注释）；新增本功能的 `research.md`、`data-model.md`、`contracts/` 和 `quickstart.md`。用配置校验、文档中的命令冒烟、合同/集成/故障测试、相对链接检查和凭据扫描验证，并与对应重大代码变更同一提交。

**Target Platform**: 原生 Linux 为主要支持平台，以 Ubuntu 24.04、Python 3.13/3.14 的交互式终端为功能验收环境；Windows 11/PowerShell 为次要功能兼容平台；macOS 不在本功能范围内。调试模式要求 stdin 和 stdout 都是 TTY。

**Project Type**: 单体 Python CLI 应用，采用领域端口与适配器分层。

**Performance Goals**: 在原生 Ubuntu 24.04、Python 3.13、80×24 `xterm-256color` 中，从 frozen request、来源和估算就绪到标题/身份/上下文摘要 mounted 的 30 次同进程启动 p95 不超过 500 ms，首次冷启动单独记录但不作为门禁；代表性请求集的输入估算至少 95% 落在实际值的 `max(15%, 128 tokens)` 误差范围；连续批准 1,000 次后 presenter 正文/来源和 sender 发送载荷残留均为 0。

**Constraints**: 调试关闭与开启时形成相同最终请求；provider 端口只含 `prepare()`、唯一 `materialize_sdk_kwargs()` 和 `send_once()`，每次物理发送必须经过同一网关且每次重试重新批准；批准绑定 call、attempt 与 materialized digest，发送前重新校验；批准后正文 TUI 立即关闭，sender 仅保留不可变发送载荷并在 `finally` 释放；`ActualUsageSummary` 不持有 prompt、part 或 `SourceRef`；可信估算不可用时明确显示不可估算；正文/来源不落盘；TUI 或来源校验失败时安全阻断；明确拒绝后状态与轮前检查点一致，普通 provider/网络失败发布单条 USER pending；写工具没有可恢复事务或补偿契约时在副作用前拒绝。

**Scale/Scope**: 单用户、单写者、本地进程；一轮最多包含整理、聊天、最多 4 次只读工具续接和 provider 重试；单段可关联数百来源；`deepseek-v4-flash` provider 能力元数据为 1M context/384K output，两个 profile 的请求输出限制保持 8192；验收覆盖 200 次混合调用和 1,000 次连续清理。

## Constitution Check

*GATE: Phase 0 前检查及 Phase 1 设计后复核均通过。*

| Gate | Required Evidence | Status |
|------|-------------------|--------|
| I. Clarity, extensibility, maintainability | `ModelCallGateway`、provider 适配、来源验证、估算、展示和轮次事务职责分离；扩展点在 contracts 中给出 | PASS |
| II. Decoupling and readability | 领域模型/端口不依赖 Textual 或 OpenAI SDK；provider 和 TUI 仅实现端口；持久化通过轮次工作单元协调，无跨层共享可变请求 | PASS |
| III. Simplest understandable implementation | 仅增加一个 TUI 依赖、一个统一网关和一个事务日志；不引入数据库、事件溯源或持久追踪 | PASS |
| IV. zh-CN traceable comments | `Comment Impact` 已定义 `[2026-07-20]` 或实际实现日期/版本的新增/更新规则，旧注释仅在过时时调整 | PASS |
| V. Git discipline | `Git Milestones` 定义与任务一致的六个可独立验证、文档同步原子提交边界 | PASS |
| VI. Core business tests | `Core Business Logic` 将 BR-001—BR-010 全部映射到正常、边界和关键失败自动化测试 | PASS |
| VII. Credential protection | 环境变量注入、认证与提示载荷分离、双门禁、脱敏错误、夹具约束及历史扫描均已设计 | PASS |
| VIII. Major-update documentation sync | `Documentation Impact` 列出受影响文件、内容、验证方式及同提交边界 | PASS |

**Phase 1 post-design re-check**: `data-model.md` 已固定唯一物化载荷、来源、三态事务和实际用量不变量；`contracts/model-call.md`、`cli-tui.md`、`turn-transaction.md`、`configuration.md` 已分别证明依赖方向、批准门禁、普通失败/pending 与拒绝差异、工具副作用能力、凭据边界和模型迁移；`quickstart.md` 已为 BR-001—BR-010 提供可执行验证入口。未出现新的门禁失败，八项继续为 PASS。

## Project Structure

### Documentation (this feature)

```text
specs/002-prompt-trace-debugger/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── cli-tui.md
│   ├── configuration.md
│   ├── model-call.md
│   └── turn-transaction.md
└── tasks.md                 # 已由 /speckit-tasks 生成并完成实现状态更新
```

### Source Code (repository root)

```text
src/bai_agent/
├── application.py          # 组合网关、TUI、估算器与轮次恢复
├── cli.py                  # --debug-prompts 与交互终端门禁
├── config/
│   ├── loader.py           # 保留配置资产路径、摘要与修订
│   └── validation.py       # 调试策略与模型容量配置校验
├── domain/
│   ├── models.py           # 调用草稿、最终请求、来源、用量和事务值对象
│   └── ports.py            # provider、批准界面、估算器和事务端口
├── model_calls/
│   ├── gateway.py          # 所有物理模型调用的唯一入口及重试编排
│   ├── provenance.py       # JSON Pointer/正文区间与来源完整性校验
│   └── estimation.py       # provider-aware 输入估算和守恒分摊
├── debug/
│   └── tui.py              # 短生命周期 Textual 批准界面
├── memory/
│   ├── curation.py         # 整理提案与最终提交分离
│   └── transaction.py      # 可恢复轮次工作单元及发布
├── prompting/
│   └── assembler.py        # 构建时保留参与/排除状态和来源
├── providers/
│   └── deepseek.py         # prepare、唯一 materialize_sdk_kwargs 与 send_once
└── security/
    └── redaction.py        # 展示/发送前凭据门禁复用

tests/
├── contract/               # CLI、gateway、provider、TUI 和配置契约
├── unit/                   # 来源、摘要、估算守恒和状态机
├── integration/            # 多调用批准、等价性、拒绝回滚和恢复
├── fault_injection/        # 事务每个落盘/发布步骤的中断恢复
├── performance/            # Ubuntu 500 ms 展示和 1,000 次释放验收
└── fixtures/               # 仅含无效占位凭据的请求/配置/终端样例
```

**Structure Decision**: 延续现有单体包和端口/适配器结构；新增 `model_calls` 作为所有 provider 调用的统一应用边界，`debug` 只负责呈现，`memory.transaction` 只负责轮次级持久化一致性。provider、提示构建和 TUI 不直接互相依赖。

## Complexity Tracking

无宪章例外。轮次暂存日志是规格间约束所需的最小额外复杂度：既有 001 BR-003 要求用户输入在生成前持久化，002 FR-028—FR-031 要求明确拒绝后不留下历史、pending 或墓碑，而 FR-032 要求普通 provider/网络失败保留可恢复输入。纯内存暂存不满足前者；先追加到不可变归档再截断会破坏归档语义并在跨分段/崩溃时产生不安全状态。因此采用单文件、`PREPARED`/`READY_PENDING`/`READY_TO_COMMIT` 三态可恢复日志，分别支持拒绝丢弃、单条 pending 前滚和完整轮次前滚，而不引入通用数据库或事件溯源系统；本说明不构成功能级宪章豁免。
