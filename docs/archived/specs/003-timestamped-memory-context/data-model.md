# Data Model: 面向模型历史上下文的智能时间段标注

**Date**: 2026-07-20

**Scope**: 提示构建期的时间值对象、来源投影、标记片段和工具事件；除独立 TOML 配置外不新增持久化格式。

## 设计约束

- `RawRecord.created_at` 及其 UTC 瞬时是直接聊天事件的事实来源，展示时区转换不得写回。
- 长期记忆与 coverage overview 的时间由已验证来源动态派生，不保存第二份范围。
- 时间标记只存在于当次提示构建结果，不成为 raw、长期记忆或工具结果正文。
- 当前输入使用本轮 provisional `RawRecord` 的既定 `created_at`，不在 retry、恢复、审批或发送时重采样。
- 所有逻辑历史区块分别以空分段状态运行，但共享同一不可变策略实例。
- 通用时间模块不读取存储、不理解消息角色、不填模板、不调用 provider。
- 任意无效时间、来源或配置在模型发送前失败；formatter 不猜测、不排序、不删项。

## 1. TemporalSegmentationPolicy

由 `config/history_timestamps.toml` 的同一份已验证 `ConfigSnapshot` 构造的不可变策略。

| Field | Type | Rules |
|---|---|---|
| `display_timezone` | `ZoneInfo` | 必须由配置中的 IANA 名称解析；不接受本机默认时区 |
| `display_timezone_name` | string | 与 `ZoneInfo.key` 一致，用于诊断，不直接进入 marker 文本 |
| `long_gap` | positive `timedelta` | 1—1440 分钟，默认 30 分钟 |
| `continuous_refresh` | positive `timedelta` | 1—10080 分钟，且 `>= long_gap`，默认 120 分钟 |
| `split_on_local_date_change` | bool | 严格 bool，默认 true |
| `config_source` | `SourceRef` | 指向真实 `config:history_timestamps` 资产、hash 和 snapshot revision |

**Invariant**: 一次 prompt/curation/tool continuation 构建只持有一个该对象；配置重载必须先完成整个新对象和消费者组装，再替换旧引用。

## 2. TemporalTimeKind / TemporalSpan

`TemporalTimeKind` 明确可见标签的语义，避免仅凭 `start == end` 猜测。

| Value | Meaning | Visible template |
|---|---|---|
| `EVENT` | 直接事件的可信点时间 | `[时间：…]` |
| `SOURCE_RANGE` | 派生内容全部可验证来源的最早—最晚事件时间 | `[时间范围：… 至 …]` |
| `RECORDED` | 未来另行定义具体 schema/version 的 legacy 适配器可使用的可信记录时间；当前无持久化入口 | `[记录时间：…]` |

`TemporalSpan`：

| Field | Type | Rules |
|---|---|---|
| `start` | aware `datetime` | 必须带可用 UTC offset |
| `end` | aware `datetime` | 必须带可用 UTC offset，UTC 瞬时 `>= start` |
| `kind` | `TemporalTimeKind` | `EVENT`/`RECORDED` 要求 `start == end`；`SOURCE_RANGE` 即使相等也保持 range kind |

**Normalization**: 比较、间隔、倒序和范围校验用 UTC 瞬时；原值不修改。只有渲染 marker 时转换为 `policy.display_timezone`。

## 3. TemporalLogEntry

各消费者提交给通用模块的最小、不可变日志单元。

| Field | Type | Rules |
|---|---|---|
| `entry_id` | string | 区块内唯一、稳定；不得由正文 hash 临时猜测 |
| `body` | string | 已由消费者按自身合同序列化；formatter 不改字符 |
| `span` | `TemporalSpan` | 有效且带时区 |
| `sources` | ordered tuple[`SourceRef`] | 至少一项，真实指向该 body/实体；顺序稳定 |
| `metadata` | immutable map | 可选的角色、record id、tool call id 等 transport 提示；不参与分段 |

**Validation**:

- 输入顺序是权威选择顺序；`entry_id` 或时间倒序不会触发排序。
- 空 body 只在协议明确允许时接受，例如 assistant/tool_calls 的 marker-only content；普通历史适配器应在上游拒绝空正文。
- formatter 不读取或修改 metadata；消费者在输出时必须逐项对应回原角色/字段。

## 4. TemporalMarker

表示紧邻某一日志项之前的单个生成元数据行。

| Field | Type | Rules |
|---|---|---|
| `before_entry_id` | string | 等于承载项 `entry_id` |
| `text` | string | 严格使用 kind 对应固定中文模板，结尾不含换行 |
| `reasons` | non-empty frozenset | `FIRST`、`LONG_GAP`、`LOCAL_DATE_CHANGE`、`REFRESH`、`TIME_REVERSAL`；多命中合并 |
| `span` | `TemporalSpan` | 承载当前项的时间，不使用上一项或 prompt 构建时间 |
| `sources` | ordered tuple[`SourceRef`] | 策略配置来源 + 触发项来源；不得伪造路径/hash |
| `trust` | enum | 固定 `UNTRUSTED_DATA` |

**Marker reason precedence**: 原因集合只用于测试/调试，固定可见文本不附带原因；`FIRST` 只在区块第一项，其他原因可以同时命中但一项最多一个 marker。

## 5. AnnotatedFragment / AnnotatedHistory

`AnnotatedHistory` 是 formatter 的完整瞬态结果。

| Field | Type | Rules |
|---|---|---|
| `text` | string | 按输入顺序连接 `marker? + newline + body`，项间使用消费者指定的固定 separator |
| `entries` | ordered tuple | 与输入一一对应，保留原 entry/span/source |
| `fragments` | ordered tuple[`AnnotatedFragment`] | marker/body 的非重叠精确片段 |
| `markers` | ordered tuple[`TemporalMarker`] | 顺序等于其承载项顺序，无尾 marker |

`AnnotatedFragment`：

| Field | Type | Rules |
|---|---|---|
| `fragment_id` | string | 区块/entry/kind 确定性生成 |
| `kind` | enum | `MARKER`、`BODY`、`SEPARATOR`；仅 current input 投影可把 marker 转为 `TRUSTED_TIME_METADATA` |
| `entry_id` | string | 关联承载项 |
| `start` / `end` | non-negative int | 对 `AnnotatedHistory.text` 的 Unicode code-point `[start,end)`；互不重叠且可回读 |
| `content` | string | 必须等于 `text[start:end]` |
| `sources` | ordered tuple[`SourceRef`] | marker 为 policy + entry；body 为 entry 原来源 |
| `trust` | enum | marker 为 `UNTRUSTED_DATA`；body 沿用消费者提供的日志信任级别 |

**Coverage invariant**: separator/newline 可以归入相邻 fragment 或显式 generated separator part，但所有最终模型可见字符必须由唯一、非重叠 `RequestPart` 或 provider overhead 归因，不能以 whole-content part 再次覆盖 marker/body。

## 6. VisibleUntrustedBlock

共享 `UntrustedBoundaryRenderer` 把一个已经定位来源的逻辑数据块渲染为：

```text
[UNTRUSTED <name>#<8 hex>]
<content>
[/UNTRUSTED <same name>#<same id>]
```

- `id` 是由 block name 与完整 inner content 确定性派生的 8 位十六进制短标识；同形正文不能跨 block 复用边界身份。
- opening/closing 由同一个真实 `prompt:untrusted_memory_boundary` `ConfigAsset` 与 generated renderer source 归因，inner fragments 保持自己的 trust 与来源。
- 一个历史/记忆/整理逻辑块恰有一对边界；工具协议按一条 assistant/tool 事件消息作为逻辑块，不逐字段或逐记录重复包装。
- provider 只接收已有 role/content/tool_calls 等支持字段；边界作为 content 字符进入字符预算、token 估算和 provenance span。
- `compose_system_instruction()` 只负责把 persona、generated separator 与 boundary instruction 串接为同一 system content；三段使用不重叠 fragment，分别引用 persona asset、generated source 和 boundary asset。

## 7. MemoryTemporalProjection

`src/bai_agent/memory/temporal.py` 的瞬态适配结果，不持久化。

| Projection | Body owner | Time derivation | Required sources |
|---|---|---|---|
| current input | assembler 序列化 provisional USER body | 本轮 provisional `RawRecord.created_at` 的 EVENT 点时间 | runtime provisional record id/turn id/hash + timestamp config |
| raw recent/batch | assembler/curation 序列化 `RawRecord` | `created_at` 的 EVENT 点时间 | raw file + record id/hash |
| long-term item | assembler/curation 序列化现有字段 | 所有已验证 source refs 的 min/max，SOURCE_RANGE | long-term item + 每个 raw source ref |
| coverage overview | assembler/curation 序列化现有 overview | 所有 coverage records 的 min/max，SOURCE_RANGE | long-term document/coverage + 每个 raw ref |
| explicit legacy item | 未来独立功能明确实现的 legacy adapter；当前版本不实例化 | 可信 `created_at` 的 RECORDED 点时间 | legacy asset/version + item id |

内部工作集：

| Field | Type | Rules |
|---|---|---|
| `raw_by_id` | immutable map[string, RawRecord] | 每次 snapshot 构建一次，禁止适配单项时重读 archive |
| `raw_revision` | string/hash | 与来源验证时读取的同一快照一致 |
| `projected_entries` | ordered tuple[`TemporalLogEntry`] | 按 selector/coverage/record 原有顺序 |

**Failure behavior**: 复用 `RAW_SEGMENT_INVALID`、`SOURCE_RECORD_MISSING`、`SOURCE_HASH_MISMATCH`、`MEMORY_COVERAGE_INVALID`/`MEMORY_COVERAGE_GAP`；仅通用时间值本身非法时新增/使用 `TEMPORAL_ENTRY_INVALID`。已声明来源的任何错误不得转为 `RECORDED`。

### Current input trust split

当前输入由一个 EVENT entry 经过同一 annotator 产生 marker；投影层把该 marker 转为 `TRUSTED_TIME_METADATA`/`TRUSTED_METADATA` 片段并置于边界外，明确表达“可信但不是指令”，renderer 只包围用户正文。正文 fragment 保持 `USER_INSTRUCTION`，但模型可见结构明确位于 `current_input` 不可信块中。重试和 pending resume 必须传入同一个 provisional/persisted USER record。raw archive 仅保存用户原正文和原 `created_at`，下一轮历史重新生成 marker，不能读取或复制上轮的展示字符串；其他历史 marker 仍保持 `MARKER`/`UNTRUSTED_DATA` 并位于历史块内。

## 8. ToolHistoryEvent

当前轮进程内、排除持久化/SDK 序列化的原始工具历史事件。它保存未标注 body，使每次 continuation 都能从同一事实重建整个工具区块。

| Field | Type | Rules |
|---|---|---|
| `event_id` | string | assistant batch 用 origin model call id；result 用 tool call id + stable outcome id |
| `kind` | enum | `TOOL_CALL_BATCH` 或 `TOOL_RESULT` |
| `occurred_at` | aware `datetime` | call batch=`accepted_at`；result=`completed_at` |
| `original_body` | string | assistant 原 content 或 canonical ToolResult JSON；永不包含旧 marker |
| `role` | enum | 原 `assistant`/`tool` |
| `tool_calls` | immutable tuple | 仅 assistant batch；id/name/arguments 原样 |
| `tool_call_id` | string/null | 仅 result；必须匹配对应 call |
| `sources` | ordered tuple[`SourceRef`] | provider response 或 tool executor 的真实运行时来源 |

**Wire mapping**:

- `TOOL_CALL_BATCH` 可对应多个 tool calls，但只产生一条 assistant 消息和一个时间事件。
- marker 只前缀到该消息 content；不得进入 `tool_calls`。
- 每个 `TOOL_RESULT` 独立参与分段；marker 只前缀到该 tool message content，canonical JSON 保持逐字连续子串。
- assistant/tool 消息顺序和配对保持；禁止插入额外 marker message。
- 运行时时间字段必须从 DTO `model_dump()`、canonical result JSON 和 SDK payload 扩展字段中排除。

## 9. Existing persisted entities

本功能读取但不改变以下既有持久化合同：

### RawRecord

- `created_at` 继续是带时区、持久化的事件时间；现有写入顺序、record id、hash 和正文不变。
- 新记录继续由现有轮次事务发布；不追加 marker、显示时区或 segment id。

### LongTermMemoryItem / coverage overview

- schema v1 的非空 `source_refs`、item `created_at`、coverage ids/hash 和完整性校验不变。
- `created_at` 是记忆记录时间，不作为现行 v1 的事实发生时间。
- 来源时间范围只在构建提示时计算，不新增 YAML 字段。

### ConfigSnapshot / ConfigAsset

- snapshot 新增 `history_timestamps.toml` 的 settings 与 asset，但 revision 算法和不可变加载语义不变。
- 缺少该必需 manifest 是配置错误，不使用代码默认值继续请求。

### MemorySourceQueryTool result

- 输入、分页、权限、错误、字段和 canonical JSON 完全不变。
- 外层工具消息可能由通用 tool history bridge 前缀 marker；这不是工具返回 schema 的一部分。

## 10. State transitions

### Configuration lifecycle

```text
FILES_CHANGED
  -> LOAD_ALL_MANIFESTS
  -> STRICT_VALIDATE
     -> INVALID: fail before raw mutation/provider call; retain no partial objects
     -> VALID: build policy + annotator + all consumers
              -> atomic application reference swap at next turn boundary
```

### One logical history block

```text
EMPTY
  -> first entry: emit FIRST marker, set previous and last-marker entry
  -> next entry:
       compute gap/date/reversal/refresh from full instants
       -> no trigger: emit body only; update previous
       -> one or more triggers: emit one marker + body;
                               update previous and last-marker entry
  -> END: emit nothing else
```

### Tool continuation lifecycle

```text
accepted CompletionResult with tool_calls
  -> sample accepted_at once
  -> append raw TOOL_CALL_BATCH event
  -> execute each call
       -> form sendable ToolResult
       -> sample completed_at once
       -> append raw TOOL_RESULT event
  -> re-annotate full current-turn tool block
  -> map marker/body spans into existing assistant/tool messages
  -> provider materialize and send
```

## 11. Cross-entity invariants

1. 对同一 ordered entries + policy，`AnnotatedHistory.text`、marker positions/reasons 和 fragment spans 逐字确定。
2. 每个非空区块第一个 entry 恰有一个 marker；空区块、区块尾和无承载时刻没有 marker。
3. 输出 entry 数、顺序、body、角色与协议字段等于输入；只允许在正文前增加 marker 元数据行。
4. 一个 marker 的来源至少包含同一 snapshot 的时间策略资产和它所承载 entry 的真实来源。
5. 所有 boundary decision 使用未截断的 aware datetime；显示时区和分钟格式化不参与决策。
6. `SOURCE_RANGE` 的 start/end 来自全部已验证 raw 来源的 UTC min/max；相等端点仍显示范围模板。
7. `RECORDED` 只能由后续功能明确 schema/version 且定义为无来源字段的 legacy adapter 创建；当前版本没有持久化 adapter，现行 v1、未识别格式或损坏来源不能走该路径。
8. 所有预算和 estimator 输入使用最终含 marker 文本；formatter 不以预算为由删 marker 或 entry。
9. 一个最终字符串位置最多属于一个 included text span；DeepSeek adapter 不得再创建覆盖 marker/body 的整段 part。
10. 工具调用接受/结果完成时间各只采样一次；重试、审批和重渲染复用原时刻。
11. `memory_source_query` 实现和结果 schema 不因时间标注改变；其 body 在外层消息中仍可逐字定位。
12. 标注流程不写 raw/YAML，不修改原始 UTC 时间，并对 10,000 项保持 O(n) 时间和 O(n) 输出空间。
13. 当前输入的 marker 时间等于 provisional `RawRecord.created_at`，使用可信时间元数据 trust 并位于边界外；正文保持 `USER_INSTRUCTION` 且位于 `current_input` 不可信边界内，retry/resume 不重采样。历史 marker 不提升信任。
14. 每个不可信逻辑数据块在最终 payload 中恰有一对匹配的 visible boundary；wrapper、inner content 与 estimator 使用同一个最终字符串。
15. system content 中 persona 与 boundary instruction 的 span 不重叠，并分别绑定同一 `ConfigSnapshot` 中各自的真实 asset/hash/revision。
