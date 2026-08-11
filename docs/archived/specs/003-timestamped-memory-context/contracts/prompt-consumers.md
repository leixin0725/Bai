# Contract: 日志类提示消费者接入

**Purpose**: 固定全部现有消费者的适配、预算、来源和 provider 协议边界，避免某个提示构建路径遗漏或自行复制分段规则。

## Consumer inventory

每行是独立逻辑区块，必须单独调用统一 annotator；`non-log exclusions` 不得调用。

| Flow | Block | Entry/body representation | Time basis | Budget/protocol owner |
|---|---|---|---|---|
| chat | `memory_overview` | 现有 overview 文本 | 全部 coverage refs 的 SOURCE_RANGE | `PromptAssembler` overview chars |
| chat | `long_term_memories` | 现有长期记忆 item 文本/分隔 | 每项全部 source refs 的 SOURCE_RANGE | selector + assembler long-term budget |
| chat | `recent_records` | 现有 `role: content` | 每条 raw `created_at` EVENT | assembler recent chars |
| curation | `batch_records` | 每项 canonical JSON 行 | 每条 raw `created_at` EVENT | `CurationService` + gateway estimator |
| curation | `existing_memories` | 每项 canonical JSON 行 | 每项全部 source refs 的 SOURCE_RANGE | `CurationService` + gateway estimator |
| curation | `current_overview` | canonical JSON item/block | coverage refs 的 SOURCE_RANGE | `CurationService` + gateway estimator |
| tool continuation | current-turn tool history | 原 assistant content/tool JSON body | accepted EVENT / completed EVENT | controller + provider estimator |

**Non-log exclusions**: 当前用户输入、人格、状态/system rules、curation batch metadata、输出 schema/格式指令、provider tools schema 和 memory source query 工具返回合同本身。

## Shared adaptation rules

1. 调用方先取得同一 `ConfigSnapshot` 和一次已验证 raw snapshot，再适配 body/span/sources。
2. 上游选择顺序保持权威：recent/archive 顺序、长期记忆相关性顺序、curation item 顺序及工具协议顺序均不重排。
3. 每个 block 独立调用 annotator；不传递 `previous` 或 `last_marker` 到下一 block。
4. 渲染结果和 fragment spans 直接组合进 `PromptSegment`/`RequestPart`；不得把 text flatten 后再用正文搜索恢复来源。
5. marker/body/separator 的 included spans 唯一且不重叠，最终 payload 中每段可回读。
6. 同一请求所有 marker 来源引用同一策略 asset revision。

## Chat prompt contract

- `PromptAssembler` 不再只接收丢失来源时间的长期记忆字符串；它接收投影后的有序项或足够的 item/raw snapshot 以构造投影。
- overview、long-term、recent 分别生成 `AnnotatedHistory`，再按现有段落位置组合；每个非空 block 的首行是自身 marker。
- 当前 user input 保持独立消息/segment，不在其前加日志 marker。
- 长期记忆按相关性顺序逐候选纳入；每次计算加入候选后的最终 annotated block 字符成本，包括候选可能触发的 marker 和 separator。
- overview/recent 的限制也检查最终含 marker 文本；不得先检查裸文本再超额附加 marker。
- 若必须覆盖的 recent 最终超限，使用既有/明确 overflow 路径失败，不删 marker 或正文。

## Memory projection and curation contract

- curation template 保留现有变量名、指令、batch metadata 和输出 schema。
- `batch_records`、`existing_memories`、`current_overview` 各自独立标注。
- 日志项使用一行 canonical JSON body，marker 独占前一行。例如：

```text
[时间：2026-07-20 09:00 +08:00]
{"content":"第一条","record_id":"r-1","role":"user"}
```

- canonical JSON 的现有字段、排序/转义规则和 proposal parser 不变；marker 不放入 JSON 对象/数组，也不成为模型输出 schema 字段。
- 模板替换函数在插入变量时累计绝对 offset，并把 block-local fragment spans 平移到最终 prompt；禁止用 `str.find()` 反查重复 JSON/body。
- 长期记忆/overview 时间解析复用同一 `raw_by_id`；单次 curation 不按 item 重读 archive。

## Tool continuation protocol

### Event capture

- `TOOL_CALL_BATCH.occurred_at` 在模型成功 response 被 gateway 接受时采样；同一 assistant response 的多个 calls 共用该时刻。
- `TOOL_RESULT.occurred_at` 在 executor 完成校验、事务/安全处理并形成可发送 canonical result 后采样；每个结果独立。
- 时间只采样一次并排除 DTO/wire 序列化；debug approval、网络 retry 和重建不得改变旧事件时间。

### Message mapping

原始协议顺序：

```text
assistant(content?, tool_calls=[...])
tool(content=<canonical result>, tool_call_id=<matching id>)
```

标注后仍是相同两类消息：

```text
assistant(content="<marker>\n<original assistant body>" or "<marker>", tool_calls=[unchanged])
tool(content="<optional marker>\n<canonical result>", tool_call_id=<unchanged>)
```

- 不插入 system/user/metadata message。
- `tool_calls` 的 id、type、name、arguments 和次序逐字段不变。
- tool role、`tool_call_id` 和 canonical ToolResult JSON body 逐字不变；body 只允许成为 marker 后的连续子串。
- 当前轮全部未标注工具事件作为一个 block 每次整体重建；不能按 continuation round 分别调用 annotator，也不能以已标注字符串为新输入。
- 若 marker 导致工具 content 不再是整串 JSON，内部逻辑仍使用渲染前 `ToolResult`，不得重新解析模型可见 content。

### DeepSeek provenance

- marker/body 对同一 `/messages/{i}/content` 使用互不重叠 span。
- assistant body 来源使用原始 provider response/origin call identity；不得误用当前 continuation draft call id。
- tool body 来源使用 executor/tool call identity；tool_calls 在 `/messages/{i}/tool_calls` 有独立 part。
- `DeepSeekProvider.prepare()` 只有上游未提供 content fragments 时才能生成 whole-content fallback；已有 fragments 时禁止再覆盖整段。
- provider materialize 后才进行 pointer/span、credential 和 token 守恒校验；任何不匹配均阻止发送。

## Memory source query exclusion

`MemorySourceQueryTool` 的实现、注册、输入 schema、分页、权限、错误码和 canonical 返回字段不得修改。必须满足两层断言：

1. 直接调用工具时结果与现有 golden contract 逐字一致，无 marker 字段/行。
2. 该结果作为 tool continuation 历史时，controller 可以在外层 tool message content 前添加 completed-at marker；移除可选前缀后，剩余 body 与工具原结果逐字一致。

## Failure behavior

以下情况全部在模型发送前 fail closed：

- 独立配置缺失/无效或消费者拿到不同 snapshot revision；
- raw/long-term/coverage 来源缺失、不可读、hash 不匹配或时间非法；
- 最终预算超限且不能在不破坏既有覆盖/选择合同下裁剪；
- marker/body span 重叠、越界、无法从最终 payload 回读或来源缺失；
- 工具 event 缺少 accepted/completed 时刻、call 配对失效或需要改变结构化字段才能标注。

失败不得产生 provider request；配置/来源失败发生在 raw mutation 和工具副作用前。不得以记忆 `created_at`、prompt 构建时刻或系统当前时区静默补救。

## Acceptance matrix

| Flow | Success | Boundary | Critical failure |
|---|---|---|---|
| chat/recent | 密集消息仅首 marker | gap/refresh exact、跨日、倒序 | recent 含 marker 超限明确失败 |
| long-term | 来源 min/max 范围且顺序不变 | 重叠/相等范围、相关性倒序 | missing/hash/time invalid 无降级 |
| overview | 独立首 range marker | coverage 引用时间乱序 | coverage gap/invalid 阻断 |
| curation | 三 block 独立首 marker、JSON 字段不变 | 空 block、重复正文 span | template span/来源不一致阻断 |
| tool | assistant→tool 配对和 body 子串不变 | 并行 calls、长执行、跨日、倒序 | 缺时间/配对/span 阻断 |
| debug/estimate | 所见 payload 即发送 payload且 marker 计费 | debug on/off 固定时钟等价 | 重叠 part/摘要变化阻断 |
| source query | 直接结果 golden 不变 | 结果作为普通 tool body | 工具实现/schema 任何变化失败 |

## Future consumer checklist

新日志类提示消费者接入时必须：

1. 声明独立 block 边界及明确非日志排除项。
2. 为每项提供原样 body、稳定 identity、真实来源和 EVENT/SOURCE_RANGE span；只有未来另行定义具体 schema/version 的 legacy adapter 才可提供 RECORDED span，当前消费者不得自行选择该降级。
3. 复用同一 snapshot 的 annotator，不实现自己的 gap/date/refresh 规则。
4. 把 marker/body fragments 映射成不重叠最终 `RequestPart`。
5. 在预算和 provider materialize 前完成标注。
6. 增加成功、临界、来源失败、顺序/协议不变和消费者清单测试。
