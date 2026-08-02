# Implementation Plan: 面向模型历史上下文的智能时间段标注

**Branch**: `003-timestamped-memory-context` | **Date**: 2026-07-20 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/003-timestamped-memory-context/spec.md`

## Summary

为所有当前模型可见的日志类上下文增加一套稀疏、确定且可复用的时间段标注：近期聊天、长期记忆、覆盖概览、记忆整理输入和当前轮工具历史先由各自适配器提供有序正文、可信来源和事件点/来源范围，再交给无存储依赖的统一时间模块，按段首、长间隔、跨本地日期、长段刷新和时间倒退规则生成固定中文标记。每个逻辑区块独立分段；标记在预算、来源追踪和 provider 物化前进入最终正文，但不回写存储、不改变工具协议，也不修改长期记忆来源追溯工具。

## Technical Context

**Language/Version**: Python `>=3.13,<3.15`

**Primary Dependencies**: 保留现有 `filelock`、`openai`、`pydantic`、`ruamel.yaml`、`textual`；时间运算使用标准库 `datetime`/`zoneinfo`，新增 `tzdata>=2026.3` 作为 Windows 等缺少系统 IANA 时区数据库的平台后备，不引入日期时间框架或后台服务。

**Storage**: 继续使用 JSONL 原始记录和 YAML 长期记忆；原始 UTC 时间、正文和来源关系不迁移、不改写，生成的时间标记不持久化。仅新增受版本控制的 `config/history_timestamps.toml`，并将其作为既有原子 `ConfigSnapshot` 的必需资产加载。

**Testing**: `pytest`、`pytest-asyncio`、`hypothesis`、`respx`；用注入时钟和固定 aware datetime 覆盖纯算法、配置、记忆来源投影、提示预算/来源、整理模板、工具续接及 DeepSeek wire contract；性能测试验证 10,000 个日志项在主要平台上 1 秒内完成标注。

**Core Business Logic**: BR-001 以项数、顺序、角色、正文子串和工具协议逐字段不变测试覆盖；BR-002 以首项、`gap == threshold`、低于阈值、跨日、倒序和重叠范围测试覆盖；BR-003 以刷新恰好命中、重置及无尾标记测试覆盖；BR-004 以多条件合并和 100 条密集记录测试覆盖；BR-005 以聊天点时间、工具接受/完成时间、长期记忆来源最小/最大范围、通用 `RECORDED` 格式合同、当前无持久化适配器及声明来源损坏失败测试覆盖；BR-006 以固定三种中文格式、时区转换、DST 重复本地时刻和相同输入逐字一致测试覆盖；BR-007 以配置快照原子重载、无效配置发送前失败且无 raw/provider 副作用测试覆盖；BR-008 以最终文本字符预算、provider estimator 和互不重叠来源 span 守恒测试覆盖；BR-009 以 10,000 项性能、单次 raw 索引和无 N+1 归档读取测试覆盖；BR-010 以消费者清单、区块状态隔离、非日志内容排除和 `memory_source_query` 黄金合同测试覆盖。每条规则均安排成功、边界与关键失败路径。

**Comment Impact**: 在统一时间算法入口、来源范围解析、工具事件采样点、原子配置组装和协议不变量处新增或更新简体中文注释，并使用 `[2026-07-20]`（或实际实现日期/版本）标记；仅在原注释因数据流改变而过时时修改，禁止删除仍有效说明。

**Sensitive Credentials**: 本功能不新增凭据种类或流向。DeepSeek Key 仍只由既有环境变量路径注入；时间标记只格式化可信时间，不复制正文、工具参数或凭据。提示调试、错误和测试夹具继续执行现有凭据门禁并只使用无效占位值。

**Git Milestones**: 六个可独立验证的原子阶段，与 `tasks.md` 检查点一致：(1) Foundation + US1 MVP：时区依赖、独立配置、领域值对象、纯分段器和近期聊天接入；(2) Memory/Prompt：长期记忆、overview、选择预算与主聊天提示接入；(3) Curation：三类整理历史区块及精确来源接入；(4) Tools/Provider：可注入时钟、工具事件桥、DeepSeek 协议与来源去重；(5) Configuration：所有消费者的原子 reload、打包与跨平台时区验证；(6) Final：全消费者验收、10k 性能、README/001/002/003 文档一致性和最终安全审计。每阶段的实现、测试和受影响文档在验证后同一提交。

**Documentation Impact**: 更新 `README.md`（覆盖范围、默认稀疏规则、配置入口及来源工具不变）；更新 `specs/001-persistent-memory-agent/quickstart.md` 与 `contracts/{configuration,model-and-tools,storage}.md`（配置字段、提示合同、UTC/来源范围/无迁移语义）；更新 `specs/002-prompt-trace-debugger/quickstart.md` 与 `contracts/model-call.md`（marker/body span、预算和调试所见即所发）；维护本功能的 `research.md`、`data-model.md`、`contracts/`、`quickstart.md`。通过相对链接检查、默认值/消费者清单搜索、配置/合同/集成/性能测试验证，并与对应重大代码变更同一提交。`.github/workflows/compatibility.yml` 仅在新增命令未被现有 Ubuntu/Windows pytest 步骤覆盖时同步，否则在实现审计记录 N/A 理由。

**Target Platform**: 原生 Ubuntu 24.04、Python 3.12/3.13/3.14 为主要支持环境，1 秒性能门禁固定在 Python 3.13；Windows 11/PowerShell 为次要功能兼容平台并验证 `tzdata` 后备；macOS 延续现有项目范围，不纳入本功能支持。

**Project Type**: 单体 Python CLI 应用，沿用领域模型、应用编排、端口/适配器和文件存储分层。

**Performance Goals**: 对 10,000 个有序时间化日志项的标注在 Ubuntu 24.04/Python 3.13 单次运行 1 秒内完成；长期记忆/overview 时间投影按 `raw + source_refs` 线性处理并复用一次 raw 快照；不得实质回退现有启动、TUI 和 provider 请求门禁。

**Constraints**: 固定三种中文标记模板且完整显示日期、分钟和 UTC 偏移；所有边界先用完整 aware datetime/UTC 瞬时计算再格式化；每个非空逻辑区块重置状态并有首标记；不排序、不合并、不删除、不改正文；工具 `assistant(tool_calls) -> tool` 配对、调用标识和 JSON body 保持不变；最终含标记正文先接受预算、来源和凭据校验；无效时间/配置 fail closed；不逐项读取 archive；不修改 `memory_source_query`；不持久化展示标记。

**Scale/Scope**: 单用户、单写者、本地进程；验收数据最多 10,000 条 raw、1,000 条长期记忆、单轮最多 4 次工具续接；现有八类时间化逻辑区块（含 `current_input`）全部接入，同一策略可供未来日志消费者通过适配 `TemporalLogEntry` 复用；共享 visible boundary renderer 覆盖全部不可信逻辑数据块。

## Constitution Check

*GATE: Phase 0 前检查及 Phase 1 设计后复核均通过。*

| Gate | Required Evidence | Status |
|------|-------------------|--------|
| I. Clarity, extensibility, maintainability | 通用分段器、记忆时间投影、消费者渲染、配置装配和 provider wire mapping 职责明确；未来日志只需适配统一项 | PASS |
| II. Decoupling and readability | `prompting.temporal` 不读取存储、模板或 provider；消费者单向依赖纯模块，单份不可变策略无共享可变状态 | PASS |
| III. Simplest understandable implementation | 使用标准 datetime/zoneinfo、一个纯模块和薄适配层；仅因 Windows IANA 数据增加 `tzdata`，不引入数据库、服务或日期框架 | PASS |
| IV. zh-CN traceable comments | `Comment Impact` 已明确核心入口/不变量的简体中文注释和 `[2026-07-20]` 追踪规则 | PASS |
| V. Git discipline | `Git Milestones` 定义与 `tasks.md` 一致的六个带验证和文档同步的原子提交边界 | PASS |
| VI. Core business tests | `Core Business Logic` 将 BR-001—BR-010 全部映射到成功、边界和关键失败自动化测试 | PASS |
| VII. Credential protection | 不新增凭据，标记不复制内容，既有环境注入、凭据门禁和无效夹具策略保持 | PASS |
| VIII. Major-update documentation sync | `Documentation Impact` 列出 README、001/002/003 合同与 quickstart 的内容、验证和同提交要求 | PASS |

**Phase 1 post-design re-check**: [data-model.md](./data-model.md) 已区分持久事实、瞬态时间项、标记片段和工具事件；[temporal-annotation.md](./contracts/temporal-annotation.md)、[configuration.md](./contracts/configuration.md) 与 [prompt-consumers.md](./contracts/prompt-consumers.md) 已固定算法、配置原子性、消费者边界、协议/预算/来源不变量；[quickstart.md](./quickstart.md) 给出 BR-001—BR-010 的验证入口。设计没有放宽长期记忆来源完整性，也没有修改来源追溯工具，八项继续为 PASS。

## Project Structure

### Documentation (this feature)

```text
specs/003-timestamped-memory-context/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── configuration.md
│   ├── prompt-consumers.md
│   └── temporal-annotation.md
└── tasks.md                       # Phase 2 由 /speckit-tasks 创建，本计划不创建
```

### Source Code (repository root)

```text
config/
├── history_timestamps.toml        # 唯一时间段策略配置
└── prompts/
    └── memory_curation.md         # 三个整理历史区块的行式表示

src/bai_agent/
├── application.py                # 从单一快照组装/重载 annotator 并注入消费者
├── config/
│   ├── loader.py                 # 必需 manifest、ConfigAsset 与 revision
│   └── validation.py             # 严格字段、范围、关系和 IANA 时区校验
├── domain/
│   ├── models.py                 # 时间值对象、片段与不落盘工具事件
│   └── errors.py                 # 可操作的时间/配置错误码
├── memory/
│   ├── temporal.py               # raw 索引和派生记忆来源范围适配
│   ├── selection.py              # 含标记的增量预算选择
│   └── curation.py               # batch/existing/overview 独立标注与模板 span
├── model_calls/
│   └── gateway.py                # 成功 CompletionResult 的 accepted_at 采样
├── prompting/
│   ├── temporal.py               # 无存储依赖的纯分段/格式化实现
│   └── assembler.py              # overview/long-term/recent 渲染及细粒度来源
├── providers/
│   └── deepseek.py               # 保持 wire 协议并消除重叠 whole-content part
├── runtime/
│   └── controller.py             # 当前轮工具事件累计、重建及消息/span 映射
└── tools/
    └── executor.py               # 可发送 ToolResult 完成时刻采样

tests/
├── contract/
│   ├── test_history_timestamp_config.py
│   ├── test_prompt_temporal_context.py
│   ├── test_temporal_tool_protocol.py
│   └── test_memory_source_tool.py       # 原合同回归，不改工具实现
├── unit/
│   ├── test_temporal_annotation.py
│   ├── test_temporal_annotation_properties.py
│   ├── test_memory_temporal_projection.py
│   └── test_temporal_prompt_budget.py
├── integration/
│   ├── test_temporal_chat_context.py
│   ├── test_temporal_curation_context.py
│   ├── test_temporal_config_reload.py
│   ├── test_temporal_tool_continuation.py
│   └── test_prompt_debug_equivalence.py
└── performance/
    └── test_temporal_annotation_scale.py
```

**Structure Decision**: 延续单体包结构。`prompting.temporal` 是唯一规则实现，接收完全构造好的不可变日志项；`memory.temporal` 只把已验证 raw/长期记忆/coverage 投影成该合同；assembler、curation 和 controller 只负责各自的正文与协议形态；application 负责同一配置快照的装配。这个依赖方向让未来消费者复用算法而无需依赖存储或 DeepSeek。

## Complexity Tracking

无宪章例外。一个纯时间模块、一个记忆投影适配器和消费者侧薄桥接是满足“所有历史构建模块统一复用”、精确来源 span 与工具协议不变的最小结构；`tzdata` 仅补齐受支持 Windows 平台的 IANA 数据，不引入第二套时间实现。现有长期记忆 schema v1 强制非空 `source_refs`，因此不为不存在的旧 schema 放宽完整性或创建迁移；通用合同保留显式 `RECORDED` 语义，但当前版本没有相应持久化入口，未来必须通过独立功能明确 schema/version 后才能接入。
