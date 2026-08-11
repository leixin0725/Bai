# Tasks: 核心运行时与消息管道（迁移阶段 0、1）

**Input**: Design documents from `/specs/004-core-runtime-pipeline/`

**Prerequisites**: plan.md (required)、spec.md (required)、research.md、data-model.md、contracts/

**Tests**: 每条核心业务规则（BR-001~BR-007）都有强制自动化测试；测试任务必须先于对应实现任务编写（先红后绿）。

**Organization**: 按用户故事分组，每个故事可独立实现、独立测试、独立验收。

**Constitution Gates**: 任务保持 I→II→III 设计优先级；新增/更新 zh-CN 注释带 `[2026-08-08]`；不引入凭据；每个重大修改在对应阶段检查点前完成受影响文档同步，并与实现同原子提交。

## Phase 1: Setup（基线核对）

**Purpose**: 确认干净起点与配置分组基线

- [x] T001 运行全量回归 `python -m pytest`，确认当前基线通过并记录结果（tests/）
- [x] T002 [P] 核对 `config/` 六个 TOML 清单与 `personas/`、`prompts/` 到 `src/bai_agent/config/loader.py`、`validation.py` 现有校验函数的映射，产出配置分组清单基线（供 US2 使用）

## Phase 2: Foundational（阻塞性前置）

**Purpose**: 所有用户故事共用的领域 DTO 与测试设施

**⚠️ CRITICAL**: 本阶段未完成前不得开始任何用户故事

- [x] T003 [P] 在 `src/bai_agent/domain/models.py` 新增 `PipelineItemKind`、`PipelineItem`、`InputBoundary`、`ConversationAction`、`TaskStatus`、`BackgroundTaskRecord`、`SessionState`、`HealthState`、`ReloadStatus`、`RuntimeStatus`（沿用 `FrozenModel`，含校验与 `[2026-08-08]` 注释）
- [x] T004 [P] 新增 `tests/unit/test_runtime_models.py`：DTO 字段校验、TaskStatus/RuntimeStatus 状态转换不变式
- [x] T005 [P] 扩展 `tests/fakes.py`：`FakeInputSource`（可注入缓冲连片与 EOF）、事件处理记录器、`FakeApplication`、可注入时钟

**Checkpoint**: Foundation ready - 用户故事实现可以开始

## Phase 3: User Story 1 - 全天持续运行的核心会话 (Priority: P1) 🎯 MVP

**Goal**: 统一串行消息处理管道：对话/定时/系统输入按到达顺序进入同一流程，单 worker 防重入；优雅启动/停止，pending 语义与退出码不变。

**Independent Test**: 用 `FakeApplication` 并发提交多个 chat/event 处理项，验证串行、顺序、恰好一次、防重入；在队列非空与 pending 存在时停止，验证数据完整与退出码。

### Tests for User Story 1 (REQUIRED) ⚠️

- [x] T006 [P] [US1] BR-001/BR-002 单元测试 `tests/unit/test_pipeline.py`：串行、顺序、防重入、恰好一次、失败不中断
- [x] T007 [P] [US1] BR-005 契约测试 `tests/contract/test_chat_lifecycle.py`：队列非空/pending 存在时优雅停止、退出码 130/0、重启后数据完整且 pending 按既有策略处理

### Implementation for User Story 1

- [x] T008 [US1] 实现串行管道 `src/bai_agent/runtime/pipeline.py`：FIFO、单 worker、`submit(chat_input|timer_event|system_event)`、`stop()`、顺序可观测
- [x] T009 [US1] 实现运行时外壳 `src/bai_agent/runtime/shell.py`：生命周期（SIGINT/SIGTERM/二次 SIGINT）、释放写锁、最小状态快照
- [x] T010 [US1] 改造 `src/bai_agent/cli.py` 的 `chat` 切换为 shell 驱动，保留 `--resume-pending`/`--discard-pending`/`--debug-prompts` 语义
- [x] T011 [US1] 契约测试 `tests/contract/test_cli_chat_shell.py`：真实 CLI 启动、恢复/丢弃 pending、Ctrl+C 退出码
- [x] T012 [US1] 更新 `README.md`：统一消息处理管道、`chat` 循环与优雅停止行为
- [x] T013 [US1] 更新 `specs/001-persistent-memory-agent/contracts/cli.md` 的 `chat` 启动/退出码章节与 `specs/004-core-runtime-pipeline/contracts/cli.md` 第 1/3 节
- [x] T014 [US1] 更新 `specs/001-persistent-memory-agent/quickstart.md` 聊天章节与 `specs/004-core-runtime-pipeline/quickstart.md` 第 4 节
- [x] T015 [US1] 检查点（Milestone 1 原子提交）：全量回归通过，`README.md`、`specs/001-persistent-memory-agent/{quickstart.md,contracts/cli.md}` 与 `specs/004-core-runtime-pipeline/{quickstart.md,contracts/cli.md}` 一致，提交只含 US1 相关文件

## Phase 4: User Story 2 - 不改配置就不必重启 (Priority: P1)

**Goal**: 配置分组校验与错误定位；整份快照原子重载；重载失败在下一动作开始前显式 stderr 警告（分组/字段/原因/旧 revision），禁止静默回退；`:status` 的 `last_reload` 与警告一致。

**Independent Test**: 运行中把 `history_timestamps.toml` 改为非法值并发送消息：终端立即出现警告、系统按旧快照继续；`config validate` 输出八组 `ok`；修复后恢复。

### Tests for User Story 2 (REQUIRED) ⚠️

- [x] T016 [P] [US2] BR-003 契约测试 `tests/contract/test_config_reload_visibility.py`：失败重载后捕获 stderr 输出断言包含"分组 + 字段 + 旧 revision"，shell 状态快照显示 `health=warning`、`last_reload.ok=false`，修复后恢复 `ok`（CLI `:status` 的一致性断言由 T037 覆盖）
- [x] T017 [P] [US2] 单元测试 `tests/unit/test_config_groups.py`：分组清单与校验函数映射、错误消息分组定位、整份快照保持

### Implementation for User Story 2

- [x] T018 [US2] 在 `src/bai_agent/config/loader.py` 增加分组状态输出，`config validate` 返回 `groups` 字段
- [x] T019 [US2] 在 `src/bai_agent/application.py` 记录每次重载结果（revision/ok/error/分组定位），供状态快照使用
- [x] T020 [US2] 在 `src/bai_agent/runtime/shell.py` 于动作开始前输出重载失败 stderr 警告，并同步 `RuntimeStatus.last_reload`
- [x] T021 [US2] 更新 `specs/004-core-runtime-pipeline/contracts/configuration.md` 第 2/3 节、`contracts/runtime-pipeline.md` 第 7 节、`contracts/cli.md` 警告行为
- [x] T022 [US2] 更新 `specs/004-core-runtime-pipeline/quickstart.md` 第 3 节与 `README.md` 配置章节
- [x] T023 [US2] 检查点（Milestone 2 原子提交）：BR-003 测试全绿，`contracts/configuration.md`、`contracts/runtime-pipeline.md`、`contracts/cli.md`、`quickstart.md`、`README.md` 一致，提交只含 US2 相关文件

## Phase 5: User Story 3 - 一次粘贴不再拆成多轮 (Priority: P1)

**Goal**: stdin 非 TTY 整批内容为一次输入动作；TTY 缓冲连片多行合并为一次动作；零等待、无时间窗口、不截断。

**Independent Test**: 用 `FakeInputSource` 分别模拟管道 EOF、TTY 缓冲连片、逐行三种输入，断言生成的 `ConversationAction` 数量与内容；CLI 管道多行只产生一次处理。

### Tests for User Story 3 (REQUIRED) ⚠️

- [x] T024 [P] [US3] BR-006 单元测试 `tests/unit/test_input_reader.py`：管道 EOF 整批、TTY 缓冲连片、逐行、不截断、零等待（无 sleep/时间阈值）
- [x] T025 [P] [US3] 契约测试 `tests/contract/test_cli_chat_input_merge.py`：`printf` 多行管道输入后 `memory validate` 的 `raw_records` 增量恰为 2

### Implementation for User Story 3

- [x] T026 [US3] 实现输入读取器 `src/bai_agent/runtime/input_reader.py`：零等待缓冲非空判定；Windows 无等效路径时按契约降级为逐行并注释说明
- [x] T027 [US3] 在 `src/bai_agent/runtime/shell.py` 接入输入读取器，把动作提交为 `chat_input` 处理项
- [x] T028 [US3] 更新 `specs/004-core-runtime-pipeline/contracts/cli.md` 第 2 节、`quickstart.md` 第 1 节与 `README.md` 输入章节
- [x] T029 [US3] 检查点（Milestone 3 原子提交）：BR-006 测试全绿，`contracts/cli.md`、`quickstart.md`、`README.md` 一致，提交只含 US3 相关文件

## Phase 6: User Story 4 - 后台任务看得见、排得上 (Priority: P2)

**Goal**: 最小后台执行器（提交、串行执行、等待/执行/成功/失败状态记录）；定时事件与系统事件经管道投递。

**Independent Test**: 提交多个任务并制造失败任务，验证按提交顺序串行、状态可查、失败保留原因；向管道投递事件验证顺序与失败不中断。

### Tests for User Story 4 (REQUIRED) ⚠️

- [x] T030 [P] [US4] BR-004 单元测试 `tests/unit/test_executor.py`：等待→执行→成功/失败状态机、串行、失败保留原因、进程内积压
- [x] T031 [P] [US4] 契约测试 `tests/contract/test_runtime_events.py`：timer/system 事件经管道按序投递、事件失败不中断后续处理

### Implementation for User Story 4

- [x] T032 [US4] 实现最小执行器 `src/bai_agent/runtime/executor.py`：`submit(name, coro)`、串行执行、`BackgroundTaskRecord` 记录
- [x] T033 [US4] 在 `src/bai_agent/runtime/shell.py` 接入执行器与事件处理注册入口（默认空，供阶段 7 使用）
- [x] T034 [US4] 更新 `specs/004-core-runtime-pipeline/contracts/runtime-pipeline.md` 第 3/4 节与 `data-model.md` 第 3 节
- [x] T035 [US4] 检查点（Milestone 4 原子提交）：BR-004 测试全绿，`contracts/runtime-pipeline.md`、`data-model.md` 一致，提交只含 US4 相关文件

## Phase 7: User Story 5 - 系统健康一眼可知 (Priority: P3)

**Goal**: `chat` 会话内 `:status` 输出 `RuntimeStatus` 稳定 JSON（会话状态、队列、任务、健康度、最近重载、计数）。

**Independent Test**: 交互输入 `:status`，断言输出字段与真实状态一致、不写入 raw 记录、不含正文/凭据。

### Tests for User Story 5 (REQUIRED) ⚠️

- [x] T036 [P] [US5] BR-007 单元测试 `tests/unit/test_status_snapshot.py`：快照与真实状态一致、统计在事件完成后更新、无重复计数
- [x] T037 [P] [US5] 契约测试 `tests/contract/test_cli_status.py`：`:status` 字段完整性、不写 raw、输出排序稳定 JSON；复用 US2 的重载失败注入场景，验证 CLI 输出与 `last_reload`/`health` 及终端警告一致

### Implementation for User Story 5

- [x] T038 [US5] 实现 `:status` 拦截与输出（`src/bai_agent/cli.py`/`src/bai_agent/runtime/shell.py`），精确匹配、其余 `:` 开头输入按正文处理
- [x] T039 [US5] 更新 `specs/004-core-runtime-pipeline/contracts/cli.md` 第 4 节、`quickstart.md` 第 2 节与 `README.md`
- [x] T040 [US5] 检查点（Milestone 5 原子提交）：BR-007 测试全绿，`contracts/cli.md`、`quickstart.md`、`README.md` 一致，提交只含 US5 相关文件

## Phase 8: User Story 6 - 迁移准备状态可核对 (Priority: P3)

**Goal**: 阶段 0 两处标记核对结论落盘（无代码改动）。

**Independent Test**: 对照 `../../docs/archived/Scriptor_to_Bai_migration/archived/feature-checklist.md` 第 4/12 节与 `../../docs/archived/Scriptor_to_Bai_migration/archived/future-and-discarded.md` 第 4 节原文。

- [x] T041 [US6] 核对两处标记并更新 `specs/004-core-runtime-pipeline/quickstart.md` 与 `../../docs/archived/Scriptor_to_Bai_migration/migration-plan.md` 的阶段 0 核对记录
- [x] T042 [US6] 检查点：`quickstart.md` 与 `../../docs/archived/Scriptor_to_Bai_migration/migration-plan.md` 的阶段 0 核对记录一致，与 Polish 阶段共同原子提交（Milestone 6 包含）

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: 全量验收、文档一致性与安全/注释/平台审计

- [x] T043 [P] 全量回归与命令验证：`pytest`、`config validate`、`memory validate`、`doctor`（使用 `.tmp/` 隔离数据根）
- [x] T044 [P] 跨文档一致性校验：README、specs/001 quickstart 与 contracts、本功能 7 份文档、迁移计划中的命令/路径/链接/默认值
- [x] T045 [P] 凭据与安全审计：新增警告与状态输出不含正文/工具参数/凭据；不新增凭据流向
- [x] T046 [P] 注释审计：新增/更新 zh-CN 注释均带 `[2026-08-08]` 标记
- [x] T047 [P] Windows 次要兼容验收：输入读取降级路径与 `.github/workflows/compatibility.yml` 覆盖（未新增 CLI 命令/CI 步骤，工作流无需变更，N/A 已记录）
- [x] T048 [P] 更新 `../../docs/archived/Scriptor_to_Bai_migration/migration-plan.md` 阶段 1 验收状态为可核对的完成记录并链接本规格（阶段 0 核对记录由 T041 完成）
- [x] T049 最终原子提交（Milestone 6 收尾）：包含 US6 核对记录与全部 Polish 文档（`README.md`、迁移计划、本功能全部文档与 `src/`、`tests/` 变更）

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**：无依赖
- **Foundational (Phase 2)**：依赖 Setup；阻塞全部用户故事
- **US1 (Phase 3)**：依赖 Foundational；无其他故事依赖
- **US2~US5 (Phase 4-7)**：均依赖 US1（运行时外壳）；按 P1→P2→P3 顺序串行
- **US6 (Phase 8)**：仅文档核对，可与 US5 并行
- **Polish (Phase 9)**：依赖全部用户故事

### User Story Dependencies

- **US1 (P1)**：Foundational 完成后即可开始（MVP）
- **US2 (P1)**：依赖 US1 外壳（警告与 `last_reload` 需状态快照）
- **US3 (P1)**：依赖 US1 外壳（输入读取器向管道提交）
- **US4 (P2)**：依赖 US1 外壳（执行器挂接管道/状态）
- **US5 (P3)**：依赖 US1/US2/US4 的状态来源
- **US6 (P3)**：无代码依赖，可与 US5 并行

### Within Each User Story

- 核心业务测试先写并失败 → 实现 → 测试转绿
- 文档同步任务在本阶段检查点前完成并与实现同原子提交

## Parallel Opportunities

- Phase 1/2 中标记 [P] 的任务可并行
- 每个用户故事内部的测试任务 [P] 可并行编写
- US6 可与 US5 并行
- Polish 中 T043~T048 可并行

## Parallel Example: User Story 1

```bash
# 并行编写 US1 两组测试：
Task: "BR-001/BR-002 单元测试 tests/unit/test_pipeline.py"
Task: "BR-005 契约测试 tests/contract/test_chat_lifecycle.py"

# 实现完成后并行文档任务：
Task: "更新 README.md 运行模型章节"
Task: "更新 specs/001 contracts/cli.md 与 004 contracts/cli.md"
Task: "更新 specs/001 quickstart.md 与 004 quickstart.md"
```

## Implementation Strategy

### MVP First（US1 单独交付）

1. Phase 1 Setup
2. Phase 2 Foundational（阻塞）
3. Phase 3 US1 完成并独立验收 → Milestone 1 原子提交

### Incremental Delivery

1. US1 → 独立测试 → Milestone 1 提交（MVP）
2. US2 → 独立测试 → Milestone 2 提交
3. US3 → 独立测试 → Milestone 3 提交
4. US4 → 独立测试 → Milestone 4 提交
5. US5 → 独立测试 → Milestone 5 提交
6. US6 + Polish → 全量回归 → Milestone 6 提交

## Notes

- [P] 任务 = 不同文件、无依赖，可并行
- 测试先于实现（先红后绿）；每个里程碑提交前全量回归通过
- 文档与对应重大实现同原子提交，最终 Polish 不替代里程碑内文档同步
- 不引入凭据；新注释使用简体中文并带 `[2026-08-08]` 标记
- BR-003 按已确认的解释 A：整份快照原子重载、失败显式提示、禁止静默回退
