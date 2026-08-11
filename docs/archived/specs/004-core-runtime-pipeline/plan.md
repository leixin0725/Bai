# Implementation Plan: 核心运行时与消息管道（迁移阶段 0、1）

**Branch**: `004-core-runtime-pipeline` | **Date**: 2026-08-08 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/004-core-runtime-pipeline/spec.md`

## Summary

在现有单用户 CLI 上引入进程内**串行消息处理管道**：对话输入、定时事件与系统事件统一进入同一处理流程，同一时刻只处理一个对话动作，其余输入等待；输入合并只针对"一次输入动作"（一次管道输入的整批内容、或终端缓冲区中已连片到达的多行），不引入时间窗口；配置沿用现有"每轮整份快照原子重载"并补充**分组校验与错误合同**；新增**最小后台执行器**（提交、串行执行、状态记录，无优先级/取消/持久化）；补齐**运行状态查看**与**优雅启动/停止**。阶段 0 两处标记已核对落盘，无需代码改动。整体不引入事件总线、任务持久化、并行请求限制或请求优先级。

## Technical Context

**Language/Version**: Python `>=3.12,<3.15`（与 `pyproject.toml` 一致）。

**Primary Dependencies**: 不新增运行时依赖。管道、输入合并与生命周期使用标准库 `asyncio`；继续使用现有 `filelock`、`openai`、`pydantic`、`ruamel.yaml`、`textual`、`tzdata`。

**Storage**: 不改变数据布局、不迁移既有数据。管道、任务与运行状态均为进程内瞬态，不落盘；对话轮次继续写入 JSONL 原始记录与 YAML 长期记忆，未完成轮次继续走既有 pending 协议。

**Testing**: `pytest` + `pytest-asyncio`（`asyncio_mode=auto`）+ `hypothesis` + `respx`；复用 `tests/fakes.py` 的 `FakeProvider`、`DeterministicClock` 与隔离数据根夹具。新增单元/契约/集成测试覆盖管道串行与防重入、输入合并、最小执行器状态机、优雅退出、状态快照与配置分组错误合同。

**Core Business Logic**（BR → 测试映射）:

- BR-001 恰好处理一次：管道分发 + 控制器事务路径；单元测试覆盖成功、失败、重试不重复回复/不重复写入，契约测试覆盖 CLI 管道输入。
- BR-002 会话防重入：单 worker 串行执行；单元测试并发提交多条输入，验证处理串行且顺序一致。
- BR-003 配置原子生效：沿用现有快照重载测试，新增分组错误定位与"非法时保持旧快照、运行不中断"测试；测试必须断言失败重载后捕获输出中出现"分组 + 字段 + 旧 revision"的提示、`:status` 显示 `health=warning` 且 `last_reload.ok=false`，修复后恢复 `ok`。
- BR-004 后台任务状态机：执行器单元测试覆盖 等待→执行→成功/失败 与进程内积压。
- BR-005 优雅退出：生命周期测试覆盖队列非空、pending 存在/不存在时停止，重启后数据完整、pending 按既有策略处理。
- BR-006 一次输入动作合并：输入读取器测试覆盖管道 EOF 整批、终端缓冲连片多行、逐行输入三种场景。
- BR-007 统计与状态一致：状态快照测试验证会话状态、队列深度、任务与健康度同真实状态一致。

**Comment Impact**: 新增或更新的简体中文注释集中在管道入口、输入合并判定、执行器状态机、生命周期停止路径、配置重载错误合同，并使用 `[2026-08-08]` 标记；仅更新因数据流改变而过时的旧注释，不删除仍有效的说明。

**Sensitive Credentials**: 本功能不新增凭据种类或流向。DeepSeek Key 仍只由环境变量注入；管道、状态与错误输出不包含正文、工具参数或凭据；既有凭据门禁、泄露事件流程与无效测试夹具保持不变。

**Git Milestones**: 六个可独立验证的原子提交（与 `tasks.md` 检查点一一对齐，每阶段实现、测试与受影响文档同提交）：

1. **US1 管道与生命周期**：新增串行处理管道与运行时外壳，`chat` 切换到持久事件循环；优雅停止、pending 语义与退出码保持既有契约；BR-001/002/005 测试 + README/quickstart/CLI 契约同步。
2. **US2 配置分组与重载可见性**：配置分组表与 `config validate` 分组输出、重载状态接入 `:status`；重载失败显式 stderr 警告（分组/字段/原因/旧 revision）且 `:status` 一致；BR-003 测试 + 契约同步。
3. **US3 一次输入动作合并**：stdin 输入读取器（管道 EOF 整批、终端缓冲连片合并、零等待）；BR-006 测试 + 契约同步。
4. **US4 最小后台执行器与事件投递**：后台执行器（提交/串行/状态）；定时事件、系统事件进入管道的投递入口与测试处理钩子；BR-004 测试 + 契约同步。
5. **US5 运行状态查看**：`:status` 会话内状态命令；BR-007 测试 + 契约同步。
6. **US6 + 收尾**：阶段 0 两处标记核对记录、全部文档（README、quickstart、三份契约、迁移计划）一致性校验与全量回归、凭据/注释审计。

**Documentation Impact**:

- `README.md`：新增运行模型（串行管道、输入合并、`:status`、优雅停止、配置分组热重载）与验证命令。
- `specs/004-core-runtime-pipeline/quickstart.md`：本功能端到端验证路径。
- `specs/001-persistent-memory-agent/contracts/cli.md`：`chat` 循环、管道输入语义、`:status` 与退出码。
- `specs/001-persistent-memory-agent/contracts/configuration.md`：配置分组表、重载语义与错误合同。
- `specs/001-persistent-memory-agent/quickstart.md`：聊天与退出章节按新行为更新。
- `../../Scriptor_to_Bai_migration/migration-plan.md`：已完成阶段 0/1 范围同步（2026-08-08 提交）。
- `docs/ubuntu-deployment.md`：仅当启动/停止命令变化时同步，否则记录 N/A。

**Target Platform**: 原生 Ubuntu 24.04/WSL、Python 3.12/3.13/3.14 是唯一支持环境；原生 Windows 与 macOS 不在支持范围。

**Project Type**: 单体 Python CLI 应用；在现有领域模型、应用装配与端口/适配器分层上新增运行时外壳。

**Performance Goals**: 管道分发每个处理项的额外开销低于 10ms；状态快照为 O(1)；输入合并为零等待（仅基于 stdin 缓冲区非空判定，不引入时间阈值）。不设置独立的性能门禁，纳入现有回归。

**Constraints**:

- 不引入事件总线、发布/订阅框架、外部消息中间件、任务持久化、取消、优先级或并行请求限制。
- 配置重载保持**整份快照原子切换**：分组用于校验与错误定位；任一分组非法时整次重载失败，整体保持最后有效快照继续运行，不实现跨组部分合并（避免半新半旧的不一致状态——解释 A 已由用户确认，解释 B 明确不做）；**失败必须显式提示，禁止静默回退**。
- 单写者锁、pending 协议、原始记录/长期记忆数据布局与既有 CLI 命令契约不变。
- 提示词调试 TUI 的 TTY 预检、逐请求审批与发送门禁不变，继续在持久事件循环内工作。
- stdin 非 TTY（管道）时整批内容为一次输入动作；TTY 下按缓冲区连片判定合并，不等待、不按时间窗口。
- 定时事件本阶段只提供投递入口与测试钩子；完整调度、持久化与自主执行属于阶段 7。

**Scale/Scope**: 单用户、单会话、本地进程；队列深度上限 100（超限时新输入等待并报告，不丢数据）；后台任务为进程内短任务，无持久化。

## Constitution Check

*GATE: 阶段 0 前检查及阶段 1 设计后复核均通过。*

| Gate | Required Evidence | Status |
|------|-------------------|--------|
| I. Clarity, extensibility, maintainability | 管道、输入读取、执行器、状态、配置重载职责独立；后续阶段 7 调度器与阶段 8 钩子只需在既有入口上扩展 | PASS |
| II. Decoupling and readability | 运行时外壳单向调用现有 `AgentApplication`/`SingleTurnController`；领域 DTO 放 `domain`，管道/执行器放 `runtime`，不反向依赖 CLI 与存储实现 | PASS |
| III. Simplest understandable implementation | 单 worker 串行管道 + 标准库 asyncio + 零等待缓冲判定；不建事件总线、不持久化任务、不实现优先级/取消 | PASS |
| IV. zh-CN traceable comments | `Comment Impact` 已明确核心入口注释与 `[2026-08-08]` 追踪规则 | PASS |
| V. Git discipline | `Git Milestones` 定义四个带验证和文档同步的原子提交边界 | PASS |
| VI. Core business tests | BR-001—BR-007 全部映射到成功、边界与关键失败自动化测试 | PASS |
| VII. Credential protection | 不新增凭据；状态与错误输出不暴露正文/参数/凭据，既有门禁保持不变 | PASS |
| VIII. Major-update documentation sync | `Documentation Impact` 列出受影响文档、所需内容、验证方式与同提交要求 | PASS |

**Phase 1 post-design re-check**: [data-model.md](./data-model.md) 与 [contracts/](./contracts/) 已固定管道项、会话状态、任务状态机、输入合并边界、配置分组错误合同与 `:status` 输出；没有放宽 pending 协议、凭据门禁或数据完整性约束；八项继续为 PASS。

## Project Structure

### Documentation (this feature)

```text
specs/004-core-runtime-pipeline/
├── plan.md                  # 本文件
├── research.md              # Phase 0 输出
├── data-model.md            # Phase 1 输出
├── quickstart.md            # Phase 1 输出
├── contracts/
│   ├── runtime-pipeline.md  # 管道/输入合并/执行器/状态/生命周期契约
│   ├── configuration.md     # 配置分组与重载错误合同
│   └── cli.md               # chat 循环、:status 与退出码
└── tasks.md                 # Phase 2 由任务清单创建
```

### Source Code (repository root)

```text
src/bai_agent/
├── domain/
│   └── models.py             # 新增 PipelineItem、TaskRecord、RuntimeStatus 等 DTO（扩展现有 FrozenModel）
├── runtime/
│   ├── __init__.py
│   ├── pipeline.py           # 串行处理管道：单 worker、FIFO、事件投递入口、停止语义
│   ├── executor.py           # 最小后台执行器：提交、串行执行、状态记录
│   ├── input_reader.py       # stdin 输入读取与一次输入动作合并（管道 EOF/缓冲连片）
│   └── shell.py              # 运行时外壳：装配管道、生命周期（启动/停止）、状态快照
├── application.py            # 仅补充 reload 状态记录与分组错误信息，不改变装配职责
└── cli.py                    # chat 改用 shell；新增 :status 会话内命令

tests/
├── unit/                     # 新增：pipeline、executor、input_reader、status、config groups
├── contract/                 # 新增：cli chat 管道输入、:status、优雅停止、事件顺序
└── integration/              # 新增：全流程验收（管道+生命周期+状态+配置重载）
```

**Structure Decision**: 保持现有单包布局，新增职责放入 `runtime/` 包（管道、执行器、输入读取、外壳），DTO 放入 `domain/models.py`；`cli.py` 只做参数解析与外壳启动，`application.py` 继续负责应用装配。这是对现有分层的最小扩展，不引入新的顶层包或服务。

## Complexity Tracking

无宪章违规，无需复杂度例外；本计划已记录 BR-003 的"整份快照原子重载"解释边界（见 Constraints），解释 A 已由用户确认，逐组生效（解释 B）明确不做。
