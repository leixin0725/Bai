# Data Model: 持久记忆聊天 Agent

**Date**: 2026-07-19
**Source**: [spec.md](./spec.md) · [research.md](./research.md)

## 设计约定

- 所有持久化文本使用 UTF-8、LF；时间使用带时区的 RFC 3339 UTC 字符串。
- 稳定 ID 由类型前缀和 UUIDv4 组成；顺序只由严格递增的 `global_sequence` 决定，不从 ID 或时间推断。
- 核心值对象不可变；读取后先校验再进入领域层。
- 原始记录和长期记忆均为明文，但写入前必须通过凭据拒绝/脱敏门禁。
- “修剪”仅表示不再作为近期原文自动注入，不表示删除或改写原始记录。
- 长期记忆正文、来源引用和整理前沿必须共同提交，不能形成独立事实来源。

## 1. RawRecord（原始记录）

永久保存的一条用户输入或 Agent 最终输出，对应 JSONL 中一行。

| Field | Type | Required | Rules |
|---|---|---:|---|
| `schema_version` | integer | yes | 首版为配置所支持的版本；未知版本拒绝加载 |
| `record_id` | string | yes | `rec-<uuid>`；全局唯一且创建后不变 |
| `global_sequence` | integer | yes | 从 1 开始严格递增，不允许缺口、倒序或重复 |
| `turn_id` | string | yes | `turn-<uuid>`；关联同一轮用户输入与 Agent 输出 |
| `role` | enum | yes | `user` 或 `assistant`；工具结果不混入原始聊天归档 |
| `content` | string | yes | 非空；已经过凭据门禁；保留原文换行 |
| `created_at` | datetime | yes | 持久化时的 UTC 时间 |
| `state_id` | string | yes | 生成/接收该记录时解析出的状态 |
| `config_revision` | string | yes | 本轮不可变配置快照哈希 |
| `content_sha256` | string | yes | 规范化 UTF-8 内容摘要，用于发现外部损坏 |

不变量：

- 用户记录必须在任何模型请求前确认持久化。
- Assistant 记录只能保存完整的最终输出，并在展示前确认持久化。
- 同一个 `turn_id` 最多一条用户记录和一条已确认 Assistant 记录；模型失败时允许只有用户记录。
- 已确认记录永不因整理、摘要、修剪、冲突处理或来源查询而删除。

## 2. RecordSegment（记录分段）

`data/memory/raw/00000001.jsonl` 形式的有界容器。段号和记录序号可从内容重建，不维护第二份权威 manifest。

| Property | Rules |
|---|---|
| filename | 8 位递增段号；不符合命名的文件不参与加载 |
| size | 同时受 `segment_max_records` 和 `segment_max_bytes` 配置限制 |
| sealed segment | 除修复工具外不可改写；只读扫描 |
| active segment | 唯一尾段；每次追加通过同目录临时文件原子替换 |
| temporary file | 永不视为已提交；恢复时保留或报告，不自动当成正式段 |

运行时从所有有效段建立派生索引：

```text
record_id -> (segment_path, byte_offset, byte_length, global_sequence)
```

该索引只驻留内存，损坏时可从原始段完整重建，因此不是权威数据。

## 3. LongTermMemoryDocument（长期记忆文档）

`data/memory/long_term.yaml` 的根对象，也是长期记忆、来源索引和整理前沿的单一事实来源。

| Field | Type | Required | Rules |
|---|---|---:|---|
| `schema_version` | integer | yes | 未知版本 fail closed，不静默迁移 |
| `revision` | integer | yes | 每次程序提交递增；人工修改需经校验后生成下一修订 |
| `curation` | CurationCheckpoint | yes | 与 `memories`、`coverage_overview` 在同一次原子替换中提交 |
| `coverage_overview` | MemoryCoverageOverview | yes | revision 必须与根文档相同；连续覆盖 `1..curated_through_sequence` |
| `memories` | list[LongTermMemoryItem] | yes | ID 唯一；允许空列表 |

加载规则：

1. 解析并做大小/数量限制。
2. 校验字段、ID、关系、来源存在性和整理前沿。
3. 有效时刷新 `.state/long_term.last-valid.yaml`。
4. 无效时不覆盖人工编辑文件，回退最近有效副本供只读使用，并禁止新整理写入直至修复。
5. 写入前比较加载时内容哈希；若外部编辑器改过文件，则中止提交并要求重新加载。

## 4. CurationCheckpoint（整理检查点）

| Field | Type | Required | Rules |
|---|---|---:|---|
| `curated_through_sequence` | integer | yes | 已成功整理的最大连续原始序号；只向前推进 |
| `last_batch_id` | string/null | yes | 最近成功批次；初始为空 |
| `updated_at` | datetime/null | yes | 初始为空；提交后为 UTC 时间 |
| `covered_record_ids` | list[string] | yes | 最近批次的完整记录 ID，便于恢复/审计 |

不变量：

- 前沿只能覆盖从旧前沿之后开始的连续、完整轮次；不能越过序号缺口。
- 即使某批次没有可保留的长期要点，经过有效整理也可推进前沿，但必须记录批次和覆盖记录。
- `curated_through_sequence` 只有在新的长期记忆/修正、全部来源关系和批次元数据共同写入成功后才生效。
- 人工直接提高该字段而没有可验证批次时拒绝加载；人工降低会导致重复整理，也必须通过显式维护命令确认，首版普通加载不接受。

## 5. MemoryCoverageOverview（记忆覆盖概览）

`MemoryCoverageOverview` 不是独立文件或第二份摘要，而是 `long_term.yaml` 根对象的一部分；记忆整理人格的一次结构化响应同时返回长期候选和 overview update，宿主校验后联合提交。

| Field | Type | Required | Rules |
|---|---|---:|---|
| `revision` | integer | yes | 必须等于根文档 `revision` |
| `text` | string | yes | 有界概览；空提取批次也可保持/更新该文本 |
| `coverage_spans` | list[CoverageSpan] | yes | 从 1 到整理前沿连续、无重叠、无缺口 |

`CoverageSpan` 包含 `start_sequence`、`end_sequence`、`batch_id`、有序 `record_ids` 与 `records_sha256`。所有记录必须存在且范围、顺序和摘要一致。每条已确认记录必须满足：序号不大于整理前沿且被恰好一个 span 覆盖，或序号大于整理前沿且仍处于直接注入窗口；否则提示组装 fail closed。

## 6. LongTermMemoryItem（长期记忆项）

| Field | Type | Required | Rules |
|---|---|---:|---|
| `memory_id` | string | yes | `mem-<uuid>`；稳定且唯一 |
| `kind` | enum | yes | `fact`、`preference`、`constraint`、`event`、`task` 或配置允许的受控值 |
| `text` | string | yes | 非空、长度受限、不得含凭据；作为不可信数据注入 |
| `status` | enum | yes | `active`、`superseded`、`retracted` |
| `source_refs` | list[SourceReference] | yes | 至少一项；与正文同一对象提交 |
| `created_by` | enum | yes | `memory_curator` 或 `manual` |
| `created_at` | datetime | yes | UTC；人工项也必须提供 |
| `updated_at` | datetime | yes | 不早于 `created_at` |
| `supersedes` | list[string] | yes | 指向现存较旧记忆；不得成环 |
| `tags` | list[string] | yes | 规范化、去重、数量/长度受限 |

不变量：

- 自动项和人工新增项都至少引用一条现存原始记录；无来源项不是成功记忆。
- 新信息明确修正旧信息时，新项为 `active`，旧项转为 `superseded` 或 `retracted`；旧项及其来源保留。
- 自动整理不得静默覆盖 `created_by=manual` 的人工决定，只能提出新候选或明确冲突。
- 稳定事实选择只使用 `active` 项；历史状态仍可查询和追溯。
- YAML 注释由 round-trip 读写尽量保留；程序新增解释性注释使用简体中文日期/版本痕迹。

## 7. SourceReference（来源引用）

| Field | Type | Required | Rules |
|---|---|---:|---|
| `record_id` | string | yes | 必须能在原始记录派生索引中读取 |
| `relation` | enum | yes | `supports`、`corrects`、`supersedes`、`manual_basis` |
| `record_sha256` | string | yes | 与原始记录内容摘要一致，发现外部篡改 |

运行时从所有 `source_refs` 建立反向派生索引：

```text
memory_id -> ordered SourceReference[]
record_id -> referencing memory_id[]
```

查询结果按对应 RawRecord 的 `global_sequence` 升序排列，稳定 ID 仅用于并列裁决。

## 8. PersonaProfile（人格配置）

| Field | Type | Rules |
|---|---|---|
| `persona_id` | string | 配置内唯一 |
| `role` | enum | `chat`、`memory_curator`、`state`、`assistant` |
| `prompt_path` | relative path | 必须位于配置根且指向非空 Markdown |
| `model_profile_id` | string | 指向已校验 Provider profile |
| `allowed_template_variables` | list[string] | 与实际模板标识符完全相等 |
| `allowed_tool_ids` | list[string] | 仍需经过全局工具启用/权限交集 |
| `prompt_sha256` | string | 进入 ConfigSnapshot；不记录正文 |

人格提示只描述职责和行为，不保存记忆、凭据、窗口阈值或供应商秘密。聊天人格不能替代记忆整理人格执行自动长期记忆写入。

## 9. AgentStateDefinition / StateResolution

`AgentStateDefinition` 来自 `states.toml`：

| Field | Type | Rules |
|---|---|---|
| `state_id` | string | 唯一；首版至少有 `default` |
| `ordered_persona_ids` | list[string] | 有序、非空、引用有效、不得重复 |
| `enabled` | boolean | 首版只启用默认状态 |

`StateResolution` 是每轮不可变结果：`state_id`、`ordered_persona_ids`、`resolver_id`、`resolver_version`、`reason_code`。首版 `StaticStateResolver` 只读配置，不因用户、记忆或模型文本改变结果。未来解析器也只能返回配置允许的状态/人格 ID，不能直接注入提示正文。

## 10. ProviderProfile 与模型对象

### ProviderProfile

包含 `provider_id`、`adapter_type`、`base_url`、`api_key_env`、模型 profile、能力声明、超时、重试、并发和备用顺序。只有环境变量名可持久化；缺失实际值时模型调用 fail closed。

### CompletionRequest

不可变对象，包含 `flow_id`、`turn_id`、有序 Message/PromptSegment、模型 profile 引用、启用工具定义、结构化输出要求和 deadline。不得包含供应商 SDK 类型。

### CompletionResult / CompletionEvent

规范化文本、工具调用、结束原因、用量和非敏感供应商元数据。`reasoning_content` 仅允许作为 DeepSeek 适配器内部的短期协议状态，不进入领域记录、提示追踪或普通日志。

## 11. PromptContext

| Field | Type | Rules |
|---|---|---|
| `flow_id` / `turn_id` | string | 标识当前工作流，防止工具结果跨流扩散 |
| `config_revision` | string | 本轮固定 |
| `state_resolution` | object | 包含有序人格集合 |
| `segments` | list[PromptSegment] | 固定优先级和顺序 |
| `budget` | object | 配置给出的总额和各段上限 |
| `source_manifest` | list[SourcePointer] | 只保存 ID、类型和哈希，不复制正文到追踪文件 |

`PromptSegment.trust` 只能是：

- `trusted_instruction`：已校验的安全策略、基础/状态/整理人格提示。
- `user_instruction`：当前用户输入。
- `untrusted_data`：长期记忆、历史原文、整理输出、工具返回和未来外部资料。

固定组装顺序为可信基础人格、可信状态人格、长期记忆摘要/选中项、近期原始记录、工具定义、当前输入。来源工具返回只追加到发起调用的当前 `flow_id`。

## 12. CurationBatch / CurationProposal

### CurationBatch

包含 `batch_id`、旧/新前沿候选、最旧连续完整轮次的 `record_ids`、配置修订和内容哈希。只有记录即将移出配置定义的直接注入窗口时才创建；阈值前不得调用整理模型。

### CurationProposal

记忆整理模型的结构化输出，只允许：新增候选、状态修正建议和来源引用。禁止出现人格、状态、工具授权、配置、安全策略或文件路径字段。

本地校验必须证明：

- JSON 完整且符合 Schema，数量/长度在配置范围内。
- 每项至少一个来源，来源属于当前批次或配置允许引用的既有记录，且真实存在。
- 无凭据或可恢复秘密。
- 关系和 `supersedes` 无环、无悬空引用。
- 对人工项没有静默覆盖。

## 13. 工具对象

### ToolDefinition

`tool_id/name/description/input_schema/output_schema/safety`。工具名限制为供应商共同支持的保守字符集；Schema 默认 `additionalProperties=false`。

### ToolExecutionContext

由宿主创建，包含 `flow_id`、`turn_id`、`persona_id`、`state_id`、`config_revision`。这些字段不能来自模型 arguments，因此模型不能伪造调用身份或扩大权限。

### ToolCall / ToolResult

`ToolCall` 包含 `call_id`、工具名和经解析但尚未信任的参数。`ToolResult` 以固定 outcome（`success`、`invalid_arguments`、`not_found`、`denied`、`timeout`、`execution_failure`）和结构化错误返回；任何非 success 不能依赖自然语言解析。

### MemorySourceQuery

输入仅含 `memory_id` 和可选不透明 `cursor`；页大小来自可信配置。输出包含记忆/索引修订、来源总数、有序来源、下一游标。不得返回磁盘路径或允许任意全文/路径查询。实现只获得只读仓库端口。

## 14. Turn 与 Flow 状态转换

### 单轮转换

```text
received
  -> user_persisted
  -> curation_check
      -> curation_committed      (仅窗口边界需要)
      -> no_curation_needed
  -> prompt_ready
  -> generating
  -> tool_subloop?               (配置限制内，首版仅只读工具)
  -> assistant_persisted
  -> completed
```

失败语义：

- 用户持久化前失败：没有确认轮次，可安全重新提交。
- 用户持久化后 Provider 失败：保留只有用户记录的可重试轮次，不伪造 Assistant 输出。
- 整理失败：`curated_through_sequence` 不变，本轮停止在生成前，原记录继续属于直接注入范围。
- 流式中断：丢弃内存中的未完成 Assistant 缓冲，不保存/展示半条记录。
- Assistant 持久化成功后进程终止：重启时已是完整记录；幂等 `turn_id` 防止重复生成。

### 整理转换

```text
not_eligible -> eligible -> proposed -> locally_validated -> atomically_committed
                                  \-> rejected/retryable
```

只有 `atomically_committed` 能推进直接注入修剪前沿。

### 工具调用转换

```text
proposed -> parsed -> schema_validated -> authorized -> executed -> returned
             \-> invalid_arguments     \-> denied       \-> failed/timeout
```

任何分支都产生稳定审计元数据；审计不复制来源正文或工具参数中的敏感文本。

## 15. 扩展声明

| Extension | First implementation | Default | Future replacement rule |
|---|---|---|---|
| ModelProvider | `DeepSeekProvider` | enabled when credential exists | 新 Provider 实现同一契约和测试套件 |
| StateResolver | `StaticStateResolver` | `default` | 状态机只能返回配置允许 ID |
| ToolRegistry | `memory_source_query` | only built-in enabled | 新工具默认禁用并声明安全能力 |
| LoopPolicy | `DisabledLoopPolicy` | STOP | Runner 复用 SingleTurnController，必须有界 |
| Storage | segmented files/YAML | local filesystem | 迁移实现须保持端口和不变量 |

## 16. 业务规则测试映射

| Rules | Automated evidence |
|---|---|
| BR-001—BR-003 | 跨 10 次重启的 100 轮集成测试；输入/输出写入顺序断言；每个原子写点的中断恢复 |
| BR-004、BR-007、BR-014、BR-015 | 永久原始段哈希/数量不变属性测试；明文与权限警告测试；超预算选择仍可追溯 |
| BR-005—BR-006 | PromptContext 强制段/顺序契约；缺段 fail closed；修正项激活及旧项降级测试 |
| BR-008 | 替换人格只影响后续 ConfigSnapshot，历史段和长期记忆哈希保持不变 |
| BR-009—BR-010 | 未启用工具/循环调用数为 0；测试替身接入后仍经过相同记忆、凭据和审计门禁 |
| BR-011—BR-013 | 窗口阈值前整理调用为 0；边界批量调用；失败不推进前沿；成功后同文档提交并修剪 |
| BR-016 | 无来源/悬空来源/跨文件冲突全部拒绝；替换前后只能是完整旧状态或完整新状态 |
| BR-017—BR-018 | 多人格同参查询契约；稳定排序/错误；调用前后文件哈希不变；原文只出现于对应 flow |

CR-001—CR-006 另由环境变量注入、凭据模式/熵检测、日志过滤、文件权限检查、工具结果脱敏和仓库秘密扫描共同验证。
