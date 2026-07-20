---

description: "提示词追踪调试工具的依赖有序实现任务（analyze 修订版）"
---

# Tasks: 提示词追踪调试工具

**Input**: Design documents from `/specs/002-prompt-trace-debugger/`

**Prerequisites**: `plan.md`、`spec.md`、`research.md`、`data-model.md`、`contracts/`、`quickstart.md`

**Tests**: BR-001—BR-010 的正常、边界和关键失败路径均必须先写自动化测试并确认在实现前失败。凭据门禁、provider 扩展、模型迁移、Linux 性能和兼容性验收同样采用测试先行。

**Organization**: 任务按用户故事优先级组织；跨故事的事务、安全和 provider 调用边界放入 Foundational 或最早需要它们的 US1，确保公开 `--debug-prompts` 前已具备无痕拒绝、凭据保护、失败恢复和全调用覆盖。每个重大阶段在检查点前同步文档、执行验证，并通过已安装的 Git 扩展创建原子提交。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 仅表示前置条件完成后可修改不同文件并行工作。
- **[Story]**: 对应 `spec.md` 用户故事；Setup、Foundational 和 Polish 不使用故事标签。
- 新增或确需修订的代码注释使用简体中文并加入 `[2026-07-20]` 或实际实现日期/版本标记；既有注释只在过时、错误或误导时更新。
- 所有凭据示例必须是明确无效的占位符；真实 Key 不得进入源码、测试、日志、文档、任务制品或 Git 历史。

## Phase 1: Setup（共享基础设施）

**Purpose**: 引入唯一的新运行时依赖并建立模块目录；与 Phase 2 合并为第一个重大修改提交。

- [X] T001 在 `pyproject.toml` 增加 Textual `>=8.2,<9` 兼容范围，确认 Python 3.13/3.14 支持且不引入第二套 TUI 框架；若要求可复现锁定则在同文件记录项目采用范围约束而非虚构 lockfile
- [X] T002 [P] 创建 `src/bai_agent/model_calls/__init__.py` 与 `src/bai_agent/debug/__init__.py`，只导出稳定公共入口并以 `[2026-07-20]` zh-CN 模块注释说明职责边界

---

## Phase 2: Foundational（阻塞所有用户故事）

**Purpose**: 实现所有公开调试入口依赖的配置资产、三态轮次事务、失败转 pending、凭据门禁和写工具事务保护；设计冲突已在本次任务生成前完成修订。

**⚠️ CRITICAL**: T003—T012 的失败优先测试完成后才能实现；T026 检查点通过前不得开始 TUI、CLI 或 provider 网关公开接线。

### Tests for foundational contracts（先写并确认失败）

- [X] T003 [P] 在 `tests/unit/test_config_validation.py` 增加 debug color/阈值、context window、output cap、estimator registry 的正常/缺失/类型错误/越界测试并确认失败
- [X] T004 [P] 在 `tests/unit/test_config_assets.py` 增加 `ConfigAsset` 相对路径、UTF-8 摘要、统一 revision、文件后改不冒充旧来源及路径逃逸失败测试并确认失败
- [X] T005 [P] 在 `tests/unit/test_model_call_models.py` 增加来源、part、draft、PreparedProviderRequest、MaterializedSendPayload、call+attempt+materialized digest approval、无 prompt/part/SourceRef 的 ActualUsageSummary 及 PREPARED/READY_PENDING/READY_TO_COMMIT 值对象冻结、校验和摘要测试并确认失败
- [X] T006 [P] 在 `tests/contract/test_model_call_ports.py` 固定 `ProviderAdapter.prepare/materialize_sdk_kwargs/send_once`、`ApprovalPresenter`、`TokenEstimator`、`ModelCallGateway` 与 `TurnUnitOfWork` 最小端口签名，并断言每 attempt 只 materialize 一次后确认失败
- [X] T007 [P] 为 BR-008/BR-009 在 `tests/unit/test_turn_transaction.py` 增加 ABSENT→PREPARED→READY_PENDING/READY_TO_COMMIT→ABSENT、明确拒绝 discard、两种 READY 分别幂等前滚、非法状态和基线冲突测试并确认失败
- [X] T008 [P] 为 BR-008 在 `tests/fault_injection/test_turn_transaction_recovery.py` 覆盖 PREPARED/READY_PENDING/READY_TO_COMMIT 临时写、fsync、replace、raw 单/双记录、跨 segment、manifest、long-term、last-valid 和 cleanup 中断恢复并确认失败
- [X] T009 [P] 为 BR-009 在 `tests/integration/test_turn_transaction_pending.py` 增加 retry exhausted/non-retryable provider failure/网络中断将暂存 USER 经 READY_PENDING 幂等发布为且仅为一条既有 pending、重启可 `--resume-pending`，而明确 reject 不形成 pending 的测试并确认失败
- [X] T010 [P] 在 `tests/integration/test_curation_transaction_proposal.py` 增加 curation 只产生 proposal、整轮 READY_TO_COMMIT 前不写 `long_term.yaml`、reject 与 READY_PENDING 丢弃 proposal、READY_TO_COMMIT 发布来源索引的测试并确认失败
- [X] T011 [P] 为 BR-010 在 `tests/contract/test_tool_transaction_capabilities.py` 审计当前工具全部只读，增加只读工具允许执行、声明 prepare/commit/rollback 或明确补偿契约的 fake 写工具可暂存、能力缺失/不实或 prepare 失败时副作用调用为 0 的测试并确认失败
- [X] T012 [P] 为 BR-007 与 CR-001、CR-002、CR-003 在 `tests/unit/test_prompt_credential_guard.py` 增加认证/payload 分离、显示前和发送前命中、脱敏错误、日志/journal 禁区及安全事件门禁测试并确认失败

### Foundational implementation

- [X] T013 在 `src/bai_agent/domain/models.py` 实现 ConfigAsset、SourceRef、RequestPart、ModelCallDraft、PreparedProviderRequest、不可变 MaterializedSendPayload、call+attempt+digest approval、无 prompt/part/SourceRef 的 ActualUsageSummary、PreTurnCheckpoint、TurnWorkingSet 和三态 transaction 值对象，并加入 `[2026-07-20]` zh-CN 不变量注释
- [X] T014 在 `src/bai_agent/domain/ports.py` 实现 provider `prepare()`/唯一 `materialize_sdk_kwargs()`/`send_once()`、批准展示、token 估算、网关、事务和写工具恢复/补偿能力端口，在 `src/bai_agent/domain/errors.py` 增加脱敏领域错误码
- [X] T015 在 `src/bai_agent/config/loader.py` 将 persona、prompt、state、tools、agent 和 provider 配置加载为保留项目相对路径/hash/revision 的 ConfigAsset
- [X] T016 在 `src/bai_agent/config/validation.py` 实现 `[debug_prompt]`、模型容量、最大输出上限和 estimator id 校验，禁止静默截断、路径逃逸及用本地输入预算冒充模型容量
- [X] T017 [P] 在 `config/agent.toml` 增加 color、高/危占用阈值和估算安全裕度，保持调试启用状态不落配置
- [X] T018 [P] 在 `config/providers.toml` 增加 provider output cap、estimator id 和 profile context window 能力字段，本阶段不迁移生产模型 id
- [X] T019 在 `src/bai_agent/memory/transaction.py` 实现私有原子 journal、PREPARED reject discard、READY_PENDING 发布 USER pending、READY_TO_COMMIT 发布完整轮次和 fail-closed 恢复，并以 zh-CN 日期注释记录不可逆边界
- [X] T020 [P] 在 `src/bai_agent/memory/archive.py` 实现以 turn/record id 与 checkpoint hash 校验的幂等 `append_pending_user()` 和 `append_complete_turn()`，保证跨 segment 恢复不重复
- [X] T021 [P] 在 `src/bai_agent/memory/long_term.py` 实现按基线 revision/hash 或已达目标身份判断的幂等目标文档发布，冲突时不覆盖人工修改
- [X] T022 [P] 在 `src/bai_agent/memory/curation.py` 将整理拆为无写入 `propose()` 与仅由 READY_TO_COMMIT 发布路径调用的 commit，proposal 保留完整来源索引
- [X] T023 [P] 在 `src/bai_agent/tools/registry.py`、`src/bai_agent/tools/executor.py` 与 `config/tools.toml` 声明并审计当前工具全部只读，实现未来写工具可恢复 prepare/commit/rollback 或明确补偿能力门禁，能力缺失/不实或 prepare 失败时在副作用前失败
- [X] T024 [P] 在 `src/bai_agent/security/redaction.py` 和 `src/bai_agent/security/incidents.py` 实现 prompt payload 显示前/发送前凭据检测、无原值错误及既有安全事件门禁复用
- [X] T025 为 DR-003/DR-004 更新 `specs/001-persistent-memory-agent/contracts/configuration.md`、`specs/001-persistent-memory-agent/contracts/storage.md` 与 `specs/001-persistent-memory-agent/contracts/model-and-tools.md`，同步三态事务、失败转 pending、工具副作用能力、配置资产和凭据边界
- [X] T026 运行 `pytest tests/unit/test_config_validation.py tests/unit/test_config_assets.py tests/unit/test_model_call_models.py tests/contract/test_model_call_ports.py tests/unit/test_turn_transaction.py tests/fault_injection/test_turn_transaction_recovery.py tests/integration/test_turn_transaction_pending.py tests/integration/test_curation_transaction_proposal.py tests/contract/test_tool_transaction_capabilities.py tests/unit/test_prompt_credential_guard.py -q`、配置校验、文档链接/示例检查与 `git diff --check`，随后通过 Git 扩展原子提交 Phase 1—2 的 `pyproject.toml`、`config/`、`src/bai_agent/{config,domain,memory,security,tools,model_calls,debug}/`、测试和对应 `specs/` 文档

**Checkpoint**: 在任何公开调试入口出现前，拒绝、普通失败、崩溃恢复、凭据和写工具副作用已有失败优先测试及可恢复基础。

---

## Phase 3: User Story 1 - 检查最终提示词及来源（Priority: P1）🎯 MVP

**Goal**: 以唯一网关覆盖所有现有 provider 调用；单次聊天在网络发送前显示唯一 materialization 后的完整请求和真实来源，明确批准后先关闭正文 TUI 再发送同一不可变载荷，拒绝任一调用可安全撤销整轮。

**Independent Test**: 用带唯一标记的人格、状态、模板、长期记忆、短期记录和当前输入构建一轮；核对 TUI、SDK materialization 和 mock HTTP JSON 逐字段一致，所有当前调用方与 retry 都经过网关，批准前发送 0，任一批准点 reject 后状态等于 checkpoint。

### Tests for User Story 1（先写并确认失败）

- [X] T027 [P] [US1] 为 BR-001 在 `tests/contract/test_prompt_trace_provider.py` 增加 prepared request、每 attempt 唯一 `materialize_sdk_kwargs()` 输出与 respx HTTP JSON 的逐字段真实性测试，覆盖空正文、多行、简体中文、Emoji、控制字符、超长内容和工具定义
- [X] T028 [P] [US1] 为 BR-002 在 `tests/unit/test_prompt_provenance.py` 增加 JSON Pointer/span、重复正文不猜来源、多文件/数百记录聚合、运行时 producer、配置后改身份、excluded/empty/unknown_source 及失败阻断测试
- [X] T029 [P] [US1] 为 BR-006/FR-026 在 `tests/contract/test_model_call_gateway.py` 增加唯一 materialization 后 call+attempt+materialized digest 批准绑定、嵌套突变失效、debug on/off payload 相等和批准不改写请求测试
- [X] T030 [P] [US1] 为 FR-024 在 `tests/contract/test_provider_adapter_portability.py` 增加第二个 fake provider 仅实现 prepare/materialize_sdk_kwargs/send_once 即通过来源校验、估算 unavailable、批准、retry、usage 和脱敏错误的完整网关测试
- [X] T031 [P] [US1] 为 BR-003 在 `tests/integration/test_model_call_gateway_coverage.py` 增加 curation、chat、tool continuation、future persona 与 retry 所有物理 attempt 恰有一个批准项，并用禁止直接 provider.complete/SDK 调用的 spy 验证无旁路
- [X] T032 [P] [US1] 为 BR-007 在 `tests/unit/test_prompt_trace_lifecycle.py` 增加 approve 后且网络发送前 TUI 的 prompt/part/SourceRef 引用清除、sender 仅持有不可变 materialized payload、send_once 成功/失败 finally 释放、无持久 trace 和 ActualUsageSummary 不持有或恢复原文测试
- [X] T033 [P] [US1] 为 BR-008 在 `tests/integration/test_prompt_trace_rejection.py` 覆盖首个 chat、curation 后 chat、tool continuation 和 retry 批准点 reject，断言当前请求发送 0、已发辅助响应/派生结果丢弃、无 pending/tombstone 且下一轮等于 checkpoint
- [X] T034 [P] [US1] 在 `tests/contract/test_prompt_approval_tui.py` 用 Textual Pilot 增加完整正文/来源滚动、A/R、正文未完成 mounted 时禁止批准、80x24/窄宽度/resize 和终端内容安全转义测试
- [X] T035 [P] [US1] 在 `tests/integration/test_prompt_trace_single_call.py` 增加 ConfigAsset、记忆选择、prompt assembly、TUI、transaction 到 fake provider 的单次端到端来源/顺序/批准/拒绝验收

### Implementation for User Story 1

- [X] T036 [US1] 在 `src/bai_agent/model_calls/provenance.py` 实现 canonical JSON、payload SHA-256、materialized JSON digest、JSON Pointer/span 回读、included 覆盖和 ordered SourceRef 完整性校验
- [X] T037 [US1] 在 `src/bai_agent/prompting/assembler.py` 保留 included/excluded/empty、稳定 part id、trust、真实 ConfigAsset 与运行时来源，不把构建结果降为无法归因的裸字符串
- [X] T038 [P] [US1] 在 `src/bai_agent/memory/archive.py`、`src/bai_agent/memory/long_term.py` 和 `src/bai_agent/memory/selection.py` 为已选 raw/长期记忆提供数据文件路径、revision/hash 与完整 record/memory id 来源
- [X] T039 [P] [US1] 在 `src/bai_agent/tools/registry.py` 和 `src/bai_agent/tools/memory_source.py` 为工具定义及运行时工具结果提供 config/runtime SourceRef，禁止按正文反向猜测来源
- [X] T040 [US1] 在 `src/bai_agent/providers/deepseek.py` 将调用拆为无 I/O `prepare()`、每 attempt 唯一受控 `materialize_sdk_kwargs()` 和单次 I/O `send_once()`，移除 adapter 内重试、让认证只在发送 client 层注入并在 finally 释放 payload
- [X] T041 [US1] 在 `src/bai_agent/model_calls/gateway.py` 一次实现 prepare→唯一 materialize→来源/凭据校验→估算→完整 mounted→call+attempt+digest 批准→发送前清除 TUI→复核→send_once/finally release→retry/backoff；每次 retry 新建 attempt 并重新批准
- [X] T042 [P] [US1] 在 `src/bai_agent/memory/curation.py` 将 curator 调用迁移为只依赖 ModelCallGateway，并把 proposal/响应留在 TurnWorkingSet
- [X] T043 [P] [US1] 在 `src/bai_agent/runtime/controller.py` 将 chat、tool continuation 和 future persona 入口统一迁移到 ModelCallGateway，接入 PREPARED/READY_PENDING/READY_TO_COMMIT 并在 `TurnRejected` 时丢弃整轮
- [X] T044 [US1] 在 `src/bai_agent/providers/registry.py` 强制 provider 只暴露 adapter 端口，并在所有调用方迁移后删除或封闭应用层直接 SDK/provider.complete 旁路
- [X] T045 [P] [US1] 在 `src/bai_agent/debug/tui.py` 实现短生命周期 Textual approval app、完整 materialized payload/part/source、隐私提醒、滚动与 A/R 决策；完整 mounted 前禁用批准，approve 后且网络发送前关闭正文视图并释放 presenter 引用
- [X] T046 [US1] 在 `src/bai_agent/application.py`、`src/bai_agent/runtime/controller.py` 和 `src/bai_agent/cli.py` 注入 gateway/presenter/transaction，并只为当前 chat 运行提供 `--debug-prompts` 基本接线
- [X] T047 [US1] 在 `start.ps1` 增加 `-DebugPrompts` 安全透传，继续隐藏输入 API Key 并在 finally 清除当前进程凭据
- [X] T048 [P] [US1] 为 DR-001 更新 `README.md` 的单次调试启动、私人记忆提醒、最终 provider 请求、来源标签、批准/拒绝、批准后 TUI 清除、send_once finally 释放和普通失败转 pending 说明
- [X] T049 [P] [US1] 为 DR-002/DR-004 更新 `specs/001-persistent-memory-agent/contracts/cli.md`、`specs/001-persistent-memory-agent/contracts/model-and-tools.md`、`specs/001-persistent-memory-agent/quickstart.md` 与 `specs/002-prompt-trace-debugger/quickstart.md`，同步唯一网关、retry、拒绝、pending、materialization 和可执行验收命令
- [X] T050 [US1] 运行 `tests/contract/test_prompt_trace_provider.py tests/unit/test_prompt_provenance.py tests/contract/test_model_call_gateway.py tests/contract/test_provider_adapter_portability.py tests/integration/test_model_call_gateway_coverage.py tests/unit/test_prompt_trace_lifecycle.py tests/integration/test_prompt_trace_rejection.py tests/contract/test_prompt_approval_tui.py tests/integration/test_prompt_trace_single_call.py -q`、现有 provider/curation/tool/pending 回归、文档命令与链接检查及 `git diff --check`，随后通过 Git 扩展原子提交 US1 的 gateway/provider/callers/TUI/CLI/tests 与 README/001/002 文档

**Checkpoint**: US1 是首个可公开启动的安全 MVP；所有当前模型调用和 retry 已无旁路，批准、拒绝、凭据与失败恢复均有先行测试。

---

## Phase 4: User Story 2 - 实时区分一轮中的多次模型调用（Priority: P2）

**Goal**: 在 US1 已统一的调用边界上，稳定展示一轮中各调用的身份、真实顺序、attempt、状态和颜色/文本分组，不恢复已经发送的旧界面。

**Independent Test**: 构造 curation→chat→tool continuation→retry 的轮次；每个 attempt 具有稳定身份与唯一批准项，前一项未决定时后一项不可处理；支持颜色、禁用颜色和交互式无颜色终端均可仅凭标签无歧义识别。

### Tests for User Story 2（先写并确认失败）

- [X] T051 [P] [US2] 为 BR-003 在 `tests/unit/test_model_call_sequence.py` 增加 call sequence、purpose、attempt 单调递增及漏记/重复/覆盖/越序阻断测试
- [X] T052 [P] [US2] 在 `tests/integration/test_prompt_trace_multi_call.py` 增加 curation→chat→tool continuation→retry 的逐项展示、前项门禁、失败状态与下一 attempt 不合并测试
- [X] T053 [P] [US2] 为 BR-005 在 `tests/contract/test_prompt_tui_presentation.py` 增加稳定颜色、`NO_COLOR`/never/交互式不支持颜色降级、文本标签、分组缩进、控制字符和数百来源展开测试；明确不把输出重定向当作降级环境
- [X] T054 [P] [US2] 在 `tests/contract/test_prompt_call_identity.py` 增加 turn/flow/call/purpose/persona/state/provider/model/config/attempt/status 字段完整性与跨 retry 稳定性测试

### Implementation for User Story 2

- [X] T055 [US2] 在 `src/bai_agent/model_calls/gateway.py` 实现轮内 call sequence、attempt 与状态转换的单一分配器，禁止调用方自行编号或覆盖历史状态
- [X] T056 [P] [US2] 在 `src/bai_agent/memory/curation.py` 和 `src/bai_agent/runtime/controller.py` 补齐 curator/chat/tool/future persona 的调用用途、persona/state/config revision 元数据
- [X] T057 [P] [US2] 在 `src/bai_agent/debug/tui.py` 增加完整调用标题、稳定来源色板、等价无色标签和超长/数百来源可滚动分组，previous attempt 退出后不得恢复正文
- [X] T058 [P] [US2] 为 DR-001 更新 `README.md` 的多调用顺序、整理/工具/retry 逐次批准及交互式颜色/无色边界说明
- [X] T059 [P] [US2] 为 DR-002 更新 `specs/001-persistent-memory-agent/quickstart.md` 和 `specs/002-prompt-trace-debugger/quickstart.md` 的多调用、retry、颜色降级与非 TTY 失败验收
- [X] T060 [US2] 运行 `tests/unit/test_model_call_sequence.py tests/integration/test_prompt_trace_multi_call.py tests/contract/test_prompt_tui_presentation.py tests/contract/test_prompt_call_identity.py -q`、curation/tool/provider 回归、文档命令和 `git diff --check`，随后通过 Git 扩展原子提交 US2 的 gateway/callers/TUI/tests 与 README/quickstart

**Checkpoint**: US2 可独立证明多调用顺序、身份和视觉降级，不引入新的 provider 路径。

---

## Phase 5: User Story 3 - 判断上下文窗口占用（Priority: P3）

**Goal**: 批准前显示最终 materialized payload 的输入总估算、守恒分段、协议开销、输出预留、峰值/容量/比例/剩余/风险；响应后只显示无原文实际用量摘要。

**Independent Test**: 对固定 DeepSeek 模型版本和有来源说明的参考集验证 `input = sum(parts) + overhead`、`peak = input + output reserve`、95% 误差目标；未知容量/unsupported payload/无效 usage 不产生虚构数字，调用身份和上下文摘要在规定基准环境 500 ms 内可见。

### Tests for User Story 3（先写并确认失败）

- [X] T061 [P] [US3] 为 BR-004 在 `tests/unit/test_context_estimation.py` 增加总量守恒、输出预留、峰值、容量占比/负剩余、normal/high/critical/exceeded、容量未知和 unsupported payload 测试
- [X] T062 [P] [US3] 在 `tests/unit/test_context_estimation_properties.py` 用 Hypothesis 覆盖任意 Unicode/空段/part 组合的非负值、确定性、顺序和总量守恒属性
- [X] T063 [P] [US3] 在 `tests/fixtures/prompt_trace/deepseek_usage_cases.json` 记录至少 40 个无真实凭据、不发实时 API 的中英混合/长记忆/tools 样本及其 `deepseek-v4-flash` 模型 id、采集日期、官方 usage、payload hash 和刷新说明，并在 `tests/performance/test_deepseek_estimator_accuracy.py` 验证 SC-005 参考集来源与 95% 误差目标
- [X] T064 [P] [US3] 在 `tests/integration/test_prompt_trace_actual_usage.py` 增加合法/缺失/负数/不守恒 provider usage、估算误差、普通终端无原文摘要和不重新打开 prompt TUI 测试
- [X] T065 [P] [US3] 为 FR-034/SC-017 在 `tests/contract/test_model_capabilities.py` 增加 DeepSeek 1,000,000 context/384,000 output cap、chat 与 memory curator 迁移至 `deepseek-v4-flash`、两者 `thinking_enabled=false`/`max_output_tokens=8192`、其余生成参数不变、未知容量和不兼容 estimator 行为测试并确认失败
- [X] T066 [P] [US3] 为 SC-007 在 `tests/performance/test_prompt_tui_latency.py` 以 monotonic mounted 事件、fake provider、原生 Ubuntu 24.04/Python 3.13/80x24 `xterm-256color`，从 frozen request/来源/估算就绪测量首次冷启动并单独记录，再以 `tests/performance/baselines/ubuntu-24.04-python-3.13.json` 验证 30 次同进程启动 p95≤500 ms 及完整批准前发送 0

### Implementation for User Story 3

- [X] T067 [US3] 在 `src/bai_agent/model_calls/estimation.py` 实现 estimator registry、`deepseek_character_v1` 对 materialized messages/tools 的整体估算、前缀边际分配、协议 overhead、配置安全裕度和 unavailable(reason)
- [X] T068 [P] [US3] 在 `src/bai_agent/providers/deepseek.py` 映射合法 prompt/completion/total usage，并对缺失、负数和不守恒数据返回 unavailable
- [X] T069 [US3] 在 `src/bai_agent/model_calls/gateway.py` 于批准前对唯一 materialized payload 调用 estimator、验证分段守恒，并在响应后仅生成不持有 prompt/PreparedProviderRequest/MaterializedSendPayload/part/SourceRef/TUI 对象的 ActualUsageSummary
- [X] T070 [P] [US3] 在 `src/bai_agent/debug/tui.py` 增加批准前的 `≈` 输入/分段/overhead、输出预留、峰值、容量、占比、剩余、主要贡献和分级风险；在 `src/bai_agent/runtime/controller.py` 通过既有 `on_output` 路径于 prompt TUI 关闭后显示无原文 ActualUsageSummary，禁止 debug TUI 处理响应后输出
- [X] T071 [P] [US3] 在 `config/providers.toml` 将 chat 与 memory curator 迁移到 `deepseek-v4-flash`，保持两者 `thinking_enabled=false`、`max_output_tokens=8192` 及现有 temperature/tools/structured-output 等生成参数不变，记录 1M context/384K output cap/estimator 能力；在 `config/agent.toml` 校准阈值且不改变 `context_budget` 语义
- [X] T072 [P] [US3] 为 DR-001/DR-004 更新 `README.md` 的上下文字段、估算/实际区别、普通用量摘要、未知/超限表现和能力数字来源
- [X] T073 [P] [US3] 为 DR-002/DR-003/DR-004 更新 `specs/001-persistent-memory-agent/contracts/configuration.md`、`specs/001-persistent-memory-agent/quickstart.md` 与 `specs/002-prompt-trace-debugger/quickstart.md`，同步模型迁移、fixture 来源、估算方法、基准口径和故障排查
- [X] T074 [US3] 运行 `tests/unit/test_context_estimation.py tests/unit/test_context_estimation_properties.py tests/performance/test_deepseek_estimator_accuracy.py tests/integration/test_prompt_trace_actual_usage.py tests/contract/test_model_capabilities.py tests/performance/test_prompt_tui_latency.py -q`、配置/provider 回归、文档命令和 `git diff --check`，随后通过 Git 扩展原子提交 US3 的 estimation/provider/gateway/TUI/config/tests 与 README/001/002 文档

**Checkpoint**: US3 的估算、实际值和性能结果均具有稳定口径与可追溯 fixture，不依赖猜测数字。

---

## Phase 6: User Story 4 - 安全启停调试视图（Priority: P4）

**Goal**: 完成运行级安全体验：默认关闭、仅交互式 TTY 启用、不可运行中切换、退出后失效、控制字符安全、批准后 TUI 清除、发送后 payload 释放、重启恢复和 debug on/off 行为等价。

**Independent Test**: 比较同一确定性输入的 debug on/off HTTP 请求、记忆和工具行为；覆盖默认/重启关闭、stdin/stdout 非 TTY、Textual 初始化失败、Ctrl+C/EOF、journal 权限/损坏/冲突和 1,000 次释放，所有失败均不自动批准或泄露正文/凭据。

### Tests for User Story 4（先写并确认失败）

- [ ] T075 [P] [US4] 在 `tests/contract/test_cli_prompt_debug.py` 增加 `chat --debug-prompts`、默认/重启关闭、运行中不可切换、stdin/stdout 非 TTY、重定向、Textual 初始化失败、exit 2、Ctrl+C 130 和任何新写入/发送前失败测试
- [ ] T076 [P] [US4] 为 BR-006 在 `tests/integration/test_prompt_debug_equivalence.py` 增加 debug on/off materialized/HTTP 请求、记忆变化、工具行为和调用顺序深度相等，以及展示失败不改写/不自动批准测试
- [ ] T077 [P] [US4] 在 `tests/integration/test_turn_transaction_security.py` 增加 journal 私有权限、schema 损坏、未知字段、prompt/provenance/credential 禁区、人工编辑冲突和脱敏恢复错误测试
- [ ] T078 [P] [US4] 为 BR-007 在 `tests/performance/test_prompt_trace_release.py` 增加连续批准发送 1,000 次后 presenter 正文/来源/debug object 遗留为 0，send_once 成功/失败后 sender materialized payload 遗留为 0，ActualUsageSummary 不持有或恢复原文的测试
- [ ] T079 [P] [US4] 在 `tests/integration/test_prompt_debug_runtime_lifecycle.py` 增加 WriterLease 后优先恢复、READY_PENDING/READY_TO_COMMIT 启动收敛、恢复前阻止新输入/provider 和退出后 debug flag 不持久化测试

### Implementation for User Story 4

- [ ] T080 [US4] 在 `src/bai_agent/cli.py` 完成 debug flag 生命周期、stdin/stdout TTY 预检、重定向失败、脱敏错误/退出码和 Ctrl+C/EOF 语义，并在 `start.ps1` 保持安全透传与凭据 finally 清除
- [ ] T081 [US4] 在 `src/bai_agent/application.py` 将事务恢复放在 WriterLease 后、pending/config/provider/新输入之前，未收敛或冲突时阻止全部新轮与模型调用
- [ ] T082 [P] [US4] 在 `src/bai_agent/debug/tui.py` 完成 R/Esc/Ctrl+C/EOF、控制字符安全文本、交互式颜色探测、app 退出清屏和所有成功/失败路径引用释放
- [ ] T083 [P] [US4] 在 `src/bai_agent/security/incidents.py` 与 `src/bai_agent/runtime/tracing.py` 确保调试错误、恢复状态和 actual usage 只记录安全元数据，不记录 prompt/source/credential
- [ ] T084 [P] [US4] 为 DR-001/DR-004 更新 `README.md` 的默认关闭、TTY/重定向、私人记忆暴露、凭据边界、拒绝与普通失败区别、批准后 TUI 清除与 send_once finally 释放、重启恢复和故障排查
- [ ] T085 [P] [US4] 更新 `specs/001-persistent-memory-agent/contracts/storage.md`、`specs/001-persistent-memory-agent/contracts/cli.md`、`specs/001-persistent-memory-agent/contracts/model-and-tools.md` 与 `specs/001-persistent-memory-agent/quickstart.md`，同步三态 journal、恢复顺序、pending、TTY 和工具副作用限制
- [ ] T086 [P] [US4] 根据最终实现校准 `specs/002-prompt-trace-debugger/contracts/cli-tui.md`、`specs/002-prompt-trace-debugger/contracts/turn-transaction.md` 与 `specs/002-prompt-trace-debugger/quickstart.md` 的退出码、键位、清除/释放时点、journal schema 和恢复命令
- [ ] T087 [US4] 运行 `tests/contract/test_cli_prompt_debug.py tests/integration/test_prompt_debug_equivalence.py tests/integration/test_turn_transaction_security.py tests/performance/test_prompt_trace_release.py tests/integration/test_prompt_debug_runtime_lifecycle.py -q`、现有 persistence/restart/pending/security 回归、文档命令/链接和 `git diff --check`，随后通过 Git 扩展原子提交 US4 的 CLI/application/TUI/security/tests 与 README/001/002 文档

**Checkpoint**: 四个故事全部可验收；公开调试运行在交互、拒绝、普通失败、崩溃和凭据风险下均安全收敛。

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: 完成规模、凭据、可用性、注释、兼容性和全量回归门禁，不把故事阶段应同步的文档推迟到此处。

- [ ] T088 [P] 在 `tests/integration/test_prompt_trace_coverage_scale.py` 增加至少 200 次 chat/curation/tool/future persona/failure/retry 混合调用验收，证明 approval 与物理出站一一对应、顺序一致且未批准发送为 0
- [ ] T089 [P] 为 CR-004 扩展 `tests/integration/test_repository_secret_safety.py` 与 `tests/security_scanner.py`，扫描 TUI、错误、日志、journal、fixture、工作树和可达 Git 历史，确保可用凭据与持久 prompt trace 数量均为 0
- [ ] T090 按 `specs/002-prompt-trace-debugger/quickstart.md` 在隔离数据目录执行普通/debug 单次/多次、无色、非 TTY、拒绝、pending、重启和故障排查命令，在 `specs/002-prompt-trace-debugger/checklists/usability.md` 记录至少 10 次首次使用验收并验证至少 90% 能在 30 秒内定位来源
- [ ] T091 运行 `pytest -q`、全部 `tests/performance/` 与 `tests/fault_injection/`，修复回归但不得降低 BR-001—BR-010、凭据、provider portability、模型迁移或 write-tool transaction 断言
- [ ] T092 审核 `src/bai_agent/` 本功能注释：全部为简体中文、带日期/版本并说明职责与不变量，只在原注释过时或误导时更新；在 `specs/002-prompt-trace-debugger/tasks.md` 记录修正偏差
- [ ] T093 在 `pyproject.toml` 声明范围内验证 Ubuntu 24.04/Python 3.13 与 3.14 的主要 editable install、`python -m bai_agent config validate --config-dir config`、CLI help 和核心功能测试，并在 Windows 11/PowerShell 执行同组次要功能兼容验收，macOS 记为不在范围，最后运行 `git diff --check`
- [ ] T094 [P] 更新 `.github/workflows/compatibility.yml`：以带固定容器/镜像说明的 Ubuntu 24.04/Python 3.13 与 3.14 为主要功能矩阵，保留 Windows runner 次要功能矩阵，移除 macOS；将手动参考性能作业迁到 Ubuntu 24.04/Python 3.13 并执行 30 次同进程 prompt TUI p95 门禁；修复乱码中文注释并用 `[2026-07-20]` 时间戳记录原因
- [ ] T095 核对 `specs/002-prompt-trace-debugger/spec.md` 的 FR-001—FR-034、BR-001—BR-010、CR-001—CR-004、DR-001—DR-004、SC-001—SC-017 与任务/测试/文档逐项闭环，确认本次 analyze 的 CRITICAL/HIGH 为 0、名义覆盖 100%，且无 non-TTY、事务状态、TUI 生命周期、模型迁移或未映射产品行为冲突；运行链接/占位符/凭据/兼容性/`git diff --check` 审计后，通过 Git 扩展原子提交仅包含最终测试、注释、兼容性和文档校准变更

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup**: 可立即开始，与 Foundational 合并提交。
- **Foundational**: 依赖 Setup；设计语义已在任务生成前统一，T003—T012 测试先行，T026 通过后才允许公开调试入口。
- **US1**: 依赖 Foundational；在单个原子阶段完成 provider 拆分、retry、所有调用方迁移、旁路封闭、TUI/CLI 和拒绝回滚。
- **US2**: 依赖 US1 的唯一网关与 TUI，只增加多调用身份、顺序和视觉表达。
- **US3**: 依赖 US1 的 materialized payload/parts 和 US2 的 attempt 身份。
- **US4**: 依赖 US1 的安全内核，完成运行级 TTY、恢复、释放和等价性硬化。
- **Polish**: 依赖四个用户故事全部完成。

### User Story Dependency Graph

```text
Setup -> Foundational safety/transaction
       -> US1 safe public MVP -> US2 multi-call presentation -> US3 context usage -> US4 runtime hardening -> Polish
```

### Within Each Phase

1. 先完成该阶段所有测试并确认因缺失行为失败。
2. 按模型/端口 → service/adapter → controller/UI/CLI 顺序实现。
3. 更新同一重大修改影响的 README、quickstart、配置和公共/存储契约。
4. 运行阶段测试、既有回归、文档命令/链接和 `git diff --check`。
5. 通过 Git 扩展创建只含本阶段相关文件的原子提交。

## Parallel Opportunities

- **Foundational**: T003—T012 可并行编写不同测试；T017/T018、T020—T024 在各自前置契约完成后可并行。
- **US1**: T027—T035 可并行写测试；T038/T039/T045 可在来源/端口稳定后并行；T042/T043 可在 T041 后并行；T048/T049 可并行同步文档。
- **US2**: T051—T054 可并行；T056/T057 及 T058/T059 可在各自前置完成后并行。
- **US3**: T061—T066 可并行；T068/T070/T071 可在估算契约稳定后并行；T072/T073 可并行同步文档。
- **US4**: T075—T079 可并行；T082/T083 与 T084—T086 可在核心运行接线稳定后并行。
- **Polish**: T088/T089 可并行，T094 可在兼容性设计稳定后独立更新 workflow，随后由 T095 统一审计。

## Parallel Execution Examples

### US1

```text
并行测试：T027 || T028 || T029 || T030 || T031 || T032 || T033 || T034 || T035
实现顺序：T036 -> T037 -> (T038 || T039 || T045) -> T040 -> T041 -> (T042 || T043) -> T044 -> T046 -> T047
```

### US2

```text
并行测试：T051 || T052 || T053 || T054
实现顺序：T055 -> (T056 || T057) -> (T058 || T059) -> T060
```

### US3

```text
并行测试：T061 || T062 || T063 || T064 || T065 || T066
实现顺序：T067 -> (T068 || T070 || T071) -> T069 -> (T072 || T073) -> T074
```

### US4

```text
并行测试：T075 || T076 || T077 || T078 || T079
实现顺序：T080 -> T081 -> (T082 || T083) -> (T084 || T085 || T086) -> T087
```

## Implementation Strategy

### Safe MVP First（US1）

1. 完成 Setup 与 Foundational，按已修订工件实现并提交事务、安全、pending 和工具门禁。
2. 完成 US1 的失败优先测试。
3. 在同一阶段完成 provider 三方法端口、唯一 materialization、retry、全部调用方、拒绝回滚、TUI/CLI 和文档，禁止保留临时旁路。
4. 在 T050 独立演示：所有调用先批准，展示/materialization/HTTP 一致，reject 无痕，普通失败保留 pending。
5. 只有 T050 通过后才把 US1 称为可公开运行的 MVP。

### Incremental Delivery

1. **Foundation**: 三态事务、失败/pending、安全和扩展门禁。
2. **US1**: 安全的完整请求、来源、批准/拒绝和唯一网关。
3. **US2**: 多调用身份、顺序和视觉降级。
4. **US3**: 可追溯估算、实际用量和稳定性能口径。
5. **US4**: 运行级 TTY、恢复、释放和等价性硬化。
6. **Final**: 200/1,000 次规模、凭据历史、可用性、Linux/Windows 兼容性和最终审计。

## Analyze Remediation Mapping

- **K1/I2**: T007—T010、T019—T022 前置三态事务；T027—T050 在公开入口前完成 BR-007/BR-008/BR-009 测试和所有调用迁移。
- **K2**: T011 测试先于 T023 写工具事务门禁。
- **I1**: Spec/contract 已在生成前统一重定向/non-TTY 与交互式无色；T053/T059/T075/T080 验证和文档化。
- **I3**: T040—T044 在同一 US1 原子阶段完成 provider 拆分、retry、调用方迁移和旁路封闭。
- **I4/A1**: Data model/contracts 已固定批准后 TUI 清除、send_once finally 释放和普通 actual usage 摘要；T032/T064/T069/T070/T078/T086 验证。
- **U1**: T005/T007—T009/T019—T020 定义并实现 READY_PENDING，保持既有 pending 恢复。
- **G1**: T030 增加第二个 fake provider 全网关合同测试。
- **U2**: T005/T006/T013/T014/T027/T029/T036/T040/T041 明确唯一 materialization 与 digest/HTTP 一致性。
- **U3**: T063 固定 DeepSeek fixture 数量、来源、版本、hash 和刷新流程，不调用实时 API。
- **A2**: T066/T094 固定 Ubuntu 24.04/Python 3.13、80×24、monotonic mounted 事件、首次冷启动记录和 30 次同进程 p95 门禁。
- **D1**: FR-011/FR-026 已在生成前拆分展示就绪与逐请求摘要批准职责；T029/T034 分别验证绑定和 mounted 门禁。
- **M1**: T065 测试先于 T071 的 FR-034/SC-017 双 profile 模型迁移，T073/T094 同步文档和兼容性门禁。

## Notes

- `[P]` 不允许跳过显式依赖或同时修改同一文件。
- 测试必须先在工作树中产生预期失败，再开始对应实现；阶段提交时所有测试必须通过。
- 测试只使用隔离临时数据目录，不改写维护者的 `data/`。
- 调试 trace 不是持久数据模型；journal 仅保存事务恢复必需数据，禁止保存 prompt payload、part、SourceRef 或凭据。
- 每个检查点提交前必须同步并验证受影响文档；最终 Polish 不替代阶段内文档任务。
