# Tasks: 持久记忆聊天 Agent

**Input**: Design documents from `/specs/001-persistent-memory-agent/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

**Tests**: BR-001—BR-018 的自动化测试全部为强制任务，并位于对应实现任务之前。配置、凭据、原子写入、供应商协议、提示注入、并发、权限和性能测试也因契约或风险而强制执行。

**Organization**: 任务按用户故事分阶段。每个故事都给出独立验收方法；设计工件对齐、共享领域契约、配置骨架、安全门禁、凭据事件响应和首次仓库秘密扫描位于 Setup/Foundation。

**Constitution Gates**: 实现始终遵循“清晰可维护 → 解耦可读 → 简单实现”的优先级。非直观不变量、安全边界和恢复分支的新增注释必须使用简体中文并带 `[2026-07-19]` 或后续版本标记；准确的既有注释不改写、不删除。每个阶段必须在测试、仓库秘密扫描、选择性暂存、缓存区差异检查、注释审计和暂存文件清单复核通过后，只提交该阶段相关文件；任何凭据不得进入代码、配置、记忆、日志、测试或提交。

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

**Purpose**: 先消除分析发现的设计漂移，再建立稳定领域契约、配置骨架、凭据门禁、凭据事件响应和测试替身。

**⚠️ CRITICAL**: 本阶段完成前不得开始用户故事实现，也不得创建首次实现提交。

### Design alignment gate（任何代码前完成）

- [ ] T004 在 `specs/001-persistent-memory-agent/spec.md`、`specs/001-persistent-memory-agent/plan.md`、`specs/001-persistent-memory-agent/data-model.md`、`specs/001-persistent-memory-agent/contracts/configuration.md`、`specs/001-persistent-memory-agent/contracts/storage.md`、`specs/001-persistent-memory-agent/contracts/cli.md` 与 `specs/001-persistent-memory-agent/quickstart.md` 统一定义 MemoryCoverageOverview：它与长期记忆/来源/整理前沿保存在同一 `long_term.yaml` revision，由同一次 memory_curator 结构化响应更新而不新增 `memory_overview.md` 或第二次模型调用；每条已确认原始记录必须处于“尚在直接注入窗口”或“被连续 coverage span 覆盖”之一，空提取批次也扩展覆盖且缺口 fail-closed；同时统一 SC-008 为 10,000 条永久原始记录、规定 Windows 参考性能口径与三平台功能矩阵、定义确定性人格差异 oracle、POSIX/Windows DACL 判定、凭据事件阻塞命令、生产模块树，并消除 FR-026/FR-029 与 BR-004/BR-015 的重复措辞而不削弱不变量

### Foundation tests（先写并确认失败）

- [ ] T005 [P] 在 `tests/unit/test_domain_models.py` 为冻结 DTO、稳定 ID、UTC 时间、JSON 往返和非法枚举编写边界测试
- [ ] T006 [P] 在 `tests/unit/test_config_validation.py` 为 TOML 类型、交叉字段约束、配置根路径边界、缺失外部凭据、ConfigSnapshot revision，以及入口清单引用的全部人格/提示文件存在、非空和引用图完整性编写测试
- [ ] T007 [P] 在 `tests/unit/test_credentials.py` 与 `tests/unit/test_logging.py` 为凭据拒绝/不可逆脱敏及日志字段白名单编写泄露测试
- [ ] T008 [P] 在 `tests/contract/test_ports.py` 为 ModelProvider、MemoryRepository、Tool、StateResolver 和 LoopPolicy 替身的结构契约与 JSON DTO 往返编写测试

### Foundation implementation

- [ ] T009 [P] 在 `src/bai_agent/domain/models.py` 实现共享冻结 dataclass、Pydantic 边界模型、稳定 ID/时间/哈希值对象及 JSON 类型别名
- [ ] T010 [P] 在 `src/bai_agent/domain/errors.py` 实现稳定领域错误码、安全消息和 retryable 语义，禁止泄漏正文、凭据和绝对路径
- [ ] T011 在 `src/bai_agent/domain/ports.py` 定义存储、模型、工具、状态、配置时钟与循环 Protocol，保证核心层不导入供应商 SDK 类型
- [ ] T012 在 `src/bai_agent/config/loader.py` 实现只读 TOML 基础加载、显式入口清单和每轮不可变 ConfigSnapshot
- [ ] T013 在 `src/bai_agent/config/validation.py` 实现数值/引用/能力/本地数据根校验及配置根逃逸拒绝
- [ ] T014 [P] 在 `src/bai_agent/security/credentials.py` 与 `src/bai_agent/security/redaction.py` 实现写入前凭据检测、拒绝/不可逆脱敏及秘密环境变量读取边界
- [ ] T015 在 `src/bai_agent/runtime/tracing.py` 实现安全结构化日志基础设施，只允许稳定 ID、哈希、计数、时长、错误码和用量字段
- [ ] T016 [P] 按已由 T004 对齐的 `specs/001-persistent-memory-agent/contracts/configuration.md` 创建无真实凭据的 `config/agent.toml`、`config/providers.toml`、`config/states.toml`、`config/tools.toml`、`config/logging.toml`、`config/personas/chat.md`、`config/personas/memory_curator.md`、`config/personas/states/default.md`、`config/prompts/chat_context.md`、`config/prompts/memory_curation.md` 与 `config/prompts/untrusted_memory_boundary.md` 基线文件
- [ ] T017 在 `tests/conftest.py` 与 `tests/fakes.py` 实现隔离数据目录、确定性时钟/ID、FakeProvider、内存端口、输出捕获和故障注入共享 fixture

### Foundation safety gates（首次提交前）

- [ ] T018 [P] 在 `tests/integration/test_repository_secret_safety.py` 与 `tests/security_scanner.py` 扫描 `git ls-files` 覆盖的全部源码/注释/配置/人格/文档/测试、所有可达 Git 提交对象、工作区与暂存差异，以及存在时的 `build/`、`dist/`、运行日志、`data/runtime/prompt-traces/` 和隔离记忆 fixture；断言只允许明确无效占位符、可用凭据数量为 0，且扫描器自身输出不回显秘密值
- [ ] T019 [P] 在 `tests/integration/test_credential_incident_response.py` 用仅写入临时隔离目录的受控假凭据覆盖源码/注释/文档、Git 历史替身、原始记忆、长期记忆、生成制品、日志和提示追踪的泄露定位；断言报告只含逻辑 ID、指纹和受影响范围，且 `security incident check` 在轮换、全仓库扫描、运行数据/日志扫描和处置记录全部确认前保持非零退出码
- [ ] T020 在 `src/bai_agent/security/incidents.py` 与 `src/bai_agent/cli.py` 实现脱敏凭据指纹、受影响制品清单、处置 JSON、`security incident check` 和 `security incident acknowledge`：只有 rotation reference、repository scan revision、runtime/log scan revision 与处置记录齐全时才解除交付阻塞，任何输出均不得包含凭据或真实绝对路径
- [ ] T021 在 `docs/security-incident-response.md` 编写停止传播、撤销/轮换 DeepSeek 及未来 Provider 凭据、检查全部 tracked files/可达 Git 历史/生成制品/记忆/日志/追踪、记录处置结果、执行 `security incident check` 和解除交付阻塞的操作手册
- [ ] T022 运行 `tests/unit/test_domain_models.py`、`tests/unit/test_config_validation.py`、`tests/unit/test_credentials.py`、`tests/unit/test_logging.py`、`tests/contract/test_ports.py`、`tests/integration/test_repository_secret_safety.py`、`tests/integration/test_credential_incident_response.py` 与干净 fixture 上的 `python -m bai_agent security incident check`；仅暂存 `pyproject.toml`、`.gitignore`、`config/`、`docs/security-incident-response.md`、`specs/001-persistent-memory-agent/`、`src/bai_agent/domain/`、`src/bai_agent/config/`、`src/bai_agent/security/`、`src/bai_agent/cli.py`、`src/bai_agent/runtime/tracing.py` 及共享/Foundation 测试，执行 `git diff --cached --check`，复核新增/更新注释均为简体中文且带日期/版本痕迹，并用 `git diff --cached --name-only` 与 `git status --short` 确认无无关文件且安全门禁未处于阻塞状态后创建 Foundation 原子提交

**Checkpoint**: 共享契约、非敏感配置、凭据事件处置和首次仓库安全门禁可用；用户故事实现可以按依赖关系开始。

---

## Phase 3: User Story 1 - 跨启动延续同一个 Agent（Priority: P1）🎯 MVP

**Goal**: 完成单一连续记忆空间、输入/输出确认顺序、跨重启恢复、默认状态、最小完整提示上下文、原始文件权限和无会话 CLI。

**Independent Test**: 用 FakeProvider 完成 100 轮交互并重启 10 次，不选择会话即可恢复全部已确认记录；每次请求都包含基础人格、默认状态、有效的长期记忆段、近期原文和当前输入；在每个写入故障点只能恢复完整旧/新状态，Provider 失败后可显式恢复 pending turn，原始记忆与状态文件权限过宽或无法确认时产生安全告警。

### Tests for User Story 1（先写并确认失败）

- [ ] T023 [P] [US1] 在 `tests/unit/test_raw_record_archive.py` 为 BR-001/BR-002 的 RawRecord 序列、turn 配对、Unicode/多行正文、校验和与分段滚动编写单元和 Hypothesis 属性测试
- [ ] T024 [P] [US1] 在 `tests/integration/test_persistence_order.py` 为 BR-003 编写“用户输入早于 Provider 调用、Assistant 输出早于 stdout、失败内容不确认”的调用顺序测试
- [ ] T025 [P] [US1] 在 `tests/fault_injection/test_raw_atomicity.py` 为 BR-001/BR-003 覆盖临时创建、写入、flush、fsync、replace 前后中断和尾段半行损坏恢复
- [ ] T026 [P] [US1] 在 `tests/integration/test_restart_continuity.py` 为 BR-001/BR-002 实现 100 轮、10 次重启、空记忆、大量记忆及单一全局顺序验收测试
- [ ] T027 [P] [US1] 在 `tests/integration/test_writer_lock.py` 验证两个子进程竞争 `data/memory/.state/writer.lock` 时恰好一个获得写权且失败方不改文件
- [ ] T028 [P] [US1] 在 `tests/contract/test_deepseek_provider.py` 覆盖完整响应、错误归一化、有界重试、取消、截断、流中断不得成功及 `reasoning_content` 不外泄
- [ ] T029 [P] [US1] 在 `tests/contract/test_cli_chat.py` 覆盖无会话/线程命令、稳定退出码、Ctrl+C/EOF、pending turn 报告和 `--resume-pending` 幂等语义
- [ ] T030 [P] [US1] 在 `tests/unit/test_static_state_resolver.py` 验证任意用户/记忆/工具正文都不能改变默认状态，每轮 resolution 含 resolver/version/reason，且持久化 RawRecord 带 `state_id`
- [ ] T031 [P] [US1] 在 `tests/contract/test_mvp_prompt_context.py` 为 BR-005 验证基础人格、默认状态、允许为空但结构有效的长期记忆段、近期原文和当前输入均按确定顺序存在，缺少任一强制段时生成前失败
- [ ] T032 [P] [US1] 在 `tests/integration/test_raw_file_permissions.py` 验证新建目录/文件在 POSIX 分别收紧为 `0700`/`0600`；Windows DACL 仅允许当前用户、SYSTEM 与 Administrators，且 Everyone/Users/Authenticated Users 的读写 allow ACE 判为 `too_broad`，ACL 查询失败、网络共享或无法识别继承链判为 `unverifiable`；两种异常均必须产生稳定安全告警并使 `memory validate`/`doctor` 返回安全失败

### Implementation for User Story 1

- [ ] T033 [P] [US1] 在 `src/bai_agent/memory/recovery.py` 实现同目录临时写、flush、fsync、replace、残留临时文件识别和平台目录同步的原子写基础
- [ ] T034 [P] [US1] 在 `src/bai_agent/security/permissions.py` 实现 T004 契约规定的 POSIX mode 与 Windows DACL 检查/尽力收紧，拒绝跟随越界符号链接或 junction，并统一返回 `private`、`too_broad`、`unverifiable` 状态、稳定错误码和不含真实绝对路径的安全告警
- [ ] T035 [US1] 在 `src/bai_agent/memory/archive.py` 实现有界 JSONL 尾段重写、段滚动、严格全局序列、RawRecord 校验和、永久归档追加及共享权限门禁
- [ ] T036 [US1] 在 `src/bai_agent/memory/recovery.py` 实现正式段扫描、offset 派生索引、损坏隔离、序号缺口拒绝、单写者锁生命周期和 pending turn 恢复
- [ ] T037 [P] [US1] 在 `src/bai_agent/providers/deepseek.py` 实现隔离 OpenAI SDK 的 DeepSeek 完整响应适配器、配置参数映射、错误归一化和供应商字段过滤
- [ ] T038 [US1] 在 `src/bai_agent/providers/registry.py` 实现按配置选择 ModelProvider/profile、能力预检和未知适配器 fail-closed
- [ ] T039 [P] [US1] 在 `src/bai_agent/states/resolver.py` 实现首版只返回配置默认状态的 StaticStateResolver，使每条 RawRecord 都带显式 `state_id`
- [ ] T040 [P] [US1] 在 `src/bai_agent/prompting/assembler.py` 实现供 MVP 使用的基础人格、默认状态、有效长期记忆段、确定性近期原文和当前输入 PromptContext 组装，并通过端口接收全部内容而不硬编码提示词
- [ ] T041 [US1] 在 `src/bai_agent/runtime/controller.py` 实现 SingleTurnController 的输入先存、状态解析、强制提示段校验、完整生成、输出先存、幂等 turn ID、失败保留输入和安全取消顺序
- [ ] T042 [US1] 在 `src/bai_agent/application.py` 连接配置快照、写锁、原始归档、权限检查、状态解析、提示组装、Provider 与安全追踪用例
- [ ] T043 [US1] 在 `src/bai_agent/cli.py` 实现 `chat` 与原始归档版 `memory validate`、全局路径选项、稳定 JSON/错误输出、权限告警和 `--resume-pending`
- [ ] T044 [US1] 在 `src/bai_agent/__main__.py` 连接 CLI 入口、异步生命周期、Ctrl+C 退出码和锁释放，且不提供任何会话选择功能
- [ ] T045 [US1] 运行 `tests/unit/test_raw_record_archive.py`、`tests/integration/test_persistence_order.py`、`tests/fault_injection/test_raw_atomicity.py`、`tests/integration/test_restart_continuity.py`、`tests/integration/test_writer_lock.py`、`tests/contract/test_deepseek_provider.py`、`tests/contract/test_cli_chat.py`、`tests/unit/test_static_state_resolver.py`、`tests/contract/test_mvp_prompt_context.py`、`tests/integration/test_raw_file_permissions.py`、`tests/integration/test_repository_secret_safety.py` 及 `python -m bai_agent security incident check`；仅暂存 `src/bai_agent/memory/`、`src/bai_agent/providers/`、`src/bai_agent/security/permissions.py`、`src/bai_agent/states/resolver.py`、`src/bai_agent/prompting/assembler.py`、`src/bai_agent/runtime/controller.py`、`src/bai_agent/application.py`、`src/bai_agent/cli.py`、`src/bai_agent/__main__.py` 与 US1 测试，执行 `git diff --cached --check`，复核新增/更新注释均为简体中文且带日期/版本痕迹，并用 `git diff --cached --name-only` 与 `git status --short` 排除无关文件且安全门禁未处于阻塞状态后创建 US1 原子提交

**Checkpoint**: US1 可独立运行和验收；这是建议的首个可演示 MVP。

---

## Phase 4: User Story 2 - 组织长短期记忆并构造每轮上下文（Priority: P2）

**Goal**: 在窗口边界用一次整理调用原子提交长期记忆、多来源、覆盖概览和修剪前沿，使每条原始记录始终可由近期原文或可追溯概览代表，并支持人工维护、容量选择和统一只读来源查询。

**Independent Test**: 使用小窗口 fixture 输入重复、修正、稳定事实和无长期价值闲聊；阈值前整理调用为 0，边界只调用一次 memory_curator，成功后同一 YAML revision 推进来源、MemoryCoverageOverview coverage spans 和前沿，任一失败不修剪；断言每条已确认原始记录恰好处于概览覆盖或近期直接注入范围，空提取批次同样连续覆盖；有效人工修改生效、无效修改保留原文件并回退；所有人格同参查询得到相同只读来源。

### Tests for User Story 2（先写并确认失败）

- [ ] T046 [P] [US2] 在 `tests/unit/test_long_term_models.py` 为 BR-006/BR-012/BR-016 的 active/superseded/retracted、人工优先、来源非空、关系无环和悬空引用拒绝编写测试
- [ ] T047 [P] [US2] 在 `tests/integration/test_long_term_store.py` 为 BR-012/BR-014/BR-016 编写 YAML 注释往返、有效人工修改、无效文件不覆盖、last-valid 只读回退、来源哈希校验及长期文件权限测试
- [ ] T048 [P] [US2] 在 `tests/unit/test_curation_policy.py` 为 BR-011/BR-013 编写阈值前零调用、最旧连续完整轮次、批次上限、空提取可推进和失败不推进测试
- [ ] T049 [P] [US2] 在 `tests/fault_injection/test_long_term_atomicity.py` 为 BR-004/BR-013/BR-016 覆盖单次整理响应、Schema、外部并发编辑、YAML flush/fsync/replace，以及长期记忆/来源/MemoryCoverageOverview/前沿同 revision 联合提交各故障点
- [ ] T050 [P] [US2] 在 `tests/unit/test_memory_selection.py` 为 BR-004/BR-006/BR-007/BR-015 覆盖冲突优先、完整轮次选择、未选项候选资格、原始段不变，并证明从 sequence 1 到最新确认记录无缺口地满足“`<= curated_through_sequence` 被 coverage span 覆盖”或“`> curated_through_sequence` 仍在近期直接注入范围”；覆盖空提取批次、重叠/缺口拒绝、概览预算有界和 span 到 batch/record hash 可追溯
- [ ] T051 [P] [US2] 在 `tests/contract/test_prompt_context.py` 为 BR-005/BR-007/BR-018 覆盖有界 MemoryCoverageOverview 先于按相关性选择的长期明细、近期原文、预算/信任/coverage/source manifest、强制段缺失或覆盖有缺口时生成前失败，以及未调用工具时自动注入来源原文数量为 0
- [ ] T052 [P] [US2] 在 `tests/contract/test_memory_source_tool.py` 为 BR-017/BR-018 覆盖聊天/整理/状态/两个辅助人格同参结果、稳定分页/错误、当前 flow 隔离和调用前后权威文件哈希不变
- [ ] T053 [P] [US2] 在 `tests/integration/test_curation_workflow.py` 为 BR-011/BR-012/BR-013/BR-016 实现窗口整理、一次模型调用、有界重试、人工优先、重启去重、多来源、空提取连续覆盖，以及长期记忆/来源/MemoryCoverageOverview/前沿同 revision 端到端测试
- [ ] T054 [P] [US2] 在 `tests/integration/test_plaintext_permissions.py` 为 BR-014 和 CR-002/CR-005/CR-006 验证明文可读、长期 YAML/last-valid 复用 T032 的精确权限判定，以及原始/长期/概览/工具结果均不能持久化测试凭据

### Implementation for User Story 2

- [ ] T055 [US2] 在 `src/bai_agent/domain/models.py` 增加 LongTermMemoryDocument/Item、SourceReference、MemoryCoverageOverview/CoverageSpan、CurationCheckpoint/Batch/Proposal、PromptSegment/Context 与 Tool DTO，强制 span 连续不重叠、绑定 batch/sequence/record hash、覆盖到整理前沿且与文档 revision 一致
- [ ] T056 [US2] 在 `src/bai_agent/memory/long_term.py` 实现 ruamel.yaml round-trip 加载、完整验证、长期记忆/来源/MemoryCoverageOverview/前沿联合原子提交、revision/hash 检查及共享权限门禁
- [ ] T057 [US2] 在 `src/bai_agent/memory/long_term.py` 实现人工修改识别、manual 来源、last-valid 原子刷新、无效主文件只读回退及禁止自动整理状态
- [ ] T058 [US2] 在 `src/bai_agent/memory/selection.py` 实现短期窗口追踪、完整轮次/批次选择、长期记忆冲突优先、配置预算内的相关明细选择，以及无需扫描原始正文全集的 coverage span/近期窗口完整性判定
- [ ] T059 [US2] 在 `src/bai_agent/memory/long_term.py` 实现 MemoryCoverageOverview 的持久化、连续 coverage span 校验、有界 overview text 与 source manifest；人工修改只改变经校验的长期项并按同一 revision 更新其覆盖引用，不创建独立概览文件或第二事实来源
- [ ] T060 [US2] 在 `src/bai_agent/memory/curation.py` 实现边界触发、稳定 batch ID、最旧连续批次、结构化候选本地 Schema/来源/凭据/人工优先校验
- [ ] T061 [US2] 在 `src/bai_agent/memory/curation.py` 实现使用既有 `config/prompts/memory_curation.md` 和专用 persona/model profile 的单次非流式 JSON 整理，使同一响应同时给出长期记忆候选与有界 overview update；执行 Schema/覆盖/来源/凭据校验、有界安全重试，并仅在长期记忆、来源、MemoryCoverageOverview 联合提交成功后推进前沿
- [ ] T062 [P] [US2] 在 `src/bai_agent/prompting/assembler.py` 扩展固定顺序的可信人格、不可信 MemoryCoverageOverview、相关长期明细、近期原文、预算降级、coverage/source manifest 和显式数据边界组装
- [ ] T063 [P] [US2] 在 `src/bai_agent/tools/registry.py` 与 `src/bai_agent/tools/executor.py` 实现统一 ToolDefinition/Context/Result 注册、参数 Schema、人格权限和稳定错误基础
- [ ] T064 [US2] 在 `src/bai_agent/tools/memory_source.py` 实现只读 `memory_source_query`、revision 绑定游标、稳定排序分页、来源哈希校验和当前 flow 审计
- [ ] T065 [US2] 在 `src/bai_agent/runtime/controller.py` 集成整理前置门禁、MemoryCoverageOverview/相关明细/短期上下文、来源查询工具子轮次，并在 coverage 缺口或整理失败时禁止 Provider 调用和提前修剪
- [ ] T066 [US2] 在 `src/bai_agent/cli.py` 实现完整 `memory validate` 与复用同一只读服务的 `memory source MEMORY_ID --cursor` 命令，并报告 coverage span 缺口/重叠、来源和权限异常
- [ ] T067 [US2] 在 `src/bai_agent/runtime/tracing.py` 实现不含正文的 prompt source manifest、overview revision、covered/direct sequence 范围、整理批次和来源查询审计字段
- [ ] T068 [US2] 运行 `tests/unit/test_long_term_models.py`、`tests/integration/test_long_term_store.py`、`tests/unit/test_curation_policy.py`、`tests/fault_injection/test_long_term_atomicity.py`、`tests/unit/test_memory_selection.py`、`tests/contract/test_prompt_context.py`、`tests/contract/test_memory_source_tool.py`、`tests/integration/test_curation_workflow.py`、`tests/integration/test_plaintext_permissions.py`、US1 回归、`tests/integration/test_repository_secret_safety.py` 及 `python -m bai_agent security incident check`；仅暂存 `src/bai_agent/domain/models.py`、`src/bai_agent/memory/`、`src/bai_agent/prompting/assembler.py`、`src/bai_agent/tools/`、`src/bai_agent/runtime/`、`src/bai_agent/cli.py` 与 US2 测试，执行 `git diff --cached --check`，复核新增/更新注释均为简体中文且带日期/版本痕迹，并用 `git diff --cached --name-only` 与 `git status --short` 排除无关文件且安全门禁未处于阻塞状态后创建 US2 原子提交

**Checkpoint**: US2 可在 FakeProvider/fixture 人格下独立验收，完整记忆范围始终参与上下文，且不会因容量或整理丢失任何原始记录。

---

## Phase 5: User Story 3 - 通过独立配置定义人格（Priority: P3）

**Goal**: 从统一配置目录严格加载相互独立的聊天、状态和记忆整理人格及全部提示模板，支持轮次间安全重载且不改历史记忆。

**Independent Test**: 分别替换三类人格文件并重载，使用两个带稳定标记的合法聊天人格和 FakeProvider 捕获最终 PromptContext，确定性证明后续人格段/revision 改变而原始段和长期 YAML 哈希不变；缺失、空白、角色错误、模板变量错误和路径逃逸全部停止生成，不使用随机模型措辞、默认人格或硬编码提示兜底。

### Tests for User Story 3（先写并确认失败）

- [ ] T069 [P] [US3] 在 `tests/unit/test_persona_config.py`、`tests/fixtures/config-persona-a/` 与 `tests/fixtures/config-persona-b/` 为独立角色、两个含不同稳定标记的合法聊天人格、缺失/空白/重复引用、非法 UTF-8、文件过大和聊天人格不得替代整理人格编写测试
- [ ] T070 [P] [US3] 在 `tests/unit/test_prompt_templates.py` 为严格 `string.Template.substitute()`、允许变量完全匹配、绝对/`..`/符号链接逃逸、不可信数据插槽，以及 `memory_curation.md` 的长期候选/overview update 单次结构化输出变量编写测试
- [ ] T071 [P] [US3] 在 `tests/integration/test_persona_reload.py` 为 BR-008 使用 FakeProvider 捕获最终 PromptContext，断言 persona A/B 稳定标记和 config revision 只在轮次间确定性切换、当前轮固定且历史原始/长期记忆哈希不变，不以真实模型自然语言输出作为行为差异 oracle
- [ ] T072 [P] [US3] 在 `tests/contract/test_cli_config.py` 覆盖 `config validate`、`doctor`、完整提示引用图、可操作错误、秘密只检查存在性及输出不含提示正文/API Key 的 CLI 契约

### Implementation for User Story 3

- [ ] T073 [US3] 在 `src/bai_agent/config/loader.py` 实现 agent 入口引用图、人格/提示文件加载、内容哈希和轮次间原子 ConfigSnapshot 重载
- [ ] T074 [US3] 在 `src/bai_agent/config/validation.py` 实现 PersonaProfile 角色唯一性、严格模板标识符、编码/大小、配置根路径和跨文件引用完整校验
- [ ] T075 [US3] 在 `src/bai_agent/prompting/personas.py` 实现基础/状态/整理人格按职责读取与可信指令段生成，任何缺失均 fail-closed
- [ ] T076 [P] [US3] 完善 `config/personas/chat.md`、`config/personas/memory_curator.md`、`config/personas/states/default.md`、`config/prompts/chat_context.md`、`config/prompts/memory_curation.md` 与 `config/prompts/untrusted_memory_boundary.md` 的独立职责、严格变量、不可信数据边界及单次整理响应中的 overview update 结构要求
- [ ] T077 [US3] 在 `src/bai_agent/application.py` 实现轮次边界配置重载、persona/model profile 绑定及配置失败不生成响应且不改记忆
- [ ] T078 [US3] 在 `src/bai_agent/cli.py` 实现 `config validate` 与默认无网络的 `doctor`，输出 config revision、角色、提示、状态和启用工具但不输出正文/秘密
- [ ] T079 [US3] 运行 `tests/unit/test_persona_config.py`、`tests/unit/test_prompt_templates.py`、`tests/integration/test_persona_reload.py`、`tests/contract/test_cli_config.py`、既有记忆回归、`tests/integration/test_repository_secret_safety.py` 及 `python -m bai_agent security incident check`；仅暂存 `config/`、`src/bai_agent/config/`、`src/bai_agent/prompting/personas.py`、`src/bai_agent/application.py`、`src/bai_agent/cli.py`、persona fixtures 与 US3 测试，执行 `git diff --cached --check`，复核新增/更新注释均为简体中文且带日期/版本痕迹，并用 `git diff --cached --name-only` 与 `git status --short` 排除无关文件且安全门禁未处于阻塞状态后创建 US3 原子提交

**Checkpoint**: US3 可独立通过配置替换人格和提示模板；人格修改不改变任何历史记忆。

---

## Phase 6: User Story 4 - 为状态相关人格预留扩展（Priority: P4）

**Goal**: 在 US1 已验证默认静态状态的基础上，证明三个测试状态及多人格可按稳定顺序组合且不改记忆核心。

**Independent Test**: 用同一 Controller/Memory/PromptAssembler 注入三个测试状态；多人格按配置顺序加入提示并写入 RawRecord.state_id，替换 StateResolver 不需修改记忆仓库或提示组装契约，缺失引用不产生部分响应。

### Tests for User Story 4（先写并确认失败）

- [ ] T080 [P] [US4] 在 `tests/integration/test_state_persona_composition.py` 覆盖三个测试状态、多份人格确定顺序、RawRecord.state_id、无专属人格仍保留基础人格和缺失引用 fail-closed
- [ ] T081 [P] [US4] 在 `tests/contract/test_state_resolver.py` 验证替换测试 StateResolver 无需修改 Controller、MemoryRepository 或 PromptAssembler 契约

### Implementation for User Story 4

- [ ] T082 [US4] 在 `src/bai_agent/domain/models.py` 完善 AgentStateDefinition、StateResolutionContext/Result 及有序 persona ID 不重复/引用约束
- [ ] T083 [US4] 在 `src/bai_agent/states/resolver.py` 完善配置驱动的 StaticStateResolver，生产只选择 `default_state_id`，测试可注入其他已验证状态
- [ ] T084 [US4] 在 `src/bai_agent/prompting/assembler.py` 与 `src/bai_agent/runtime/controller.py` 集成有序状态人格、状态追踪和无效引用时生成前停止
- [ ] T085 [P] [US4] 在 `tests/fixtures/config-three-states/states.toml` 与 `tests/fixtures/config-three-states/personas/states/` 创建三个状态和多人格的非敏感验收配置
- [ ] T086 [US4] 运行 `tests/unit/test_static_state_resolver.py`、`tests/integration/test_state_persona_composition.py`、`tests/contract/test_state_resolver.py`、US1/US2 回归、`tests/integration/test_repository_secret_safety.py` 及 `python -m bai_agent security incident check`；仅暂存 `src/bai_agent/domain/models.py`、`src/bai_agent/states/resolver.py`、`src/bai_agent/prompting/assembler.py`、`src/bai_agent/runtime/controller.py`、状态 fixture 与 US4 测试，执行 `git diff --cached --check`，复核新增/更新注释均为简体中文且带日期/版本痕迹，并用 `git diff --cached --name-only` 与 `git status --short` 排除无关文件且安全门禁未处于阻塞状态后创建 US4 原子提交

**Checkpoint**: 状态扩展边界可替换、可测试，首版仍只有确定的默认状态行为。

---

## Phase 7: User Story 5 - 安全接入未来工具和自主循环（Priority: P5）

**Goal**: 证明除来源查询外的工具和自主循环默认禁用；显式测试扩展受 Schema、权限、预算、审计、停止和凭据门禁约束。

**Independent Test**: 默认配置下额外 Provider/Tool/Loop 调用为 0；启用无副作用测试工具后可审计且不能伪造 persona/flow；受限测试循环在次数、deadline、预算、取消或人工信号下停止并复用同一 Controller。

### Tests for User Story 5（先写并确认失败）

- [ ] T087 [P] [US5] 在 `tests/contract/test_tool_registry.py` 为 BR-009/BR-010 覆盖未知/禁用/虚构工具、非法 JSON、额外/缺失参数、权限伪造、超时、结果过大和稳定错误码
- [ ] T088 [P] [US5] 在 `tests/contract/test_deepseek_tool_calls.py` 覆盖工具定义映射、多个调用、重复 call ID、无效 arguments、tool_call_id 回传和供应商 SDK 类型不外泄
- [ ] T089 [P] [US5] 在 `tests/integration/test_tool_extension.py` 为 BR-009/BR-010 验证默认仅来源查询可用、无副作用测试工具显式启用/禁用、每次调用/结果可关联触发 RawRecord、persona、flow、turn 与 `state_id`，且失败不破坏聊天/记忆
- [ ] T090 [P] [US5] 在 `tests/integration/test_autonomous_loop.py` 为 BR-009/BR-010 覆盖 DisabledLoopPolicy 零调用、最大迭代、deadline、token/成本预算、人工停止、取消重抛和幂等检查点恢复
- [ ] T091 [P] [US5] 在 `tests/integration/test_extension_security.py` 为 CR-001—CR-006 和 BR-010 覆盖工具/循环无凭据参数、提示注入不能扩大权限/修改配置/开启无限循环及安全诊断

### Implementation for User Story 5

- [ ] T092 [US5] 在 `src/bai_agent/domain/models.py` 与 `src/bai_agent/tools/registry.py` 完善 Provider-neutral ToolDefinition/Call/ExecutionContext/Result、安全 annotations 和本地 input/output JSON Schema
- [ ] T093 [US5] 在 `src/bai_agent/tools/executor.py` 实现宿主创建上下文、启用/人格权限交集、串行调用、deadline/轮数/结果大小限制和无正文审计
- [ ] T094 [US5] 在 `src/bai_agent/providers/deepseek.py` 与 `src/bai_agent/runtime/controller.py` 实现工具定义/调用/结果适配及配置有界工具子循环，拒绝重复/未知调用
- [ ] T095 [US5] 在 `src/bai_agent/runtime/loops.py` 实现 DisabledLoopPolicy、可替换 AutonomousLoopRunner 边界、单次复用 SingleTurnController、停止预算、检查点和取消清理
- [ ] T096 [US5] 在 `config/tools.toml` 与 `config/agent.toml` 完善未来工具默认关闭、来源查询例外、循环 disabled 和所有限制参数
- [ ] T097 [US5] 在 `src/bai_agent/runtime/tracing.py` 实现工具/循环调用的 trigger record/persona/flow/turn/`state_id`/结果码/预算审计，并过滤 arguments、result 正文和凭据
- [ ] T098 [US5] 运行 `tests/contract/test_tool_registry.py`、`tests/contract/test_deepseek_tool_calls.py`、`tests/integration/test_tool_extension.py`、`tests/integration/test_autonomous_loop.py`、`tests/integration/test_extension_security.py`、来源查询回归、`tests/integration/test_repository_secret_safety.py` 及 `python -m bai_agent security incident check`；仅暂存 `src/bai_agent/domain/models.py`、`src/bai_agent/tools/`、`src/bai_agent/providers/deepseek.py`、`src/bai_agent/runtime/`、`config/agent.toml`、`config/tools.toml` 与 US5 测试，执行 `git diff --cached --check`，复核新增/更新注释均为简体中文且带日期/版本痕迹，并用 `git diff --cached --name-only` 与 `git status --short` 排除无关文件且安全门禁未处于阻塞状态后创建 US5 原子提交

**Checkpoint**: 所有扩展默认安全；测试工具和循环能接入但不能绕过核心门禁。

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: 完成规模、跨版本、安全、文档、注释和全量验收门禁。

- [ ] T099 [P] 在 `tests/performance/test_startup.py`、`tests/fixtures/performance.py` 与 `tests/performance/baselines/windows-reference.json` 生成 10,000 条永久原始记录（近期直接窗口保持配置上限）和 1,000 条长期记忆，在指定 Windows 参考环境执行至少 100 次全新进程启动；从进程创建计时到配置、原始索引、长期 YAML 与 MemoryCoverageOverview 可供首轮组装，记录 OS/CPU/内存/存储/Python/缓存策略并按 nearest-rank 计算 p95，断言不超过 3 秒且网络调用为 0
- [ ] T100 [P] 在 `tests/integration/test_full_acceptance.py` 汇总 SC-001—SC-017，明确覆盖 SC-003 中每条原始记录属于 coverage span 或近期直接窗口、SC-005 中 FakeProvider 捕获两份人格稳定标记、SC-009 中全部 tracked files/可达 Git 历史/生成制品/运行数据零凭据、SC-014 中 1,000 条长期记忆来源完整性，以及 100 轮/10 重启、窗口整理、人工维护、来源查询、状态和扩展端到端验收
- [ ] T101 [P] 在 `tests/integration/test_packaging.py` 与 `.github/workflows/compatibility.yml` 建立 Windows/Ubuntu/macOS × Python 3.13/3.14 的真实运行矩阵，逐项验证可安装包、`python -m bai_agent` 入口、权限结果归一化、本地路径/原子替换、UTF-8 行为和非 Windows 平台功能回归；3 秒性能门槛只在 T004 指定的 Windows 参考环境判定
- [ ] T102 根据实际命令和结果更新 `README.md` 与 `specs/001-persistent-memory-agent/quickstart.md`，包含配置、外部凭据注入、聊天、pending 恢复、人工维护、MemoryCoverageOverview/近期窗口完整覆盖、来源查询、备份、权限判定、性能复现实验和三平台矩阵，并链接 `docs/security-incident-response.md` 的全仓库泄露处置流程
- [ ] T103 审计 `src/bai_agent/` 中模块边界、持久化不变量、安全门禁、MemoryCoverageOverview 和非直观恢复分支的简体中文注释，补充 `[2026-07-19]` 或当前版本痕迹且不改写仍准确的既有注释
- [ ] T104 运行完整 `pytest`、T099 的 Windows 参考性能 marker、`.github/workflows/compatibility.yml` 三平台矩阵、`python -m bai_agent config validate --config-dir config`、隔离数据上的 `memory validate`、`tests/integration/test_repository_secret_safety.py` 与 `python -m bai_agent security incident check`；仅暂存 `src/bai_agent/`、`config/`、`tests/`、`.github/workflows/compatibility.yml`、`docs/`、`README.md`、`pyproject.toml` 与本功能文档，执行 `git diff --cached --check`，复核新增/更新注释均为简体中文且带日期/版本痕迹，并用 `git diff --cached --name-only` 与 `git status --short` 排除无关文件且安全门禁未处于阻塞状态后创建最终原子提交

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup（Phase 1）**: 无依赖，可立即开始。
- **Foundational（Phase 2）**: 依赖 Setup，阻塞全部用户故事；T004 必须先消除设计工件漂移，T018—T021 必须在首次实现提交 T022 前完成。
- **US1（Phase 3）**: 依赖 Foundational，形成包含默认状态、最小完整上下文和原始文件权限的持久化 MVP。
- **US2（Phase 4）**: 端到端实现依赖 US1 的原始归档和 Controller；其模型、概览和选择测试可在 Foundation 后使用替身先行。
- **US3（Phase 5）**: 依赖 Foundational，可与 US1/US2 的大部分工作并行；最终接入使用既有 persona/config 端口。
- **US4（Phase 6）**: 端到端组合依赖 US2 的 PromptAssembler 和 US3 的人格配置；默认静态状态已在 US1 测试和实现。
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
| BR-001—BR-003 | T023—T029 |
| BR-004、BR-007、BR-015 | T049—T051 |
| BR-005—BR-006 | T031、T046、T050—T051 |
| BR-008 | T069—T071 |
| BR-009—BR-010 | T087—T091 |
| BR-011—BR-013 | T046—T049、T053 |
| BR-014 | T032、T047、T054 |
| BR-016 | T046—T049、T053 |
| BR-017—BR-018 | T051—T052 |

### Within Each User Story

- T004 对齐后的 spec/plan/data-model/contracts 是后续任务的唯一设计依据；未完成时不得编写业务测试或生产代码。
- 必须先完成该故事的测试任务并确认失败，再开始对应实现；US1 的 T030/T031 必须先于 T039/T040。
- 领域模型/契约先于仓库、服务和适配器；服务先于 Controller/CLI 集成。
- 整理、工具和循环的模型输出必须先通过确定性本地校验。
- 故事末尾必须运行列出的测试、全仓库/历史秘密扫描和 `security incident check`，再执行选择性暂存、缓存区差异/注释/文件清单检查并创建原子提交。

## Parallel Opportunities

### Setup / Foundation

- T002 与 T003 可并行；T004 完成后，T005—T008 可并行编写测试。
- T009、T010、T014、T016 修改不同文件，可并行；T018 与 T019 可在基础实现形成后并行，随后收束到 T020—T022。

### User Story 1

- 测试 T023—T032 可并行。
- 基础实现 T033、T034、T037、T039、T040 修改不同模块，可并行；随后收束到 T035/T036/T038，再接入 T041—T044。

### User Story 2

- 测试 T046—T054 可并行。
- T056/T057、T058/T059/T060/T061、T062 和 T063/T064 可在 T055 的 DTO 完成后按文件分支推进，最终在 T065—T068 收束。

### User Story 3

- 测试 T069—T072 可并行。
- T076 可与 T073—T075 的代码实现并行，随后在 T077—T079 集成。

### User Story 4

- 测试 T080 与 T081 可并行；T085 fixture 可与 T082—T084 的代码工作并行。

### User Story 5

- 测试 T087—T091 可并行。
- 工具执行 T092/T093、Provider 映射 T094、循环 T095 和配置 T096 可按文件拆分后在 T097/T098 收束。

### Polish

- T099—T101 可并行运行/完善；结果稳定后再完成文档、注释和最终提交。

## Parallel Execution Examples

### User Story 1

```text
并行测试：T023、T024、T025、T026、T027、T028、T029、T030、T031、T032
并行实现起点：T033（原子写）、T034（权限）、T037（DeepSeek）、T039（默认状态）、T040（MVP 提示）
收束顺序：T035/T036/T038 -> T041 -> T042/T043/T044 -> T045
```

### User Story 2

```text
并行测试：T046、T047、T048、T049、T050、T051、T052、T053、T054
实现分支：T056/T057（YAML） || T058/T059/T060/T061（选择、概览与整理） || T062（提示） || T063/T064（工具）
收束顺序：T055 -> 各实现分支 -> T065/T066/T067 -> T068
```

### User Story 3

```text
并行测试：T069、T070、T071、T072
实现分支：T073/T074/T075（加载与角色） || T076（提示文件）
收束顺序：各实现分支 -> T077/T078 -> T079
```

### User Story 4

```text
并行测试：T080、T081
实现分支：T082/T083/T084（状态契约） || T085（三状态 fixture）
收束顺序：实现分支 -> T086
```

### User Story 5

```text
并行测试：T087、T088、T089、T090、T091
实现分支：T092/T093（工具） || T094（Provider） || T095（循环） || T096（配置）
收束顺序：实现分支 -> T097 -> T098
```

## Implementation Strategy

### MVP First（US1）

1. 完成 Setup 与 Foundational；先通过 T004 对齐设计工件，再完成全仓库/历史秘密扫描和可执行凭据泄露处置门禁。
2. 完成 US1 的失败测试、实现和回归；默认状态、最小完整提示上下文和原始文件权限均属于 MVP，不后置。
3. 停止并独立运行 100 轮/10 重启、故障恢复、pending retry、强制提示段和权限告警验收。
4. 通过选择性暂存、缓存区检查、注释审计和秘密扫描后提交并演示；此时已具备单一连续 Agent 的核心价值。

**Suggested MVP scope**: T001—T045。

### Incremental Delivery

1. US1：永久原始归档、默认状态、最小完整上下文与跨启动聊天。
2. US2：单次窗口整理、MemoryCoverageOverview、长期记忆、来源和人工维护。
3. US3：统一独立人格/提示配置与安全重载。
4. US4：状态人格组合扩展边界。
5. US5：受控工具和自主循环扩展边界。
6. Polish：规模、跨版本、安全、事件响应和完整 quickstart 验收。

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
- 每轮生成前必须证明从第一条到最新确认原始记录不存在表示缺口：已整理序列由 MemoryCoverageOverview 的连续 span 覆盖，未整理序列仍在近期直接注入范围；相关长期明细按预算选择，来源原文只在显式工具调用后进入当前 flow。
- 只读来源查询必须始终复用同一 ToolRegistry/Repository 端口，不建立 CLI 或人格专用旁路。
- 仓库秘密扫描必须覆盖全部 tracked files、可达 Git 历史和适用生成/运行制品；`security incident check` 非零时禁止创建阶段完成提交或发布。
- 每个重大阶段验证后创建原子 Git 提交，且不得 stage 用户已有或无关的未跟踪文件。
