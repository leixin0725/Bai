# Data Model: 提示词追踪调试工具

**Date**: 2026-07-20
**Scope**: 领域值对象、调用状态、上下文估算与轮次事务；不是持久追踪格式。

## 设计约束

- 最终请求正文与来源只存在于待批准调用的进程内对象中，不写入日志、记忆或事务日志。
- 所有物理 provider 调用使用同一 `ModelCallGateway`，每次重试都是新的 attempt 和批准项。
- provider 认证由发送适配层单独注入，不属于最终提示载荷。
- 参与最终请求的内容必须全部可归因；来源未知只能用于显示构建错误，不能获准发送。
- 拒绝任一调用即拒绝整个轮次；本地状态最终与轮前检查点一致。
- 普通 provider/网络失败不是拒绝，必须发布且仅发布一条可恢复 USER pending。
- 当前工具只读；未来写工具没有可恢复事务或补偿契约时不得发生副作用。

## 1. ConfigAsset

表示本次构建实际加载的一份可维护配置或提示词资产。

| Field | Type | Rules |
|---|---|---|
| `asset_id` | string | 在一次 `ConfigSnapshot` 内稳定且唯一 |
| `kind` | enum | `persona`、`prompt_template`、`state_prompt`、`tool_config`、`provider_config`、`agent_config` |
| `project_relative_path` | string | 规范化相对路径，不允许绝对路径或 `..` 逃逸 |
| `content` | string | 本次快照实际使用的原文 |
| `content_sha256` | hex string | 对 UTF-8 原文计算 |
| `revision` | string | 配置快照 revision；同一快照内一致 |

**Relationship**: 一个 `ConfigAsset` 可被多个 `SourceRef` 引用；后续文件被编辑不会改变本次快照身份。

## 2. SourceRef

对一个实际构建来源的不可变引用。

| Field | Type | Rules |
|---|---|---|
| `source_kind` | enum | `config_file`、`data_file`、`runtime`、`generated` |
| `source_id` | string | 在调用内稳定；不得由正文反向猜测 |
| `project_relative_path` | string/null | 仅文件来源填写；运行时来源必须为 null |
| `content_sha256` | string/null | 文件/数据快照可用时填写 |
| `revision` | string/null | 配置 revision 或数据 revision/hash |
| `entity_ids` | ordered tuple[string] | 长期记忆、raw record、tool call 等稳定标识 |
| `producer` | string | 如 `config_loader`、`memory_selector`、`user_input`、`tool:memory_source`、`deepseek_adapter` |

**Validation**:

- `config_file`/`data_file` 必须有路径与内容身份。
- `runtime` 必须无路径并有 producer 与至少一个关联标识。
- 聚合 part 的 refs 保持真实选择顺序，不去重成单一路径。

## 3. RequestPart

表示最终请求中的一段内容或构建中被明确排除的一段候选。

| Field | Type | Rules |
|---|---|---|
| `part_id` | string | 调用内唯一、确定性生成 |
| `order` | integer | 从 0 开始，稳定排序 |
| `participation` | enum | `included`、`excluded`、`empty`、`unknown_source` |
| `trust` | enum | 延续现有 trust 分类 |
| `payload_pointer` | JSON Pointer/null | included 时指向最终载荷字段，如 `/messages/2/content` |
| `text_span` | tuple[int,int]/null | 对字符串字段的 `[start,end)` Unicode code-point 区间 |
| `content` | string | TUI 展示正文；批准后、网络发送前随 presenter 释放 |
| `sources` | ordered tuple[SourceRef] | included 时至少一项且不得 unknown |
| `exclusion_reason` | string/null | 非 included 时给出明确原因 |

**Validation**:

- included part 的 pointer 必须可解析，span 不越界，载荷切片必须等于 `content`。
- included part 必须有来源；否则 participation 必须为 `unknown_source` 且调用不可发送。
- excluded/empty part 不进入最终请求和 token 合计，但可在构建诊断区域显示。
- provider chat 协议包装使用 synthetic part `generated:provider_protocol_overhead` 表示，不伪造文件路径。

## 4. ModelCallDraft

provider-neutral 的模型调用及完整来源上下文。

| Field | Type | Rules |
|---|---|---|
| `call_id` | UUID/string | 整个逻辑调用稳定 |
| `turn_id` / `flow_id` | string | 关联当前轮与流程 |
| `call_sequence` | positive int | 轮内实际顺序，严格递增 |
| `purpose` | enum/string | `chat`、`memory_curation`、`tool_continuation`、未来辅助用途 |
| `persona_id` / `state_id` | string/null | 本次实际使用值 |
| `config_revision` | string | 构建快照身份 |
| `model_profile_id` | string | 指向统一配置 |
| `request` | CompletionRequest | 现有 provider-neutral 请求 |
| `parts` | tuple[RequestPart] | 含 included 和诊断候选 |

**Relationship**: 一个 draft 可因重试产生多个 `PreparedProviderRequest` attempt，但每个 attempt 独立批准。

`CompletionRequest.messages` 的 assistant 项可携带 provider-neutral `tool_calls`（call id、函数名、参数）；tool continuation 必须把该 assistant 项放在对应 tool result 之前。DeepSeek adapter 只在物化时转换为 OpenAI tool_calls 结构，并为生成的 assistant content/tool_calls 添加 `[generated]` 来源 part。

## 5. PreparedProviderRequest / MaterializedSendPayload

`PreparedProviderRequest` 是 `prepare()` 产生的 provider 适配结果和来源上下文；`MaterializedSendPayload` 只能由唯一一次 `materialize_sdk_kwargs()` 产生，是 TUI 展示、批准摘要和 sender 发送共同使用的深度不可变 SDK 参数。

| Entity.Field | Type | Rules |
|---|---|---|
| `PreparedProviderRequest.call_id` | string | 来自 draft |
| `PreparedProviderRequest.attempt` | positive int | 每个物理尝试递增 |
| `PreparedProviderRequest.provider_id` / `model` | string | 本次实际目标 |
| `PreparedProviderRequest.provider_request` | frozen JSON value | provider-specific 无认证逻辑请求；只能由 materializer 读取 |
| `PreparedProviderRequest.max_output_tokens` | positive int | 本次真实预留 |
| `PreparedProviderRequest.parts` | tuple[RequestPart] | 在物化后校验 pointer/span；approve 后随 presenter 释放 |
| `MaterializedSendPayload.call_id` / `attempt` | string / int | 与 prepared 完全相同 |
| `MaterializedSendPayload.provider_id` / `model` | string | 与 prepared 完全相同 |
| `MaterializedSendPayload.sdk_kwargs` | frozen JSON value | SDK 实际接收的全部模型可见字段；不含 API Key/header/client/来源 |
| `MaterializedSendPayload.canonical_payload_sha256` | hex string | 对 `sdk_kwargs` 规范 JSON 的 SHA-256 |

**Validation**:

- `materialize_sdk_kwargs()` 每个 attempt 恰好调用一次；`prepare()`、TUI、摘要器和 `send_once()` 均不得另行生成 SDK kwargs。
- `sdk_kwargs` 的 mapping/sequence 在内存中深度冻结；sender 只允许在实际 SDK 调用参数边界通过 `thaw_json()` 恢复为等值 dict/list/scalar。恢复前后 canonical JSON 与 digest 必须一致，不得把内部 `mappingproxy` 交给 JSON encoder。
- materializer 递归冻结所有 dict/list，拒绝非 JSON 类型。
- 规范 JSON 使用 UTF-8、稳定 key 排序和无非语义空白；数组顺序原样保留。
- provenance validator 以 `sdk_kwargs` 回读所有 included pointer/span，且 credential guard 通过后才能展示。
- 发送前重算 materialized digest 并与批准令牌比较；TUI 与 sender 必须引用同一 `MaterializedSendPayload`。
- approve 后 presenter 释放 prepared、part、SourceRef 和 renderable；sender 只保留 materialized payload，并在 `send_once` 成功或失败后的 `finally` 释放。

## 6. ApprovalDecision / ApprovalToken

| Field | Type | Rules |
|---|---|---|
| `decision` | enum | `approve` 或 `reject`；无默认值 |
| `call_id` | string | 必须等于 prepared request |
| `attempt` | int | 必须等于当前 attempt |
| `payload_sha256` | string | 批准时绑定；reject 可保留用于审计内存但不得持久化 |
| `decided_at` | monotonic timestamp | 仅用于当前进程排序，不持久化 |

批准不创建修改后的 request；reject 不创建取消记录，而是触发整轮回滚。

## 7. ContextUsageEstimate

| Field | Type | Rules |
|---|---|---|
| `status` | enum | `estimated` 或 `unavailable` |
| `estimated_input_tokens` | int/null | estimated 时非负 |
| `part_tokens` | ordered map[part_id,int] | 只含 included part |
| `protocol_overhead_tokens` | int/null | provider 包装开销 |
| `max_output_tokens` | int | 来自 prepared request |
| `projected_peak_tokens` | int/null | input + max output |
| `context_capacity` | int/null | 来自当前 profile 能力元数据 |
| `projected_percent` | decimal/null | capacity 已知时计算 |
| `projected_remaining_tokens` | int/null | capacity - peak，可为负 |
| `risk` | enum/null | `normal`、`high`、`critical`、`exceeded` |
| `method` | string/null | estimator id/version |
| `confidence` | enum/null | `conservative` 等文档化值 |
| `reason` | string/null | unavailable 时必填 |

**Invariant**: estimated 时 `estimated_input_tokens == sum(part_tokens.values()) + protocol_overhead_tokens`；`projected_peak_tokens == estimated_input_tokens + max_output_tokens`。

### ActualUsageSummary

| Field | Type | Rules |
|---|---|---|
| `status` | enum | `actual` 或 `unavailable` |
| `actual_input_tokens` / `actual_output_tokens` / `actual_total_tokens` | int/null | actual 时非负且守恒 |
| `actual_percent` | decimal/null | 容量已知时计算 |
| `estimated_input_tokens` / `input_estimation_error` | int/null | 只复制发送前数值，不保留 estimate 对象 |
| `reason` | string/null | unavailable 时必填且脱敏 |

`ActualUsageSummary` 不得持有或引用 prompt、`PreparedProviderRequest`、`MaterializedSendPayload`、`RequestPart`、`SourceRef`、TUI widget 或 renderable；只由普通聊天输出显示，不重新打开 prompt TUI。

## 8. TurnWorkingSet

当前轮在最终确认前的进程内工作视图。

| Field | Type | Rules |
|---|---|---|
| `checkpoint` | PreTurnCheckpoint | 已提交 raw 尾部身份、长期记忆 revision/hash、Agent state、配置 revision |
| `provisional_user_record` | RawRecord | 在 provider 调用前写入事务日志，但不进入已确认归档 |
| `curation_proposal` | optional ProposedCuration | 目标长期记忆文档及来源索引，未提交 |
| `tool_results` | ordered tuple | 首版只允许只读工具结果 |
| `state_candidate` | optional | 整轮确认前不发布 |
| `assistant_record` | optional RawRecord | 成功完成后写入 READY_TO_COMMIT journal |

提示构建读取 checkpoint 的已提交数据加 working set 的虚拟视图；拒绝时直接丢弃工作集。

## 9. TurnTransactionJournal

唯一允许持久化的临时轮次状态；私有文件权限、临时文件 + fsync + atomic replace。

| Field | PREPARED | READY_PENDING | READY_TO_COMMIT |
|---|---|---|---|
| `schema_version` | required | required | required |
| `state` | `PREPARED` | `READY_PENDING` | `READY_TO_COMMIT` |
| `transaction_id` / `turn_id` | required | required | required |
| `checkpoint` | required | required | required |
| `provisional_user_record` | required | required | required |
| `pending_failure_code` | absent | required, sanitized | absent |
| `assistant_record` | absent | absent | required |
| `target_long_term_document` | absent | absent | optional |
| `target_long_term_sha256` | absent | absent | optional |
| prompt/provenance/credential/tool body | forbidden | forbidden | forbidden |

### State transitions

```text
ABSENT
  └─ begin_turn + durable journal ─> PREPARED
       ├─ explicit reject / restart before a READY state ─> discard journal ─> ABSENT
       ├─ provider/network failure + durable replace ─> READY_PENDING
       │      └─ idempotent publish one USER pending ─> delete journal ─> ABSENT
       └─ complete local results + durable replace ─> READY_TO_COMMIT
              └─ idempotent publish complete raw turn + long-term ─> delete journal ─> ABSENT
```

- 启动时在取得 `WriterLease` 后、读取 pending/config/provider 前恢复。
- `PREPARED` 总是丢弃，因为它既未形成普通失败决定也未形成确认完整轮次，且不得暴露为 pending/cancelled。
- `READY_PENDING` 总是前滚为且仅为一条 USER pending；事务恢复后显式 `--resume-pending` 可重发，默认启动与 `--discard-pending` 可放弃该合法尾部 pending；不得包含 assistant、长期记忆或拒绝标记。
- `READY_TO_COMMIT` 总是前滚为完整轮次；发布 API 以 record id、turn id、checkpoint hash 幂等。
- 基线被人工修改或 hash 冲突时 fail closed，阻止新轮次并给出恢复指引，不覆盖外部更改。

## 10. Model call lifecycle

```text
DRAFTED -> PREPARED -> MATERIALIZED -> PROVENANCE_VALIDATED -> CREDENTIAL_CHECKED
        -> DISPLAY_READY -> APPROVED -> UI_CLEARED -> SENDER_OWNS_PAYLOAD
        -> SEND_ONCE -> COMPLETED/PROVIDER_FAILED -> SENDER_RELEASED
                                \-> REJECTED -> TURN_ROLLBACK
        \-> VALIDATION_FAILED / DISPLAY_FAILED -> BLOCKED
```

- `materialize_sdk_kwargs()` 在 `MATERIALIZED` 只调用一次；批准绑定 call、attempt 和 materialized digest。
- `UI_CLEARED` 发生在网络发送前；presenter/TUI/trace 对 prepared、正文与来源的引用必须释放，sender 只接管同一个 `MaterializedSendPayload`。
- `send_once` 无论成功或失败都在 `finally` 进入 `SENDER_RELEASED`；成功只返回 response 与无原文 usage 数值，普通失败驱动事务进入 `READY_PENDING`，明确拒绝不进入 sender。
- transport 可重试错误使下一个 attempt 从 `PREPARED` 重新开始，不恢复上一 attempt 的 UI。
- 调试关闭时仍经过 DRAFTED/PREPARED/MATERIALIZED/校验/send_once，但不进入 TUI/批准分支；形成的 `sdk_kwargs` 必须与调试开启路径深度相同。

## 11. Cross-entity invariants

1. 调试模式每个 `send_once` 前恰有一个匹配 call/attempt/materialized digest 的 approve；批准操作不得改变请求。
2. 每个 included part 都能从最终载荷切片回读相同正文并至少有一个合法来源。
3. 每个 attempt 只执行一次 `materialize_sdk_kwargs()`；TUI 展示与 sender 读取同一个 `MaterializedSendPayload`，不得生成第二份发送载荷。
4. 任何 credential guard 命中都在 TUI 显示和网络发送前终止，错误不含原值。
5. reject 使当前 transaction 从 PREPARED 收敛到 ABSENT，confirmed raw/long-term/state 与 checkpoint 相同。
6. 普通 provider/网络失败使当前 transaction 从 PREPARED 收敛到 READY_PENDING，再只发布一条 USER pending；reject 不允许走该路径。
7. READY_PENDING 与 READY_TO_COMMIT 不允许回滚；恢复分别只前滚单条 pending 或完整轮次。
8. `ActualUsageSummary` 和普通输出不得持有 prompt、materialized payload、part 或 SourceRef；事务日志和正常日志中不得出现 `sdk_kwargs`、part content、SourceRef 集合或认证信息。
9. 当前工具均为只读；写工具没有经验证的可恢复事务或补偿能力时副作用调用次数为 0。
10. 可放弃 pending 是派生状态而非新 journal 状态：它必须是 raw 最后一条 USER、该 turn 无 ASSISTANT、此前记录均为完整连续轮次，且其 sequence/record id 未进入长期记忆 source refs、coverage spans、covered ids 或 curation frontier。
11. 丢弃只原子替换最后一个 segment 并移除其最后一行；若该行是 segment 唯一记录则保留空尾 segment，后续 append 复用该编号。全局 sequence 不重排，下一条新记录安全复用被放弃的末尾 sequence。
