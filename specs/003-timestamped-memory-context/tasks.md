---

description: "智能历史时间段标注的依赖有序实现任务"
---

# Tasks: 智能历史时间段标注

**Input**: Design documents from `/specs/003-timestamped-memory-context/`

**Prerequisites**: `plan.md`、`spec.md`、`research.md`、`data-model.md`、`contracts/`、`quickstart.md`

**Tests**: BR-001—BR-010 的成功、边界和关键失败路径全部采用测试先行；来源完整性、DeepSeek 工具协议、配置原子重载、提示预算、调试等价、存储不改写和 10,000 项性能同样必须自动化验证。

**Organization**: 任务按四个用户故事的优先级组织。共享值对象、纯分段器和必需配置位于 Foundational；每个故事阶段先写并确认失败测试，再实现、同步对应 README/quickstart/合同、验证并创建原子提交。T018、T030、T034、T051、T058、T065 构成与计划一致的六个原子检查点，其中长期聊天记忆与 curation 在 US2 内分别对应 Memory/Prompt 和 Curation。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 仅表示显式前置完成后，可与同阶段其他任务修改不同文件并行执行。
- **[Story]**: 对应 `spec.md` 的 US1—US4；Setup、Foundational 和 Polish 不使用故事标签。
- 新增或确需修订的代码注释使用简体中文，并加入 `[2026-07-20]` 或实际实现日期/版本标记；仍准确的既有注释不得随意删除或改写。
- 测试只使用隔离临时目录和无效凭据占位符，不读取或改写维护者的 `data/`，不调用真实 Provider。

## Phase 1: Setup（共享基础设施）

**Purpose**: 增加跨平台 IANA 时区数据依赖和唯一默认配置；本阶段与 Foundational/US1 一起形成首个可用 MVP 提交。

- [X] T001 在 `pyproject.toml` 添加 `tzdata>=2026.3` 运行依赖并保持 Python `>=3.13,<3.15`、现有依赖范围和无第二套日期框架约束
- [X] T002 [P] 新增 `config/history_timestamps.toml`，写入 schema v1、`Asia/Shanghai`、30 分钟 gap、120 分钟 refresh 和启用跨日本地日期的 canonical default

---

## Phase 2: Foundational（阻塞所有用户故事）

**Purpose**: 固定统一时间项、策略、标记片段、严格配置及唯一 O(n) 分段算法；任何消费者接线前先证明共享规则。

**⚠️ CRITICAL**: T003—T006 必须先写并因缺失行为失败；T010 通过前不得开始任何用户故事实现。

### Tests for foundational contracts（先写并确认失败）

- [X] T003 [P] 在 `tests/unit/test_domain_models.py` 为 `TemporalTimeKind`、`TemporalSpan`、`TemporalLogEntry`、`TemporalMarker`、`AnnotatedFragment`、`AnnotatedHistory` 增加冻结、aware datetime、start/end、kind、重复 id、空来源和非法 span 的成功/失败测试并确认失败
- [X] T004 [P] 为 BR-001/BR-002/BR-003/BR-004/BR-006 在 `tests/unit/test_temporal_annotation.py` 增加空/单项、gap 小于/等于/大于阈值、范围重叠/相接、跨日开关、refresh 临界与重置、同刻、倒序、多原因合一、100 条密集消息、无尾 marker、三种固定中文格式和 DST offset 测试并确认失败
- [X] T005 [P] 为 BR-001/BR-006/BR-009 在 `tests/unit/test_temporal_annotation_properties.py` 增加输入顺序/body 不变、每项最多一个 marker、相同输入逐字确定、marker/body/separator spans 非重叠可回读和随机 aware 时间序列属性测试并确认失败
- [X] T006 [P] 为 BR-007 在 `tests/contract/test_history_timestamp_config.py` 增加 canonical default、必需/未知字段、bool 冒充 int、类型/范围/refresh 关系、非法 IANA 时区、ConfigAsset 路径/hash/revision 和缺失 manifest 测试并确认失败

### Foundational implementation

- [X] T007 在 `src/bai_agent/domain/models.py` 实现时间策略/语义/span/log entry/marker/fragment/history 不可变值对象并扩展 `PromptSegment` 的精确 fragment 来源能力，在 `src/bai_agent/domain/errors.py` 增加可操作 `TEMPORAL_ENTRY_INVALID` 错误，附 `[2026-07-20]` zh-CN 公共不变量注释
- [X] T008 在 `src/bai_agent/prompting/temporal.py` 实现无 I/O、保持顺序的 `annotate_history()`、UTC 瞬时边界判断、固定中文格式、显示时区转换和精确 fragment spans，并在 `src/bai_agent/prompting/__init__.py` 只导出稳定入口
- [X] T009 在 `src/bai_agent/config/validation.py` 实现 `history_timestamps.toml` 精确字段、严格类型、1—1440/1—10080 范围、refresh>=gap 和 `ZoneInfo` 校验，禁止代码默认或本地时区静默降级
- [X] T010 在 `src/bai_agent/config/loader.py` 将 `history_timestamps.toml` 加入必需 `MANIFESTS`，生成 `config:history_timestamps`/`history_timestamp_policy` 资产并纳入同一 `ConfigSnapshot.revision`

**Checkpoint**: 共享算法、配置合同和领域模型可独立测试；它们尚未单独宣称为用户可见完成项，将与 US1 接线和文档一起提交。

---

## Phase 3: User Story 1 - 从近期对话理解时间分段（Priority: P1）🎯 MVP

**Goal**: 让近期聊天按默认或已验证策略显示稀疏时间段 marker，同时保持历史角色/正文/顺序，排除当前输入和非日志指令，并把 marker 纳入预算与来源追踪。

**Independent Test**: 仅使用固定 `RawRecord.created_at` 的近期历史构建聊天提示；密集消息只显示首 marker，gap/refresh/跨日/倒序在正确项前标记，当前输入不标记，最终正文、来源 spans 和预算结果可逐字验证。

### Tests for User Story 1（先写并确认失败）

- [X] T011 [P] [US1] 为 BR-001/BR-010 在 `tests/contract/test_prompt_temporal_context.py` 固定 `recent_records` 的 `role: content` 原文、独立首 marker、空区块表示、当前输入/人格/状态排除和 RequestPart marker+body 来源合同并确认失败
- [X] T012 [P] [US1] 为 BR-002/BR-003/BR-004/BR-006 在 `tests/integration/test_temporal_chat_context.py` 增加密集、30 分钟临界、120 分钟刷新、跨日、同刻、倒序及 debug 关闭路径的近期聊天端到端请求测试并确认失败
- [X] T013 [P] [US1] 为 BR-008/BR-009 在 `tests/unit/test_temporal_prompt_budget.py` 增加 recent 字符边界包含 marker、恰好命中、超限明确失败、marker `UNTRUSTED_DATA`、配置+raw 双来源及伪装 marker 正文不提升信任测试并确认失败；overview 预算留给 US2/T021

### Implementation for User Story 1

- [X] T014 [US1] 在 `src/bai_agent/prompting/assembler.py` 将 recent `RawRecord` 适配为 EVENT 日志项并独立标注，保留 `role: content`、空区块和 current input 排除语义，只在最终 annotated recent text 上执行本阶段预算并生成非重叠 RequestPart spans；overview 标注与预算由 US2/T021/T026 实现
- [X] T015 [US1] 在 `src/bai_agent/application.py` 从同一 `ConfigSnapshot` 构造 `TemporalSegmentationPolicy`/annotator 并注入 `PromptAssembler`，确保聊天构建使用 persisted `RawRecord.created_at` 而非 prompt 构建时刻
- [X] T016 [P] [US1] 为 DR-001 更新 `README.md`，说明近期聊天的稀疏段首/gap/refresh/跨日行为、三个固定标签、默认配置入口、当前输入与人格排除以及 marker 不回写存储
- [X] T017 [P] [US1] 为 DR-002/DR-003/DR-005 更新 `specs/001-persistent-memory-agent/contracts/model-and-tools.md` 与 `specs/001-persistent-memory-agent/quickstart.md`，加入统一日志项/recent 合同、默认配置示例和密集/gap/refresh/跨日/倒序可执行验收
- [X] T018 [US1] 运行 `tests/unit/test_domain_models.py`、`tests/unit/test_temporal_annotation.py`、`tests/unit/test_temporal_annotation_properties.py`、`tests/contract/test_history_timestamp_config.py`、`tests/contract/test_prompt_temporal_context.py`、`tests/integration/test_temporal_chat_context.py`、`tests/unit/test_temporal_prompt_budget.py` 及现有 prompt/config 回归，执行 config validate、文档链接/示例检查和 `git diff --check`；在 `specs/003-timestamped-memory-context/tasks.md` 记录结果并通过 Git 扩展原子提交 Phase 1—3 的依赖、配置、共享模块、US1、测试与对应文档

**Checkpoint**: US1 是可独立演示的 MVP；近期聊天具有确定、稀疏、可追踪且预算完整的时间段，非日志内容不受影响。

---

## Phase 4: User Story 2 - 让长期和整理记忆保留真实时间语境（Priority: P2）

**Goal**: 用单次已验证 raw 快照为长期记忆和 coverage overview 派生真实来源范围，并让聊天与 curation 的三个历史区块复用统一分段；不把整理时间冒充事件时间，不写回存储。

**Independent Test**: 选择来源时间已知且顺序包含跳跃/倒退/重叠的长期记忆，分别构建聊天和整理提示；逐项验证 SOURCE_RANGE、正文/相关性/JSON 字段不变、三个 curation 区块独立首 marker，以及损坏来源在 provider 前失败。

### Tests for User Story 2（先写并确认失败）

- [ ] T019 [P] [US2] 为 BR-005/BR-006 在 `tests/unit/test_memory_temporal_projection.py` 增加 raw EVENT、长期记忆/overview 全来源 min/max、引用乱序/重复/重叠/相等端点、通用 RECORDED formatter、当前没有持久化 RECORDED 入口、现行 v1/未识别格式禁止降级及 missing/unreadable/hash/time invalid 失败测试并确认失败
- [ ] T020 [P] [US2] 为 BR-001/BR-002/BR-010 在 `tests/integration/test_temporal_chat_context.py` 增加 overview、long-term、recent 三个非空区块各自首 marker、相关性顺序保持、范围跳跃/倒序分段、非日志排除和单次 raw snapshot 读取测试并确认失败
- [ ] T021 [P] [US2] 为 BR-008 在 `tests/unit/test_temporal_prompt_budget.py` 增加 long-term 按既有相关性顺序计算 annotated 增量成本、marker 恰好占满预算、候选跳过稳定和 overview/long-term 超限不删 marker/来源测试并确认失败
- [ ] T022 [P] [US2] 为 BR-001/BR-005/BR-009/BR-010 在 `tests/integration/test_temporal_curation_context.py` 增加 batch_records/existing_memories/current_overview 独立标注、canonical JSON body 子串、重复正文绝对 span、metadata/output schema 排除、proposal schema 不变和来源失败 provider=0 测试并确认失败
- [ ] T023 [P] [US2] 为 FR-023/BR-005 在 `tests/integration/test_long_term_store.py` 增加既有合法 raw/YAML 无迁移可读、构建前后文件字节与 UTC 时间不变、last-valid 恢复后仍严格校验实际来源、未识别格式不得走 RECORDED 以及 marker 不持久化测试并确认失败

### Implementation for long-term chat memory

- [ ] T024 [US2] 新增 `src/bai_agent/memory/temporal.py`，用单个 immutable `raw_by_id` 投影 raw/long-term/coverage 为统一日志项，复用现有来源/hash/coverage 错误并对全部 refs 求 UTC min/max，禁止 N+1 archive 读取和损坏来源 RECORDED 降级
- [ ] T025 [US2] 在 `src/bai_agent/memory/selection.py` 保持相关性顺序，用统一 annotator 计算每个长期候选的精确 annotated 增量成本，超限沿用显式选择策略且不单独删除 marker、正文或来源
- [ ] T026 [US2] 在 `src/bai_agent/prompting/assembler.py` 将 `memory_overview`、`long_term_memories`、`recent_records` 作为三个独立 block 渲染，平移 marker/body spans 到最终 segment，并让范围相等仍使用 SOURCE_RANGE 模板
- [ ] T027 [US2] 在 `src/bai_agent/application.py` 和 `src/bai_agent/runtime/controller.py` 为一轮聊天复用同一次 raw/long-term 已验证视图，把完整 item/coverage 时间来源传给 selector/assembler，禁止只传丢失时间的裸字符串
- [ ] T028 [P] [US2] 为 DR-001 更新 `README.md` 的长期记忆/coverage 来源时间范围、相关性顺序、来源损坏失败和无存储迁移说明
- [ ] T029 [P] [US2] 为 DR-003/DR-004/DR-005 更新 `specs/001-persistent-memory-agent/contracts/model-and-tools.md`、`specs/001-persistent-memory-agent/contracts/storage.md` 与 `specs/001-persistent-memory-agent/quickstart.md`，同步 UTC 事实来源、动态范围、RECORDED 仅作未来显式版本适配扩展且当前无持久化适配器、last-valid 边界和聊天三 block 验收
- [ ] T030 [US2] 运行 `tests/unit/test_memory_temporal_projection.py`、`tests/integration/test_temporal_chat_context.py`、`tests/unit/test_temporal_prompt_budget.py`、`tests/integration/test_long_term_store.py` 及现有 archive/selection/coverage 回归，执行文档链接/默认值检查和 `git diff --check`；在 `specs/003-timestamped-memory-context/tasks.md` 记录结果并通过 Git 扩展原子提交 US2 的 Memory/Prompt 实现、测试和对应 README/001 文档

### Implementation for curation history

- [ ] T031 [US2] 在 `src/bai_agent/memory/curation.py` 用同一 raw index/annotator 分别渲染 batch_records、existing_memories、current_overview，以“marker 行 + 单项 canonical JSON 行”保留现有字段和 proposal parser，并在模板替换时直接累计最终绝对 spans
- [ ] T032 [P] [US2] 在 `config/prompts/memory_curation.md` 保留变量名、batch metadata、untrusted boundary 和 output schema，只调整三类历史变量的行式表示说明且不得把 marker 放入 JSON schema
- [ ] T033 [P] [US2] 为 DR-003/DR-005 更新 `README.md`、`specs/001-persistent-memory-agent/contracts/model-and-tools.md` 与 `specs/001-persistent-memory-agent/quickstart.md` 的 curation 三 block、JSON 不变、重复正文 provenance 和验收命令
- [ ] T034 [US2] 运行 `tests/integration/test_temporal_curation_context.py`、`tests/integration/test_curation_workflow.py`、`tests/integration/test_curation_transaction_proposal.py` 及现有 prompt provenance/estimation 回归，校验模板变量、canonical JSON、文档示例/链接和 `git diff --check`；在 `specs/003-timestamped-memory-context/tasks.md` 记录结果并通过 Git 扩展原子提交 US2 的 Curation 实现、测试、模板和对应文档

**Checkpoint**: US2 可独立证明聊天长期记忆与整理历史都使用真实来源范围；现有存储无需改写，损坏来源不会被记录时间掩盖。

---

## Phase 5: User Story 3 - 统一标注工具历史和未来日志类型（Priority: P3）

**Goal**: 为工具调用接受时刻和结果可发送完成时刻建立不落盘事件，跨 continuation round 重建一个工具历史 block；保持 DeepSeek assistant/tool 配对、结构化字段、来源/预算和调试真实性，并用统一合同支持未来消费者。

**Independent Test**: 把同一固定时间序列适配为聊天、curation 和工具历史，验证边界/标签逐字一致；工具 wire 仍是相邻 assistant(tool_calls)→tool，id/arguments/result body 不变，debug on/off 相等，`memory_source_query` 直接结果保持 golden contract。

### Tests for User Story 3（先写并确认失败）

- [ ] T035 [P] [US3] 为 BR-005 在 `tests/unit/test_model_call_models.py` 增加 tool-call `accepted_at`、tool-result `completed_at` aware/冻结/稳定 origin identity、DTO `model_dump()` 与 canonical result JSON 排除运行时时间的测试并确认失败
- [ ] T036 [P] [US3] 为 BR-001/BR-005/BR-010 在 `tests/contract/test_temporal_tool_protocol.py` 增加 marker-only assistant、单/多 tool calls、短/长执行、跨日、倒序、调用/结果分别分段、无新增 role、call id/name/arguments/order/tool_call_id 和 canonical body 逐字段不变测试并确认失败
- [ ] T037 [P] [US3] 为 BR-005/BR-006 在 `tests/integration/test_temporal_tool_continuation.py` 用 deterministic clock 覆盖成功 attempt 才采样 accepted_at、可发送结果后采样 completed_at、审批/重试不重采样、四轮整体重建不重复 marker 和失败时发送 0 并确认失败
- [ ] T038 [P] [US3] 为 BR-008/BR-009 在 `tests/integration/test_prompt_debug_equivalence.py` 与 `tests/unit/test_prompt_provenance.py` 增加 marker/body 同 pointer 非重叠 spans、配置+事件来源、无 whole-content 重叠、part token 守恒、固定 clock 下 debug on/off 最终 payload 逐字相同和篡改阻断测试并确认失败
- [ ] T039 [P] [US3] 为 FR-025/BR-010 扩展 `tests/contract/test_memory_source_tool.py` 的 direct golden 输入/分页/权限/错误/返回 JSON 不变测试，并新增它作为外层 tool history body 时去掉 marker 后逐字相等的失败断言；确认新增断言失败但既有 golden 仍通过，且不得修改 `src/bai_agent/tools/memory_source.py`
- [ ] T040 [P] [US3] 为 BR-006/BR-010 在 `tests/contract/test_prompt_temporal_context.py` 增加相同统一日志项经 chat/curation/tool/future fake consumer 得到相同边界/标签、七个现有 block 全清单和非日志排除合同测试并确认失败

### Implementation for User Story 3

- [ ] T041 [US3] 在 `src/bai_agent/domain/models.py` 实现不落盘 `ToolHistoryEvent` 和稳定 origin identity，并为 `CompletionResult`/`ToolResult` 增加排除序列化的 aware 接受/完成时间；在 `src/bai_agent/domain/ports.py` 复用/扩展可注入 `Clock` 合同
- [ ] T042 [US3] 在 `src/bai_agent/model_calls/gateway.py` 仅在 provider response 解析校验成功并被接受后为 tool-call batch 采样一次 accepted_at，失败 attempt、retry backoff 和 debug approval 不产生或重采样事件
- [ ] T043 [P] [US3] 在 `src/bai_agent/tools/executor.py` 于参数/权限/大小/安全/事务处理完成且形成可发送 `ToolResult` 后采样一次 completed_at，保持 ToolResult canonical JSON schema 不含时间元数据
- [ ] T044 [US3] 在 `src/bai_agent/runtime/controller.py` 保存当前轮未标注 tool events，每次 continuation 从整个 block 重建 assistant/tool message content 与 marker/body parts，保持结构化 call 字段、紧邻配对和原 body 连续子串
- [ ] T045 [US3] 在 `src/bai_agent/providers/deepseek.py` 使用 controller 提供的 content fragments，只有缺失时才创建 whole-content fallback；为 tool_calls 保留原始 origin 来源并消除重叠 part/当前 draft 误归因
- [ ] T046 [US3] 在 `src/bai_agent/model_calls/provenance.py` 拒绝同一 pointer 的 included text spans 重叠/越界/未覆盖，在 `src/bai_agent/model_calls/estimation.py` 让最终 marker 只计费一次并保持 part+protocol overhead 守恒
- [ ] T047 [US3] 在 `src/bai_agent/application.py` 向 gateway、executor 和 controller 注入同一策略 annotator 与可测试 clock，确保配置在任何工具副作用前已完整校验
- [ ] T048 [P] [US3] 为 DR-001 更新 `README.md`，列全七个现有日志 block、工具接受/完成时间、未来消费者接入规则、调试所见即所发及 `memory_source_query` 直接合同不变边界
- [ ] T049 [P] [US3] 为 DR-003/DR-005 更新 `specs/001-persistent-memory-agent/contracts/model-and-tools.md` 与 `specs/001-persistent-memory-agent/quickstart.md`，同步工具 metadata envelope、assistant/tool 协议、外层 marker 与来源工具排除验收
- [ ] T050 [P] [US3] 更新 `specs/002-prompt-trace-debugger/contracts/model-call.md` 与 `specs/002-prompt-trace-debugger/quickstart.md`，记录 marker/body non-overlap spans、最终预算、stable origin、debug on/off 等价和 DeepSeek whole-content fallback 限制
- [ ] T051 [US3] 运行 `tests/unit/test_model_call_models.py`、`tests/contract/test_temporal_tool_protocol.py`、`tests/integration/test_temporal_tool_continuation.py`、`tests/integration/test_prompt_debug_equivalence.py`、`tests/unit/test_prompt_provenance.py`、`tests/contract/test_memory_source_tool.py`、`tests/contract/test_prompt_temporal_context.py` 及现有 DeepSeek/tool/gateway/estimation 回归，执行七 block 清单、文档链接和 `git diff --check`；在 `specs/003-timestamped-memory-context/tasks.md` 记录结果并通过 Git 扩展原子提交 US3 的 Tools/Provider 实现、测试和 README/001/002 文档

**Checkpoint**: US3 可独立证明所有当前日志消费者共享规则、工具协议/来源/预算不被破坏，且来源追溯工具实现和直接返回完全不变。

---

## Phase 6: User Story 4 - 安全调整时间显示策略（Priority: P4）

**Goal**: 让独立配置的有效修改在下一完整 reload 边界同时作用于所有消费者；缺失或无效配置在 raw 写入、工具执行和 provider 请求前以可操作错误原子失败。

**Independent Test**: 将 gap 从 30 改为 60 分钟并用同一 45 分钟历史重复构建，验证下一轮所有消费者整体改变；随后注入缺失、unknown、bool/int、越界、refresh<gap 和非法时区，断言无混合策略且 provider/raw/tool 调用均为 0。

### Tests for User Story 4（先写并确认失败）

- [ ] T052 [P] [US4] 为 BR-007 在 `tests/integration/test_temporal_config_reload.py` 增加 30→60 分钟下一轮生效、显示时区/跨日整体变化、同一构建冻结旧 snapshot、无效 reload 无 partial objects/provider/raw/tool 副作用以及修复后恢复测试并确认失败
- [ ] T053 [P] [US4] 在 `tests/contract/test_cli_config.py` 与 `tests/integration/test_packaging.py` 增加独立 manifest 缺失/错误的可操作 path+field 诊断、config validate、tzdata/IANA 后备和安装制品默认配置可达性测试并确认失败
- [ ] T054 [P] [US4] 为 BR-006/BR-007/BR-010 在 `tests/integration/test_full_acceptance.py` 增加 chat/long-term/overview/curation/tool 同一 config revision、有效变更全体一致、非日志不受影响和原始 UTC/文件字节不变测试并确认失败

### Implementation for User Story 4

- [ ] T055 [US4] 在 `src/bai_agent/application.py` 的 build/reload 路径先完整加载并验证 snapshot、policy、annotator、assembler、curation、gateway/executor/controller 后再整体替换引用，任何失败保持旧对象不可见且发生在 raw/工具/provider 边界前
- [ ] T056 [US4] 在 `pyproject.toml` 与 `src/bai_agent/config/loader.py` 校准 tzdata 和默认 `config/history_timestamps.toml` 的安装/发现行为，使 Ubuntu 与 Windows 使用同一 IANA 配置且不依赖本机 locale/固定 offset
- [ ] T057 [P] [US4] 为 DR-001/DR-002 更新 `README.md`、`specs/001-persistent-memory-agent/contracts/configuration.md` 与 `specs/001-persistent-memory-agent/quickstart.md`，写明字段单位/范围/关系、IANA/tzdata、reload 边界、无效配置失败、恢复步骤和无凭据示例
- [ ] T058 [US4] 运行 `tests/contract/test_history_timestamp_config.py`、`tests/integration/test_temporal_config_reload.py`、`tests/contract/test_cli_config.py`、`tests/integration/test_packaging.py`、`tests/integration/test_full_acceptance.py` 及现有 persona/config reload 回归，在 Ubuntu/Windows 夹具验证固定 instant 输出，执行 config validate、文档默认值/链接和 `git diff --check`；在 `specs/003-timestamped-memory-context/tasks.md` 记录结果并通过 Git 扩展原子提交 US4 的配置重载/打包、测试和 README/001 文档

**Checkpoint**: 四个用户故事均可独立验收；策略配置集中、严格、跨平台且在完整快照边界原子生效。

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: 完成性能、全消费者覆盖、兼容性、安全、注释和文档一致性门禁；不替代各故事阶段已经要求的文档同步。

- [ ] T059 [P] 为 BR-006/SC-008 在 `tests/performance/test_temporal_annotation_scale.py` 增加 10,000 个预构造日志项预热后 `<1.0s`、输出验真和相同输入 100 次逐字一致测试，并增加 10,000 raw+1,000 long-term 单次索引/O(raw+refs) 无 N+1 断言
- [ ] T060 [P] 为 BR-010/SC-002/SC-007 在 `tests/integration/test_full_acceptance.py` 汇总七个 block 的 100% 接入、block 状态隔离、未来 fake consumer、非日志排除、顺序/正文/协议保持和存储 no-write 验收
- [ ] T061 [P] 扩展 `tests/integration/test_repository_secret_safety.py` 与 `tests/security_scanner.py`，确认时间配置、marker 来源、错误、debug payload、测试夹具、工作树和可达 Git 历史不含可用凭据或新增持久 prompt trace
- [ ] T062 审核 `src/bai_agent/` 本功能新增/更新注释全部为带日期/版本的简体中文并说明模块边界、时间真实性、配置原子性和工具协议不变量；仅在失真时修改既有注释，并在 `specs/003-timestamped-memory-context/tasks.md` 记录修正或 N/A 理由
- [ ] T063 为 DR-001—DR-005/SC-009 逐项核对 `README.md`、`specs/001-persistent-memory-agent/{quickstart.md,contracts/configuration.md,contracts/model-and-tools.md,contracts/storage.md}`、`specs/002-prompt-trace-debugger/{quickstart.md,contracts/model-call.md}` 与 `specs/003-timestamped-memory-context/{spec.md,plan.md,research.md,data-model.md,contracts/,quickstart.md,tasks.md}` 的默认值、七 block、示例、链接、UTC/迁移、来源工具排除和验证命令一致性
- [ ] T064 检查 `.github/workflows/compatibility.yml` 是否已在 Ubuntu 24.04/Python 3.13/3.14 与 Windows 功能矩阵运行新增非性能测试；缺失时在该文件补齐，已覆盖时在 `specs/003-timestamped-memory-context/tasks.md` 记录 `N/A` 理由，不把 1 秒强制性能门禁迁移到 Windows
- [ ] T065 按 `specs/003-timestamped-memory-context/quickstart.md` 执行全部定向命令、`pytest -m "not performance" -q`、`pytest tests/performance/test_temporal_annotation_scale.py -q -s`、适用现有 performance/fault-injection 回归、配置/文档链接/占位符/凭据扫描和 `git diff --check`；在 `specs/003-timestamped-memory-context/tasks.md` 记录 FR-001—FR-026、BR-001—BR-010、DR-001—DR-005、SC-001—SC-009 闭环并通过 Git 扩展原子提交最终测试、注释、workflow（如需）和文档审计变更

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 可立即开始；与 Foundation/US1 合并为首个用户可见原子提交。
- **Foundational (Phase 2)**: 依赖 Setup；T003—T006 测试先行，阻塞所有用户故事。
- **US1 (Phase 3)**: 依赖 Foundational；T018 通过后交付近期聊天 MVP。
- **US2 (Phase 4)**: 依赖 Foundational；实现上可与 US1 分支并行，但因共同修改 assembler/application，主执行顺序采用 US1→US2。T030 与 T034 分别是 Memory/Prompt 和 Curation 原子检查点。
- **US3 (Phase 5)**: 依赖 Foundational 和 US1 的 fragment/request-part 接线；工具核心可与 US2 并行，七 block/文档/完整消费者检查点依赖 US2 完成。
- **US4 (Phase 6)**: 依赖 US1—US3，以便证明一次 reload 同时作用于所有现有消费者。
- **Polish (Phase 7)**: 依赖所需的四个故事全部完成。

### User Story Dependency Graph

```text
Setup -> Foundational temporal/config contracts -> US1 recent-chat MVP
                                             ├-> US2 long-term -> US2 curation
                                             └-> US3 tool-history core
US2 complete + US3 complete --------------------> US4 atomic reload -> Polish
```

### Within Each Phase

1. 完成该阶段全部测试任务，并确认因目标行为尚未实现而失败。
2. 按领域值对象/适配器 → assembler/service → application/controller/provider 顺序实现。
3. 更新同一重大修改影响的 README、quickstart、配置、模型提示和存储合同。
4. 运行阶段测试、既有回归、文档命令/链接、凭据扫描（适用时）和 `git diff --check`。
5. 只在代码、测试和对应文档全部通过后，通过 Git 扩展创建本阶段原子提交。

## Parallel Opportunities

- **Foundational**: T003—T006 可按四个测试文件并行；T008 与 T009 可在 T007 合同稳定后并行，T010 依赖 T009。
- **US1**: T011—T013 可并行；T016/T017 可在实际输出稳定后并行准备，T018 统一验证。
- **US2**: T019—T023 可并行写失败测试；T024/T025/T026 顺序形成 memory pipeline，T028/T029 可并行；完成 T030 后，T031/T032 可分文件推进，T033 可并行同步文档。
- **US3**: T035—T040 可按测试文件并行；T042/T043 可在 T041 后并行，T045/T046 可在 T044 消息映射稳定后分文件并行，T048—T050 可并行同步三组文档。
- **US4**: T052—T054 可并行；T057 可在 T055/T056 行为稳定后并行准备，T058 统一验证。
- **Polish**: T059—T061 可并行；T063/T064 可在实现冻结后并行，T065 最终收敛。

## Parallel Execution Examples

### User Story 1

```text
并行测试：T011 || T012 || T013
实现顺序：T014 -> T015 -> (T016 || T017) -> T018
```

### User Story 2

```text
并行测试：T019 || T020 || T021 || T022 || T023
Memory/Prompt：T024 -> T025 -> T026 -> T027 -> (T028 || T029) -> T030
Curation：T031 || T032 -> T033 -> T034
```

### User Story 3

```text
并行测试：T035 || T036 || T037 || T038 || T039 || T040
实现顺序：T041 -> (T042 || T043) -> T044 -> (T045 || T046) -> T047 -> (T048 || T049 || T050) -> T051
```

### User Story 4

```text
并行测试：T052 || T053 || T054
实现顺序：T055 -> T056 -> T057 -> T058
```

## Implementation Strategy

### MVP First（US1 only）

1. 完成 Phase 1 Setup 和 Phase 2 Foundational，并保留失败优先测试证据。
2. 完成 US1 的 recent chat 接线、预算、来源和非日志排除。
3. 在 T018 同步 README/001 文档、运行验证并创建首个原子提交。
4. **STOP AND VALIDATE**：仅用固定近期记录即可演示密集/gap/refresh/跨日/倒序行为，无需长期记忆或工具。

### Incremental Delivery

1. **MVP**: Setup + Foundation + US1 → 近期聊天具有稀疏时间段。
2. **Memory/Prompt**: US2 第一检查点 → 长期记忆与 overview 使用真实来源范围。
3. **Curation**: US2 第二检查点 → 三个整理历史 block 统一标注。
4. **Tools/Provider**: US3 → 工具事件、协议、来源、预算、调试与未来消费者合同闭环。
5. **Configuration**: US4 → 所有消费者在原子 reload 上共享策略。
6. **Final**: 10k 性能、全消费者/存储/凭据/注释/文档/跨平台门禁。

## Core Business Rule Coverage

| Rule | Primary test tasks | Implementation tasks |
|---|---|---|
| BR-001 | T004, T005, T011, T020, T022, T036 | T008, T014, T026, T031, T044 |
| BR-002 | T004, T012, T020 | T008, T014, T026 |
| BR-003 | T004, T012 | T008, T014 |
| BR-004 | T004, T012 | T008, T014 |
| BR-005 | T019, T022, T023, T035—T037 | T024, T031, T041—T044 |
| BR-006 | T004, T005, T019, T037, T040, T054, T059 | T008, T024, T044, T055 |
| BR-007 | T006, T052—T054 | T009, T010, T015, T047, T055, T056 |
| BR-008 | T013, T021, T038 | T014, T025, T026, T031, T046 |
| BR-009 | T005, T013, T022, T038 | T007, T008, T026, T031, T045, T046 |
| BR-010 | T011, T020, T022, T036, T039, T040, T054, T060 | T014, T026, T031, T044, T047, T055 |

## Notes

- `[P]` 不允许跳过显式依赖、并发修改同一文件或绕过测试先行。
- `src/bai_agent/tools/memory_source.py` 明确不在修改清单；只允许 T039 对其现有公开合同做回归验证。
- marker 是 prompt 构建期数据元数据，不得写入 raw、long-term YAML、ToolResult schema、日志或事务 journal。
- 当前 schema v1 必须继续严格要求 `source_refs`；不要为不存在的旧 schema 放宽校验。`RECORDED` 只保留为未来另行定义具体 schema/version 的适配器扩展能力，当前版本没有持久化 `RECORDED` 入口。
- 每个检查点提交前必须同步并验证该阶段受影响文档；最终 Polish 不能替代 T016/T017、T028/T029、T033、T048—T050 或 T057。

## Implementation Evidence

- **T018 / 2026-07-20**: Foundation + US1 定向及既有 prompt/config 回归共 79 项通过；独立 `config validate` 使用无效占位凭据通过；12 份受影响 Markdown 的本地相对链接检查为 0 个断链，默认值/固定标签搜索一致，`git diff --check` 通过。
