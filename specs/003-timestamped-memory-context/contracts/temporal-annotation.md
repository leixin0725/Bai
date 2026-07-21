# Contract: 统一时间段标注

**Version**: 1.0.0

**Applies to**: 所有构建模型可见日志类历史的模块

## Input contract

`annotate_history(entries, policy, separator="\n") -> AnnotatedHistory`

- `entries` 是已按上游选择规则排好的 `TemporalLogEntry` 有序序列；实现不得排序、合并、删除或复制。
- 每项必须有稳定 id、原样 body、至少一个真实来源和合法 `TemporalSpan`。
- `policy` 必须来自一次完整、已验证的 `ConfigSnapshot`，且在调用期间不可变。
- 每次调用代表一个独立逻辑区块，禁止传入或返回供下一区块延续的可变分段状态。
- 空输入返回空 text、空 markers、空 fragments，不生成“当前时间”或尾 marker。

## Boundary algorithm

按输入顺序单次扫描。所有比较以完整 aware datetime 所代表的 UTC 瞬时进行；不得先转换/截断成显示分钟。

对第 `i` 项：

1. `i == 0`：原因集合为 `{FIRST}`。
2. 否则令：
   - `previous = entries[i-1]`
   - `last_marker_entry` 为最近一个承载 marker 的项
   - `effective_gap = max(current.start - previous.end, 0)`
3. 分别判断：
   - `effective_gap >= policy.long_gap` → `LONG_GAP`
   - `policy.split_on_local_date_change` 且 `previous.end` 与 `current.start` 转换到显示时区后的自然日不同 → `LOCAL_DATE_CHANGE`
   - `current.start < previous.start` → `TIME_REVERSAL`
   - `current.start - last_marker_entry.start >= policy.continuous_refresh` → `REFRESH`
4. 原因集合非空时，在 current body 前生成且只生成一个 marker，并把 current 设为新的 `last_marker_entry`。
5. 无论是否标记，都把 current 设为新的 previous。最后一项后不得补 marker。

### Exact boundary consequences

- gap 恰好等于阈值：分段；比阈值少最小可观测时间单位：不因 gap 分段。
- refresh 恰好等于阈值：在当前项前刷新，并从当前项重新计时。
- 范围相交或相接使 `current.start <= previous.end` 时，gap 取零；仍可因跨日、refresh 或倒序标记。
- 倒序判断使用 start；倒序不改变顺序，也不把负 gap 当成长 gap。
- 跨日比较使用 `previous.end` 与 `current.start` 的本地日期，而不是两个 start。
- 多个条件同项命中只输出一个 marker；原因集合保留全部命中值供测试/调试。
- 同刻事件不会仅因同刻而分段；区块首项规则仍适用。

## Rendering contract

固定输出模板不可配置、翻译、使用相对时间或省略重复日期/偏移：

```text
[时间：YYYY-MM-DD HH:mm ±HH:MM]
[时间范围：YYYY-MM-DD HH:mm ±HH:MM 至 YYYY-MM-DD HH:mm ±HH:MM]
[记录时间：YYYY-MM-DD HH:mm ±HH:MM]
```

- `EVENT` 使用第一种，时间为 entry start。
- `SOURCE_RANGE` 始终使用第二种；即使 start/end 同刻也完整重复两端。
- `RECORDED` 使用第三种，时间为可信记录时间；当前版本没有从持久化旧格式产生该 kind 的 adapter，只有未来另行定义具体 schema/version 的适配器才能使用。
- 年/月/日/时/分补零，24 小时制；UTC offset 必须为 `±HH:MM`。
- marker 文本不含原因、entry id、时区名称或 prompt 构建时刻。
- marker 作为紧邻承载 body 前的独立一行：`marker + "\n" + body`。
- body 必须作为逐字连续子串存在；formatter 不改变其中已有换行、JSON、角色前缀或 Unicode。

## Provenance contract

每个 marker 生成独立 included `RequestPart`/fragment：

- trust 固定为 `UNTRUSTED_DATA`；
- sources 至少包含：
  1. `config:history_timestamps` 的真实路径、hash 和 snapshot revision；
  2. 当前承载 entry 的全部真实来源，或可无损指向这些来源的聚合 ref；
- text span 必须从最终 provider payload 的 JSON Pointer 回读 marker 原文；
- body 使用另一个不重叠 span 和原有来源；
- separator 如模型可见，必须被唯一 fragment/生成来源覆盖；
- 禁止再以 whole-content part 覆盖 marker/body 已覆盖的字符。

时间触发原因可作为不含正文的诊断 metadata，但不得取代配置和事件来源。

### Current input contract

- `current_input` 也作为一个独立 EVENT block 调用本合同；时间必须取本轮已建立 provisional `RawRecord.created_at`，不得在 retry、pending resume、批准或 sender 边界读取 wall clock。
- marker sources 必须同时包含真实 `config:history_timestamps` asset 与 provisional USER record 的 runtime source；正文 fragment trust 保持 `USER_INSTRUCTION`。
- 只有 marker 及其分隔符置于 visible `untrusted_data` block，当前用户正文在该边界外继续作为本轮指令；不得把整个 current input 降级为历史数据。
- 生成 marker/boundary 只存在于 PromptContext/RequestPart/materialized payload，不进入 raw JSONL 或 long-term YAML；下一轮 recent history 从原始正文和持久化 `created_at` 重新标注。

### Visible untrusted-data boundary contract

所有不可信逻辑块在最终 provider `content` 中使用共享 renderer 包装一次：

```text
<<<UNTRUSTED_DATA_BEGIN block=<logical-name> id=<content-bound-32-hex>>>
<untrusted content>
<<<UNTRUSTED_DATA_END block=<same-name> id=<same-id>>>
```

- opening/closing、inner fragments 与必要 separator 都必须是最终 payload 的 included、无重叠 span；不得只依赖 `Message.trust`/`RequestPart.trust` 或 provider 不支持的扩展字段。
- wrapper 来源包含真实 `prompt:untrusted_memory_boundary` asset/hash/revision 与 renderer generated source；inner marker/body 保持各自原来源。
- `chat.md`/`memory_curator.md` 与 boundary instruction 可以共享 system message，但必须拥有各自精确 part/span；generated separator 单独归因，不能把整个 system content 只算作 persona 文件。
- visible wrapper 字符先于字符预算、token 估算和 provenance 校验生成；TUI、estimator 与 `send_once()` 看到同一字符串。

## Budget and failure contract

- marker 必须先生成，再执行 overview/recent/long-term 字符预算、provider token 估算、provenance 和凭据门禁。
- visible untrusted wrapper 必须在字符预算和 provider token 估算前生成；预算比较的对象就是 materialized payload 中的最终 block 文本。
- 选择型预算按既有候选顺序计算“加入当前项后整个区块的精确渲染增量”；不得用未标注 body 大小替代。
- formatter 不负责截断、删项或删 marker；调用方无法在预算内保留完整不变量时，以既有可操作 overflow 错误失败。
- 以下情况必须在模型请求前失败：naive datetime、无效 offset、end < start、重复 entry id、空来源、非法 kind/span 组合、非法策略或不可归因 fragment。
- 时间倒序不是数据错误；必须保留顺序并通过 `TIME_REVERSAL` marker 显示断点。

## Determinism and complexity

- 相同 ordered entries、policy、separator 必须产生逐字相同 text、相同 marker reason/位置和 span。
- 算法不得读取 wall clock、环境时区、locale、文件或 provider 状态。
- 单次标注为 O(n) 时间；除输出/fragment 外不建立平方级中间结构。
- 10,000 项主要平台验收必须小于 1 秒；性能用固定输入、预热和不含文件 I/O 的计时窗口。

## Representative examples

默认策略：Asia/Shanghai，gap 30 分钟，refresh 120 分钟，跨日开启。

### Dense chat

输入点时间为 09:00、09:05、09:20：

```text
[时间：2026-07-20 09:00 +08:00]
user: 第一条
assistant: 第二条
user: 第三条
```

### Exact long gap

上一项结束 09:00，当前项开始 09:30：

```text
[时间：2026-07-20 09:00 +08:00]
user: 第一段
[时间：2026-07-20 09:30 +08:00]
user: 第二段
```

### Source range

派生记忆来源覆盖两个 offset 不同的时刻；转换到显示时区后仍完整输出两端：

```text
[时间范围：2026-07-20 08:00 +08:00 至 2026-07-20 10:45 +08:00]
用户倾向于先看可验证结论。
```

## Acceptance mapping

| Rules | Automated evidence |
|---|---|
| BR-001 | 输出一一对应、body 子串、顺序/角色/工具字段不变属性测试 |
| BR-002 | first、gap `<`/`==`/`>`、跨日、重叠、同刻、倒序表驱动测试 |
| BR-003 | refresh `<`/`==`/`>`、重置与无尾 marker 测试 |
| BR-004 | 100 条密集项及多原因合一测试 |
| BR-005 | EVENT/SOURCE_RANGE/RECORDED 与错误来源测试 |
| BR-006 | 三种精确格式、时区/DST/offset、重复执行测试 |
| BR-008 | 含 marker 预算和 non-overlap span 守恒测试 |
| BR-009 | 10,000 项 O(n)/1 秒性能测试 |
