# Tasks: 持久记忆聊天 Agent

**Input**: Design documents from `/specs/001-persistent-memory-agent/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

**Tests**: BR-001—BR-018 的自动化测试全部为强制任务，并位于对应实现任务之前。配置、凭据、原子写入、供应商协议、提示注入、并发和性能测试也因契约或风险而强制执行。

**Organization**: 任务按用户故事分阶段。每个故事都给出独立验收方法；共享领域契约、配置骨架和安全门禁位于 Setup/Foundation。

**Constitution Gates**: 实现始终遵循“清晰可维护 → 解耦可读 → 简单实现”的优先级。非直观不变量、安全边界和恢复分支的新增注释必须使用简体中文并带 `[2026-07-19]` 或后续版本标记；准确的既有注释不改写、不删除。每个阶段完成测试后只提交该阶段相关文件，任何凭据不得进入代码、配置、记忆、日志、测试或提交。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可与同一执行组中的其他 `[P]` 任务并行，且不修改同一文件。
- **[Story]**: 用户故事追踪标签；Setup、Foundation 和 Polish 不使用故事标签。
- 所有任务均包含明确文件路径。

## Phase 1: Setup（共享工程骨架）

**Purpose**: 建立 Python 包、测试目录和仓库忽略边界，不实现业务行为。

- [ ] T001 在 `pyproject.toml` 定义 Python 3.13/3.14、`bai_agent` CLI 入口、运行依赖 `openai/pydantic/ruamel.yaml/filelock`、开发依赖 `pytest/pytest-asyncio/hypothesis/respx` 及 pytest markers
- [ ] T002 [P] 按计划创建 `src/bai_agent/__init__.py`、`src/bai_agent/__main__.py` 与 `src/bai_agent/{domain,config,memory,prompting,providers,tools,states,runtime,security}/__init__.py`
- [ ] T003 [P] 在 `.gitignore` 忽略 `.env`、虚拟环境、`data/`、运行日志、临时原子写文件、覆盖率产物和 Python 缓存，同时保留 `config/` 可提交

---

## Phase 2: Foundational（阻塞所有用户故事）

**Purpose**: 建立稳定领域契约、配置骨架、凭据门禁和测试替身。

**⚠️ CRITICAL**: 本阶段完成前不得开始用户故事实现。

### Foundation tests（先写并确认失败）

- [ ] T004 [P] 在 `tests/unit/test_domain_models.py` 为冻结 DTO、稳定 ID、UTC 时间、JSON 往返和非法枚举编写边界测试
- [ ] T005 [P] 在 `tests/unit/test_config_validation.py` 为 TOML 类型、交叉字段约束、配置根路径边界、缺失外部凭据和 ConfigSnapshot revision 编写测试
- [ ] T006 [P] 在 `tests/unit/test_credentials.py` 与 `tests/unit/test_logging.py` 为凭据拒绝/不可逆脱敏及日志字段白名单编写泄露测试
- [ ] T007 [P] 在 `tests/contract/test_ports.py` 为 ModelProvider、MemoryRepository、Tool、StateResolver 和 LoopPolicy 替身的结构契约与 JSON DTO 往返编写测试

### Foundation implementation

- [ ] T008 [P] 在 `src/bai_agent/domain/models.py` 实现共享冻结 dataclass、Pydantic 边界模型、稳定 ID/时间/哈希值对象及 JSON 类型别名
- [ ] T009 [P] 在 `src/bai_agent/domain/errors.py` 实现稳定领域错误码、安全消息和 retryable 语义，禁止泄漏正文、凭据和绝对路径
- [ ] T010 在 `src/bai_agent/domain/ports.py` 定义存储、模型、工具、状态、配置时钟与循环 Protocol，保证核心层不导入供应商 SDK 类型
- [ ] T011 在 `src/bai_agent/config/loader.py` 实现只读 TOML 基础加载、显式入口清单和每轮不可变 ConfigSnapshot
- [ ] T012 在 `src/bai_agent/config/validation.py` 实现数值/引用/能力/本地数据根校验及配置根逃逸拒绝
- [ ] T013 [P] 在 `src/bai_agent/security/credentials.py` 与 `src/bai_agent/security/redaction.py` 实现写入前凭据检测、拒绝/不可逆脱敏及秘密环境变量读取边界
- [ ] T014 在 `src/bai_agent/runtime/tracing.py` 实现安全结构化日志基础设施，只允许稳定 ID、哈希、计数、时长、错误码和用量字段
- [ ] T015 [P] 按 `contracts/configuration.md` 创建无真实凭据的 `config/agent.toml`、`config/providers.toml`、`config/states.toml`、`config/tools.toml`、`config/logging.toml` 及非空 `config/personas/`、`config/prompts/` 基线文件
- [ ] T016 在 `tests/conftest.py` 与 `tests/fakes.py` 实现隔离数据目录、确定性时钟/ID、FakeProvider、内存端口、输出捕获和故障注入共享 fixture
- [ ] T017 运行 `tests/unit/test_domain_models.py`、`tests/unit/test_config_validation.py`、`tests/unit/test_credentials.py`、`tests/unit/test_logging.py`、`tests/contract/test_ports.py`，确认通过后提交 `pyproject.toml`、`.gitignore`、`config/`、`src/bai_agent/domain/`、`src/bai_agent/config/`、`src/bai_agent/security/`、`src/bai_agent/runtime/tracing.py` 与共享测试

**Checkpoint**: 共享契约、非敏感配置和测试门禁可用；用户故事实现可以按依赖关系开始。

---

## Phase 3: User Story 1 - 跨启动延续同一个 Agent（Priority: P1）🎯 MVP

**Goal**: 完成单一连续记忆空间、输入/输出确认顺序、跨重启恢复和无会话 CLI。

**Independent Test**: 用 FakeProvider 完成 100 轮交互并重启 10 次，不选择会话即可恢复全部已确认记录；在每个写入故障点只能恢复完整旧/新状态，Provider 失败后可显式恢复 pending turn。

### Tests for User Story 1（先写并确认失败）

- [ ] T018 [P] [US1] 在 `tests/unit/test_raw_record_archive.py` 为 BR-001/BR-002 的 RawRecord 序列、turn 配对、Unicode/多行正文、校验和与分段滚动编写单元和 Hypothesis 属性测试
- [ ] T019 [P] [US1] 在 `tests/integration/test_persistence_order.py` 为 BR-003 编写“用户输入早于 Provider 调用、Assistant 输出早于 stdout、失败内容不确认”的调用顺序测试
- [ ] T020 [P] [US1] 在 `tests/fault_injection/test_raw_atomicity.py` 为 BR-001/BR-003 覆盖临时创建、写入、flush、fsync、replace 前后中断和尾段半行损坏恢复
- [ ] T021 [P] [US1] 在 `tests/integration/test_restart_continuity.py` 为 BR-001/BR-002 实现 100 轮、10 次重启、空记忆、大量记忆及单一全局顺序验收测试
- [ ] T022 [P] [US1] 在 `tests/integration/test_writer_lock.py` 验证两个子进程竞争 `data/memory/.state/writer.lock` 时恰好一个获得写权且失败方不改文件
- [ ] T023 [P] [US1] 在 `tests/contract/test_deepseek_provider.py` 覆盖完整响应、错误归一化、有界重试、取消、截断、流中断不得成功及 `reasoning_content` 不外泄
- [ ] T024 [P] [US1] 在 `tests/contract/test_cli_chat.py` 覆盖无会话/线程命令、稳定退出码、Ctrl+C/EOF、pending turn 报告和 `--resume-pending` 幂等语义

### Implementation for User Story 1

- [ ] T025 [P] [US1] 在 `src/bai_agent/memory/recovery.py` 实现同目录临时写、flush、fsync、replace、残留临时文件识别和平台目录同步的原子写基础
- [ ] T026 [US1] 在 `src/bai_agent/memory/archive.py` 实现有界 JSONL 尾段重写、段滚动、严格全局序列、RawRecord 校验和及永久归档追加
- [ ] T027 [US1] 在 `src/bai_agent/memory/recovery.py` 实现正式段扫描、offset 派生索引、损坏隔离、序号缺口拒绝、单写者锁生命周期和 pending turn 恢复
- [ ] T028 [P] [US1] 在 `src/bai_agent/providers/deepseek.py` 实现隔离 OpenAI SDK 的 DeepSeek 完整响应适配器、配置参数映射、错误归一化和供应商字段过滤
- [ ] T029 [US1] 在 `src/bai_agent/providers/registry.py` 实现按配置选择 ModelProvider/profile、能力预检和未知适配器 fail-closed
- [ ] T030 [P] [US1] 在 `src/bai_agent/states/resolver.py` 实现首版只返回配置默认状态的 StaticStateResolver，使每条 RawRecord 都带显式 `state_id`
- [ ] T031 [P] [US1] 在 `src/bai_agent/prompting/assembler.py` 实现供 MVP 使用的确定性近期原文 PromptContext 组装，并通过端口接受人格/记忆段而不硬编码提示词
- [ ] T032 [US1] 在 `src/bai_agent/runtime/controller.py` 实现 SingleTurnController 的输入先存、完整生成、输出先存、幂等 turn ID、失败保留输入和安全取消顺序
- [ ] T033 [US1] 在 `src/bai_agent/application.py` 连接配置快照、写锁、原始归档、状态解析、提示组装、Provider 与安全追踪用例
- [ ] T034 [US1] 在 `src/bai_agent/cli.py` 实现 `chat` 与原始归档版 `memory validate`、全局路径选项、稳定 JSON/错误输出和 `--resume-pending`
- [ ] T035 [US1] 在 `src/bai_agent/__main__.py` 连接 CLI 入口、异步生命周期、Ctrl+C 退出码和锁释放，且不提供任何会话选择功能
- [ ] T036 [US1] 运行 `tests/unit/test_raw_record_archive.py`、`tests/integration/test_persistence_order.py`、`tests/fault_injection/test_raw_atomicity.py`、`tests/integration/test_restart_continuity.py`、`tests/integration/test_writer_lock.py`、`tests/contract/test_deepseek_provider.py`、`tests/contract/test_cli_chat.py`，验证 MVP 后提交 `src/bai_agent/memory/`、`src/bai_agent/providers/`、`src/bai_agent/states/resolver.py`、`src/bai_agent/prompting/assembler.py`、`src/bai_agent/runtime/controller.py`、`src/bai_agent/application.py`、`src/bai_agent/cli.py`、`src/bai_agent/__main__.py` 与 US1 测试

**Checkpoint**: US1 可独立运行和验收；这是建议的首个可演示 MVP。

---

## Phase 4: User Story 2 - 组织长短期记忆并构造每轮上下文（Priority: P2）

**Goal**: 在窗口边界批量整理、原子提交长期记忆/多来源/修剪前沿，支持人工维护、容量选择和统一只读来源查询。

**Independent Test**: 使用小窗口 fixture 输入重复、修正、稳定事实和闲聊；阈值前整理调用为 0，边界成功后同一 YAML revision 推进来源和前沿，任一失败不修剪；有效人工修改生效、无效修改保留原文件并回退；所有人格同参查询得到相同只读来源。

### Tests for User Story 2（先写并确认失败）

- [ ] T037 [P] [US2] 在 `tests/unit/test_long_term_models.py` 为 BR-006/BR-012/BR-016 的 active/superseded/retracted、人工优先、来源非空、关系无环和悬空引用拒绝编写测试
- [ ] T038 [P] [US2] 在 `tests/integration/test_long_term_store.py` 为 BR-012/BR-014/BR-016 编写 YAML 注释往返、有效人工修改、无效文件不覆盖、last-valid 只读回退及来源哈希校验测试
- [ ] T039 [P] [US2] 在 `tests/unit/test_curation_policy.py` 为 BR-011/BR-013 编写阈值前零调用、最旧连续完整轮次、批次上限、空提取可推进和失败不推进测试
- [ ] T040 [P] [US2] 在 `tests/fault_injection/test_long_term_atomicity.py` 为 BR-004/BR-013/BR-016 覆盖整理、Schema、外部并发编辑、YAML flush/fsync/replace 各故障点
- [ ] T041 [P] [US2] 在 `tests/unit/test_memory_selection.py` 为 BR-004/BR-006/BR-007/BR-015 编写冲突优先、预算压缩、完整轮次选择、未选项候选资格和原始段哈希/数量不变测试
- [ ] T042 [P] [US2] 在 `tests/contract/test_prompt_context.py` 为 BR-005/BR-007/BR-018 编写强制人格/状态/长期/短期段、确定顺序、缺段失败、信任标签及未调用工具时来源原文为 0 的契约测试
- [ ] T043 [P] [US2] 在 `tests/contract/test_memory_source_tool.py` 为 BR-017/BR-018 覆盖聊天/整理/状态/两个辅助人格同参结果、稳定分页/错误、当前 flow 隔离和调用前后权威文件哈希不变
- [ ] T044 [P] [US2] 在 `tests/integration/test_curation_workflow.py` 为 BR-011/BR-012/BR-013/BR-016 实现窗口整理、重试、人工优先、重启去重及多来源端到端测试
- [ ] T045 [P] [US2] 在 `tests/integration/test_plaintext_permissions.py` 为 BR-014 和 CR-002/CR-005/CR-006 验证明文可读、过宽权限告警及原始/长期/工具结果均不能持久化测试凭据

### Implementation for User Story 2

- [ ] T046 [US2] 在 `src/bai_agent/domain/models.py` 增加 LongTermMemoryDocument/Item、SourceReference、CurationCheckpoint/Batch/Proposal、PromptSegment/Context 与 Tool DTO 及其不变量
- [ ] T047 [US2] 在 `src/bai_agent/memory/long_term.py` 实现 ruamel.yaml round-trip 加载、完整验证、长期记忆与来源/前沿联合原子提交和 revision/hash 检查
- [ ] T048 [US2] 在 `src/bai_agent/memory/long_term.py` 实现人工修改识别、manual 来源、last-valid 原子刷新、无效主文件只读回退及禁止自动整理状态
- [ ] T049 [US2] 在 `src/bai_agent/memory/selection.py` 实现短期窗口追踪、完整轮次/批次选择、长期记忆冲突优先和配置预算内的可追溯上下文选择
- [ ] T050 [US2] 在 `src/bai_agent/memory/curation.py` 实现边界触发、稳定 batch ID、最旧连续批次、结构化候选本地 Schema/来源/凭据/人工优先校验
- [ ] T051 [US2] 在 `src/bai_agent/memory/curation.py` 实现使用专用 persona/model profile 的非流式 JSON 整理、有界安全重试及“联合提交成功后才推进前沿”用例
- [ ] T052 [P] [US2] 在 `src/bai_agent/prompting/assembler.py` 扩展固定顺序的可信人格、不可信长期记忆/近期原文、预算降级、来源清单和显式数据边界组装
- [ ] T053 [P] [US2] 在 `src/bai_agent/tools/registry.py` 与 `src/bai_agent/tools/executor.py` 实现统一 ToolDefinition/Context/Result 注册、参数 Schema、人格权限和稳定错误基础
- [ ] T054 [US2] 在 `src/bai_agent/tools/memory_source.py` 实现只读 `memory_source_query`、revision 绑定游标、稳定排序分页、来源哈希校验和当前 flow 审计
- [ ] T055 [US2] 在 `src/bai_agent/runtime/controller.py` 集成整理前置门禁、长期/短期上下文、来源查询工具子轮次及失败时禁止 Provider 调用/提前修剪
- [ ] T056 [US2] 在 `src/bai_agent/cli.py` 实现完整 `memory validate` 与复用同一只读服务的 `memory source MEMORY_ID --cursor` 命令
- [ ] T057 [US2] 在 `src/bai_agent/runtime/tracing.py` 实现不含正文的 prompt source manifest、整理批次和来源查询审计字段
- [ ] T058 [US2] 运行 `tests/unit/test_long_term_models.py`、`tests/integration/test_long_term_store.py`、`tests/unit/test_curation_policy.py`、`tests/fault_injection/test_long_term_atomicity.py`、`tests/unit/test_memory_selection.py`、`tests/contract/test_prompt_context.py`、`tests/contract/test_memory_source_tool.py`、`tests/integration/test_curation_workflow.py`、`tests/integration/test_plaintext_permissions.py`，验证后提交 `src/bai_agent/memory/`、`src/bai_agent/prompting/assembler.py`、`src/bai_agent/tools/`、`src/bai_agent/runtime/`、`src/bai_agent/cli.py`、领域模型更新与 US2 测试

**Checkpoint**: US2 可在 FakeProvider/fixture 人格下独立验收，且不会因容量或整理丢失任何原始记录。

---

## Phase 5: User Story 3 - 通过独立配置定义人格（Priority: P3）

**Goal**: 从统一配置目录严格加载相互独立的聊天、状态和记忆整理人格，支持轮次间安全重载且不改历史记忆。

**Independent Test**: 分别替换三类人格文件并重载，后续 ConfigSnapshot/行为改变但原始段和长期 YAML 哈希不变；缺失、空白、角色错误、模板变量错误和路径逃逸全部停止生成，不使用默认人格兜底。

### Tests for User Story 3（先写并确认失败）

- [ ] T059 [P] [US3] 在 `tests/unit/test_persona_config.py` 为独立角色、缺失/空白/重复引用、非法 UTF-8、文件过大和聊天人格不得替代整理人格编写测试
- [ ] T060 [P] [US3] 在 `tests/unit/test_prompt_templates.py` 为严格 `string.Template.substitute()`、允许变量完全匹配、绝对/`..`/符号链接逃逸和不可信数据插槽编写测试
- [ ] T061 [P] [US3] 在 `tests/integration/test_persona_reload.py` 为 BR-008 编写人格只在轮次间重载、当前轮 revision 固定且历史原始/长期记忆哈希不变测试
- [ ] T062 [P] [US3] 在 `tests/contract/test_cli_config.py` 覆盖 `config validate`、`doctor`、可操作错误、秘密只检查存在性及输出不含提示正文/API Key 的 CLI 契约

### Implementation for User Story 3

- [ ] T063 [US3] 在 `src/bai_agent/config/loader.py` 实现 agent 入口引用图、人格/提示文件加载、内容哈希和轮次间原子 ConfigSnapshot 重载
- [ ] T064 [US3] 在 `src/bai_agent/config/validation.py` 实现 PersonaProfile 角色唯一性、严格模板标识符、编码/大小、配置根路径和跨文件引用完整校验
- [ ] T065 [US3] 在 `src/bai_agent/prompting/personas.py` 实现基础/状态/整理人格按职责读取与可信指令段生成，任何缺失均 fail-closed
- [ ] T066 [P] [US3] 完善 `config/personas/chat.md`、`config/personas/memory_curator.md`、`config/personas/states/default.md`、`config/prompts/chat_context.md`、`config/prompts/memory_curation.md`、`config/prompts/untrusted_memory_boundary.md` 的独立职责、严格变量和不可信数据边界
- [ ] T067 [US3] 在 `src/bai_agent/application.py` 实现轮次边界配置重载、persona/model profile 绑定及配置失败不生成响应且不改记忆
- [ ] T068 [US3] 在 `src/bai_agent/cli.py` 实现 `config validate` 与默认无网络的 `doctor`，输出 config revision、角色、状态和启用工具但不输出正文/秘密
- [ ] T069 [US3] 运行 `tests/unit/test_persona_config.py`、`tests/unit/test_prompt_templates.py`、`tests/integration/test_persona_reload.py`、`tests/contract/test_cli_config.py` 及既有记忆回归测试，验证后提交 `config/`、`src/bai_agent/config/`、`src/bai_agent/prompting/personas.py`、`src/bai_agent/application.py`、`src/bai_agent/cli.py` 与 US3 测试

**Checkpoint**: US3 可独立通过配置替换人格；人格修改不改变任何历史记忆。

---

## Phase 6: User Story 4 - 为状态相关人格预留扩展（Priority: P4）

**Goal**: 首版确定使用默认状态，同时证明三个测试状态及多人格可按稳定顺序组合且不改记忆核心。

**Independent Test**: 用同一 Controller/Memory/PromptAssembler 注入三个测试状态；默认解析不受任意文本影响，多人格按配置顺序加入提示并写入 RawRecord.state_id，缺失引用不产生部分响应。

### Tests for User Story 4（先写并确认失败）

- [ ] T070 [P] [US4] 在 `tests/unit/test_static_state_resolver.py` 验证任意用户/记忆/工具文本都不能改变默认状态，且每轮 resolution 含 resolver/version/reason
- [ ] T071 [P] [US4] 在 `tests/integration/test_state_persona_composition.py` 覆盖三个测试状态、多份人格确定顺序、RawRecord.state_id、无专属人格仍保留基础人格和缺失引用 fail-closed
- [ ] T072 [P] [US4] 在 `tests/contract/test_state_resolver.py` 验证替换测试 StateResolver 无需修改 Controller、MemoryRepository 或 PromptAssembler 契约

### Implementation for User Story 4

- [ ] T073 [US4] 在 `src/bai_agent/domain/models.py` 完善 AgentStateDefinition、StateResolutionContext/Result 及有序 persona ID 不重复/引用约束
- [ ] T074 [US4] 在 `src/bai_agent/states/resolver.py` 实现配置驱动的 StaticStateResolver，生产只选择 `default_state_id`，测试可注入其他已验证状态
- [ ] T075 [US4] 在 `src/bai_agent/prompting/assembler.py` 与 `src/bai_agent/runtime/controller.py` 集成有序状态人格、状态追踪和无效引用时生成前停止
- [ ] T076 [P] [US4] 在 `tests/fixtures/config-three-states/states.toml` 与 `tests/fixtures/config-three-states/personas/states/` 创建三个状态和多人格的非敏感验收配置
- [ ] T077 [US4] 运行 `tests/unit/test_static_state_resolver.py`、`tests/integration/test_state_persona_composition.py`、`tests/contract/test_state_resolver.py` 及 US1/US2 回归测试，验证后提交 `src/bai_agent/domain/models.py`、`src/bai_agent/states/resolver.py`、`src/bai_agent/prompting/assembler.py`、`src/bai_agent/runtime/controller.py`、状态 fixture 与 US4 测试

**Checkpoint**: 状态扩展边界可替换、可测试，首版仍只有确定的默认状态行为。

---

## Phase 7: User Story 5 - 安全接入未来工具和自主循环（Priority: P5）

**Goal**: 证明除来源查询外的工具和自主循环默认禁用；显式测试扩展受 Schema、权限、预算、审计、停止和凭据门禁约束。

**Independent Test**: 默认配置下额外 Provider/Tool/Loop 调用为 0；启用无副作用测试工具后可审计且不能伪造 persona/flow；受限测试循环在次数、deadline、预算、取消或人工信号下停止并复用同一 Controller。

### Tests for User Story 5（先写并确认失败）

- [ ] T078 [P] [US5] 在 `tests/contract/test_tool_registry.py` 为 BR-009/BR-010 覆盖未知/禁用/虚构工具、非法 JSON、额外/缺失参数、权限伪造、超时、结果过大和稳定错误码
- [ ] T079 [P] [US5] 在 `tests/contract/test_deepseek_tool_calls.py` 覆盖工具定义映射、多个调用、重复 call ID、无效 arguments、tool_call_id 回传和供应商 SDK 类型不外泄
- [ ] T080 [P] [US5] 在 `tests/integration/test_tool_extension.py` 为 BR-009/BR-010 验证默认仅来源查询可用、无副作用测试工具显式启用/禁用、审计关联和失败不破坏聊天/记忆
- [ ] T081 [P] [US5] 在 `tests/integration/test_autonomous_loop.py` 为 BR-009/BR-010 覆盖 DisabledLoopPolicy 零调用、最大迭代、deadline、token/成本预算、人工停止、取消重抛和幂等检查点恢复
- [ ] T082 [P] [US5] 在 `tests/integration/test_extension_security.py` 为 CR-001—CR-006 和 BR-010 覆盖工具/循环无凭据参数、提示注入不能扩大权限/修改配置/开启无限循环及安全诊断

### Implementation for User Story 5

- [ ] T083 [US5] 在 `src/bai_agent/domain/models.py` 与 `src/bai_agent/tools/registry.py` 完善 Provider-neutral ToolDefinition/Call/ExecutionContext/Result、安全 annotations 和本地 input/output JSON Schema
- [ ] T084 [US5] 在 `src/bai_agent/tools/executor.py` 实现宿主创建上下文、启用/人格权限交集、串行调用、deadline/轮数/结果大小限制和无正文审计
- [ ] T085 [US5] 在 `src/bai_agent/providers/deepseek.py` 与 `src/bai_agent/runtime/controller.py` 实现工具定义/调用/结果适配及配置有界工具子循环，拒绝重复/未知调用
- [ ] T086 [US5] 在 `src/bai_agent/runtime/loops.py` 实现 DisabledLoopPolicy、可替换 AutonomousLoopRunner 边界、单次复用 SingleTurnController、停止预算、检查点和取消清理
- [ ] T087 [US5] 在 `config/tools.toml` 与 `config/agent.toml` 完善未来工具默认关闭、来源查询例外、循环 disabled 和所有限制参数
- [ ] T088 [US5] 在 `src/bai_agent/runtime/tracing.py` 实现工具/循环调用的 persona/flow/turn/结果码/预算审计，并过滤 arguments、result 正文和凭据
- [ ] T089 [US5] 运行 `tests/contract/test_tool_registry.py`、`tests/contract/test_deepseek_tool_calls.py`、`tests/integration/test_tool_extension.py`、`tests/integration/test_autonomous_loop.py`、`tests/integration/test_extension_security.py` 及来源查询回归测试，验证后提交 `src/bai_agent/tools/`、`src/bai_agent/providers/deepseek.py`、`src/bai_agent/runtime/`、`config/agent.toml`、`config/tools.toml` 与 US5 测试

**Checkpoint**: 所有扩展默认安全；测试工具和循环能接入但不能绕过核心门禁。

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: 完成规模、跨版本、安全、文档、注释和全量验收门禁。

- [ ] T090 [P] 在 `tests/performance/test_startup.py` 与 `tests/fixtures/performance.py` 生成 10,000 条原始记录/1,000 条长期记忆，执行 20—30 次冷启动并断言 p95 小于 3 秒且启动零网络调用
- [ ] T091 [P] 在 `tests/integration/test_full_acceptance.py` 汇总 SC-001—SC-017 的 100 轮/10 重启、窗口整理、人工维护、来源查询、状态和扩展端到端验收
- [ ] T092 [P] 在 `tests/integration/test_repository_secret_safety.py` 扫描 `config/`、`tests/`、日志、提示追踪和 Git diff 中的测试凭据，断言可用凭据数量为 0
- [ ] T093 [P] 在 `tests/integration/test_packaging.py` 验证 Python 3.13/3.14 可安装包、`python -m bai_agent` 入口、Windows/Linux/macOS 路径处理和 UTF-8 文件行为
- [ ] T094 根据实际命令和结果更新 `README.md` 与 `specs/001-persistent-memory-agent/quickstart.md`，包含配置、外部凭据注入、聊天、pending 恢复、人工维护、来源查询、备份和故障处理
- [ ] T095 审计 `src/bai_agent/` 中模块边界、持久化不变量、安全门禁和非直观恢复分支的简体中文注释，补充 `[2026-07-19]` 或当前版本痕迹且不改写仍准确的既有注释
- [ ] T096 运行完整 `pytest`、性能 marker、`python -m bai_agent config validate --config-dir config`、隔离数据上的 `memory validate`、秘密扫描、`git diff --check` 与 `git status --short`，确认只 stage `src/bai_agent/`、`config/`、`tests/`、`README.md`、`pyproject.toml` 和本功能文档后创建最终原子提交

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup（Phase 1）**: 无依赖，可立即开始。
- **Foundational（Phase 2）**: 依赖 Setup，阻塞全部用户故事。
- **US1（Phase 3）**: 依赖 Foundational，形成持久化 MVP。
- **US2（Phase 4）**: 端到端实现依赖 US1 的原始归档和 Controller；其模型/选择测试可在 Foundation 后使用替身先行。
- **US3（Phase 5）**: 依赖 Foundational，可与 US1/US2 的大部分工作并行；最终接入使用既有 persona/config 端口。
- **US4（Phase 6）**: 端到端组合依赖 US2 的 PromptAssembler 和 US3 的人格配置；StateResolver 单元工作可在 Foundation 后先行。
- **US5（Phase 7）**: 依赖 US2 的统一来源工具/Controller、US3 的配置及 US4 的状态上下文。
- **Polish（Phase 8）**: 依赖所有计划交付的用户故事。

### User Story Dependency Graph

```text
Setup -> Foundation -> US1 -> US2 -----> US5 -> Polish
                    \-> US3 -> US4 ----/
                         \-------> US5
```

### Core Business Rule Coverage

| Rules | Test tasks |
|---|---|
| BR-001—BR-003 | T018—T024 |
| BR-004、BR-007、BR-015 | T040—T042 |
| BR-005—BR-006 | T037、T041—T042 |
| BR-008 | T059—T061 |
| BR-009—BR-010 | T078—T082 |
| BR-011—BR-013 | T037—T040、T044 |
| BR-014 | T038、T045 |
| BR-016 | T037—T040、T044 |
| BR-017—BR-018 | T042—T043 |

### Within Each User Story

- 必须先完成该故事的测试任务并确认失败，再开始对应实现。
- 领域模型/契约先于仓库、服务和适配器；服务先于 Controller/CLI 集成。
- 整理、工具和循环的模型输出必须先通过确定性本地校验。
- 故事末尾必须运行列出的测试和回归集，再创建只含该里程碑文件的提交。

## Parallel Opportunities

### Setup / Foundation

- T002 与 T003 可并行；T004—T007 可并行编写测试。
- T008、T009、T013、T015 修改不同文件，可并行；T010/T011/T012/T014/T016 按依赖收束。

### User Story 1

- 测试 T018—T024 可并行。
- 基础实现 T025、T028、T030、T031 修改不同模块，可并行；随后收束到 T026/T027/T029/T032—T035。

### User Story 2

- 测试 T037—T045 可并行。
- T052 与 T053 可在 T046 的 DTO 完成后并行；长期仓库 T047/T048、整理 T049—T051 和工具 T053/T054 分支最终在 T055 收束。

### User Story 3

- 测试 T059—T062 可并行。
- T066 可与 T063—T065 的代码实现并行，随后在 T067/T068 集成。

### User Story 4

- 测试 T070—T072 可并行；T076 fixture 可与 T073—T075 的代码工作并行。

### User Story 5

- 测试 T078—T082 可并行。
- 工具执行 T083/T084、Provider 映射 T085、循环 T086 和配置 T087 可按文件拆分后在 T088/T089 收束。

### Polish

- T090—T093 可并行运行/完善；结果稳定后再完成文档、注释和最终提交。

## Parallel Execution Examples

### User Story 1

```text
并行测试：T018、T019、T020、T021、T022、T023、T024
并行实现起点：T025（原子写）、T028（DeepSeek）、T030（默认状态）、T031（提示组装）
收束顺序：T026/T027/T029 -> T032 -> T033/T034/T035 -> T036
```

### User Story 2

```text
并行测试：T037、T038、T039、T040、T041、T042、T043、T044、T045
实现分支：T047/T048（YAML）、T049/T050/T051（整理）、T052（提示）、T053/T054（工具）
收束顺序：T046 -> 各实现分支 -> T055/T056/T057 -> T058
```

### User Story 3

```text
并行测试：T059、T060、T061、T062
实现分支：T063/T064/T065（加载与角色） || T066（提示文件）
收束顺序：各实现分支 -> T067/T068 -> T069
```

### User Story 4

```text
并行测试：T070、T071、T072
实现分支：T073/T074/T075（状态契约） || T076（三状态 fixture）
收束顺序：实现分支 -> T077
```

### User Story 5

```text
并行测试：T078、T079、T080、T081、T082
实现分支：T083/T084（工具） || T085（Provider） || T086（循环） || T087（配置）
收束顺序：实现分支 -> T088 -> T089
```

## Implementation Strategy

### MVP First（US1）

1. 完成 Setup 与 Foundational。
2. 完成 US1 的失败测试、实现和回归。
3. 停止并独立运行 100 轮/10 重启、故障恢复和 pending retry 验收。
4. 通过后提交并演示；此时已具备单一连续 Agent 的核心价值。

### Incremental Delivery

1. US1：永久原始归档与跨启动聊天。
2. US2：窗口整理、长期记忆、来源和人工维护。
3. US3：统一独立人格配置与安全重载。
4. US4：状态人格组合扩展边界。
5. US5：受控工具和自主循环扩展边界。
6. Polish：规模、跨版本、安全和完整 quickstart 验收。

每一步都必须保持此前故事测试通过，且可在该故事 Checkpoint 停止交付。

### Parallel Team Strategy

1. 团队共同完成 Setup/Foundation。
2. US1 建立归档/Controller 时，另一执行者可基于端口和 FakeProvider 开始 US3 配置实现。
3. US1 稳定后推进 US2；US3 完成后可并行准备 US4 状态 fixture/契约。
4. US2、US3、US4 收束后再接入 US5，避免工具/循环复制未稳定的核心逻辑。

## Notes

- `[P]` 只表示文件与未完成依赖允许并行，不代表可以跳过前置 Phase。
- 每个测试任务都必须在对应实现前产生可解释的失败，禁止先实现后补形式测试。
- 所有行为参数、提示词、模型名、URL、窗口、预算、重试、工具与循环开关均写入 `config/`，不得硬编码到 Python。
- 所有示例和 fixture 只使用明确不可用的凭据占位符。
- 原始记录永久保留；任务不得加入自动删除、清空会话或按启动周期分割记忆的行为。
- 只读来源查询必须始终复用同一 ToolRegistry/Repository 端口，不建立 CLI 或人格专用旁路。
- 每个重大阶段验证后创建原子 Git 提交，且不得 stage 用户已有或无关的未跟踪文件。
