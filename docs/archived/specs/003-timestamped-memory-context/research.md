# Phase 0 Research: 面向模型历史上下文的智能时间段标注

**Date**: 2026-07-20

**Status**: Complete — 无待澄清项

## 1. 统一模块的职责边界

**Decision**: 在 `src/bai_agent/prompting/temporal.py` 建立唯一、无 I/O、不可变且保持输入顺序的时间标注实现。它只接收 `TemporalLogEntry` 与一份 `TemporalSegmentationPolicy`，输出带 marker/body 片段和精确 span 的 `AnnotatedHistory`；archive 读取、长期记忆来源解析、模板变量和 provider 协议均由调用方适配。

**Rationale**: 近期聊天、整理、长期记忆和工具历史共享的是分段规则，而不是存储或 wire 格式。纯函数边界既能让所有消费者复用，也能用固定输入直接证明确定性、稀疏性和 O(n) 性能。

**Alternatives considered**: 把逻辑写进 `PromptAssembler` 无法供 curation/controller 复用；各消费者复制会造成阈值漂移；让通用模块读取 archive/store 会把未来日志类型绑定到记忆存储；按时间排序或分析正文会违反顺序与非语义分段要求。

## 2. 分段与格式化算法

**Decision**: 非空区块第一项必标记；之后在有效间隔 `current.start - previous.end >= long_gap`、启用跨日且 `previous.end` 与 `current.start` 的显示时区日期不同、`current.start < previous.start`，或 `current.start - last_marker_entry.start >= refresh` 时在当前项前标记。范围重叠的有效间隔取零；多条件同项只产生一个标记；阈值判断使用原始 aware datetime 的 UTC 瞬时，最后才转换到显示时区并精确到分钟。

**Rationale**: 这逐项落实 BR-002—BR-004，并让临界值、范围重叠、倒序和 DST 行为不受输出精度影响。marker 使用当前承载项的时间语义，而触发原因只留在领域/来源数据中，不污染固定可见格式。

**Alternatives considered**: 用格式化后的分钟计算会错判秒级临界；用上一项开始时间计算 gap 会夸大长范围后的间隔；定时插入无承载尾 marker 会制造孤立元数据；同时输出多个原因 marker 会破坏稀疏性。

## 3. 跨平台 IANA 时区

**Decision**: 使用标准库 `zoneinfo.ZoneInfo` 验证与转换 IANA 时区，并在运行依赖中加入 `tzdata>=2026.3` 作为系统时区库缺失时的第一方后备。配置时区无法解析时 fail closed，不用固定偏移或本机默认时区替代。

**Rationale**: Python 的 `zoneinfo` 会优先使用系统时区数据，再查找 `tzdata`；显式依赖 `tzdata` 能保证各 Ubuntu/WSL 环境对 `Asia/Shanghai` 和含 DST 的验收输出一致。固定偏移无法表达 DST，静默回退会让同一配置产生不同标记。

**Alternatives considered**: 只依赖系统时区数据会让精简 Linux/WSL 镜像出现 `ZoneInfoNotFoundError`；使用 `pytz`/dateutil 增加不必要的第二套 API；把所有时间显示成 UTC 不符合可配置显示时区需求。

**Sources**: [Python `zoneinfo` data sources](https://docs.python.org/3/library/zoneinfo.html#data-sources)、[Python-owned `tzdata` package](https://pypi.org/project/tzdata/)

## 4. 独立配置与原子重载

**Decision**: 新增必需的 `config/history_timestamps.toml`：

```toml
schema_version = 1
display_timezone = "Asia/Shanghai"
long_gap_minutes = 30
continuous_segment_refresh_minutes = 120
split_on_local_date_change = true
```

将其加入 `config.loader.MANIFESTS`，生成 `asset_id="config:history_timestamps"`、`kind="history_timestamp_policy"` 的 `ConfigAsset` 并参与 snapshot revision。校验器使用严格字段集合，拒绝缺失/未知字段、bool/int 混淆、错误类型、阈值越界、refresh 小于 gap 和非法 IANA 时区；默认值只存在于版本控制文件，不在 loader 中静默补齐。

**Rationale**: 现有 `AgentApplication` 已在每轮前读取完整 `ConfigSnapshot` 并整体替换依赖。把时间策略纳入同一快照即可保证所有消费者同轮使用同一值，并让无效 reload 在 raw 写入和模型调用前失败。

**Alternatives considered**: 并入 `agent.toml` 不满足独立配置要求；各消费者自行读文件会混用新旧值；进程启动后固定策略不符合既有重载边界；代码缺省会掩盖文件缺失或显式错误。

## 5. 派生记忆的可信时间

**Decision**: 每次构建只读取一次已验证 raw 快照并建立 `record_id -> RawRecord` 索引。Raw 项使用自身 `created_at` 点时间；长期记忆对全部 `source_refs` 校验后取最早/最晚事件时间；coverage overview 对全部 coverage record id/hash 校验后取最早/最晚时间，不能假设引用顺序等于时间顺序。即使范围两端相同，派生项仍使用 `SOURCE_RANGE` 标签以保留来源语义。

**Rationale**: 事件真相已经存在于 raw，动态投影可避免复制和漂移；单索引使总复杂度为 O(raw + refs)，也不改变持久化 schema。最小/最大计算适用于相关性排序、重复引用、重叠范围和倒序数据。

**Alternatives considered**: 使用长期记忆 `created_at` 会把整理时间冒充事件时间；持久化派生范围会产生第二事实源和迁移；按首/末引用取值在引用乱序时错误；逐项 `archive.read_all()` 造成 N+1 和 10k 性能风险。

## 6. 旧格式与来源完整性

**Decision**: 通用合同保留显式 `RECORDED` 时间语义，只允许后续功能明确 schema/version、实现识别规则且定义为“从未保存来源时间”的 legacy 适配器使用可信 `created_at`。当前版本没有持久化 `RECORDED` 入口；仓库 schema v1 从最初就强制非空 `source_refs`，因此所有现行长期记忆必须走来源范围。缺失、不可读、hash 不匹配、时间无效和未识别格式继续使用既有完整性错误并 fail closed，绝不降级。`LongTermStore` 既有 last-valid 文件恢复发生在时间投影之前；真正被选中的恢复文档仍须严格验证来源，恢复机制不等同于 `RECORDED` 降级。

**Rationale**: 这同时满足澄清后的兼容原则与当前代码事实，避免为了制造测试路径而放宽 v1 schema 或猜测不存在的 v0。本功能只测试统一 `RECORDED` formatter 和“当前无持久化入口”的失败边界；未来如果加入真实 legacy reader，必须通过独立功能先明确版本识别和可信字段，再复用该 formatter。

**Alternatives considered**: 捕获任意 `BaiError` 后使用记忆 `created_at` 会隐藏损坏；允许 v1 空 `source_refs` 会削弱来源追溯；自动迁移/回填超出范围且可能伪造历史。

## 7. 各逻辑区块的适配

**Decision**: 以下八个区块分别从空状态调用同一 annotator：主聊天的 `memory_overview`、`long_term_memories`、`recent_records`、`current_input`；整理提示的 `batch_records`、`existing_memories`、`current_overview`；当前轮累计工具调用/结果历史。`current_input` 使用本轮已建立 provisional `RawRecord.created_at`，其 marker 是 `UNTRUSTED_DATA` 元数据而正文保持 `USER_INSTRUCTION`；人格、系统规则、批次元数据正文与 output schema 不作为历史时间线。整理历史采用“marker 行 + 单项 canonical JSON 行”的表示，保持字段和输出 `CurationProposal` schema 不变。

**Rationale**: 独立调用直接保证每个非空区块都有首 marker 且不跨区块泄漏状态。调用方保有正文序列化职责，通用模块无需理解 `role:` 文本、YAML memory 或 JSON curation schema。

**Alternatives considered**: 一次请求共用全局时间线会让旧长期记忆影响近期聊天段落；把整个 curation JSON 数组外包一层 marker 无法逐项分段；在重试时读取 wall clock 会使审批与实发不一致；标注 batch metadata/output schema 正文会把非日志指令误作历史。

## 8. 工具调用与结果事件时间

**Decision**: 在成功模型响应完成解析/校验并被 `ModelCallGateway` 接受时采样 tool-call batch 的 `accepted_at`；同一 assistant 响应内多个 tool calls 共享此时刻并作为一个协议消息/日志项。`ToolExecutor` 在执行、安全/大小检查和事务处理全部完成、形成可发送 `ToolResult` 后采样每个结果的 `completed_at`。时间通过注入式 clock 取得并放在排除序列化的运行时 envelope/字段中；重试、审批等待和 prompt 重建不重新采样。

**Rationale**: 两个采样点分别对应澄清中的“结果被接受”和“执行完成并可发送”，也涵盖成功和安全错误结果。注入时钟让临界、跨日、倒序和重试测试确定。

**Alternatives considered**: provider 内采样不适用于其他 provider 且早于 gateway 接受；prompt 构建时采样会把等待时间误作事件；强行单调化系统时钟会隐藏倒退；所有并行 call 拆成虚假的多条 assistant 消息会破坏协议。

## 9. 工具协议中的标记承载

**Decision**: 不新增 marker role/message。边界命中时将 `marker + "\n" + original_body` 放入既有 `assistant.content` 或 `tool.content`；空 assistant body 可只含 marker。`assistant.tool_calls`、id/name/arguments、`tool.tool_call_id` 及 canonical ToolResult JSON body 不变，原 body 在最终 content 中保持逐字连续子串。工具历史跨 continuation round 保存未标注原始事件并整体重建，避免重复前缀或每 round 错误首标。

**Rationale**: DeepSeek/OpenAI-compatible 工具回放要求 assistant/tool_calls 紧邻匹配的 tool 消息，且没有可移植的独立可见 metadata 槽。把 marker 放在对应消息正文前，能表达调用与结果各自时间，同时不插入破坏配对的消息。

**Alternatives considered**: 独立 system/user 消息可能破坏工具配对；修改 arguments/result JSON 改变公开合同；provider 私有字段可能不可见或被拒；增量修改已渲染字符串会重复 marker。

**Sources**: [DeepSeek chat completion message schema](https://api-docs.deepseek.com/api/create-chat-completion/)、[DeepSeek tool calls guide](https://api-docs.deepseek.com/guides/tool_calls/)

## 10. 预算、来源与调试真实性

**Decision**: 所有 marker 在字符预算、provider token estimator、provenance 校验和最终物化前进入正文。`AnnotatedHistory` 返回 marker/body 的精确 `[start,end)` span；marker part 的来源同时包含真实 `config:history_timestamps` 资产和触发项来源，trust 保持 `UNTRUSTED_DATA`。长期记忆按既有相关性顺序做含 marker 的精确增量预算；formatter 本身不删项。模板变量替换时直接记录绝对偏移，不用 `find()` 猜重复文本。

工具消息的 marker/body 在同一 `/messages/{i}/content` 下使用互不重叠 span；DeepSeek adapter 仅在上游未提供片段时生成 whole-content fallback，并为 tool_calls 保留独立来源，禁止 marker/body 与整段 content part 重叠以免估算重复。

**Rationale**: 只有最终文本先形成，预算和调试视图才能与真实请求一致。细粒度、非重叠 span 同时证明 marker 由哪份策略和哪条事件触发，也让 estimator 的 part 守恒成立。

**Alternatives considered**: 超限时静默删除 marker 违反真实性；整段聚合来源无法关联单个 marker；反向搜索重复正文会误归因；保留重叠 whole-content part 会 double count；把配置来源标为 trusted instruction 会错误提升数据权限。

## 11. 模型可见的不可信数据边界

**Decision**: 内部 trust metadata 继续用于校验与 TUI，但不再把它当成模型可见边界。所有历史、长期记忆、覆盖概览、整理输入和工具事件在 provider 支持的 `content` 字段中按逻辑块加入一对简短的 `[UNTRUSTED block#8位ID]`/`[/UNTRUSTED block#8位ID]`，block 与由完整正文派生的短 id 必须匹配。当前输入例外地把可信时间 marker 放在边界外，只把用户正文放入 `current_input` 块；其他历史 marker 暂不移动。system 中 persona 与边界说明共同发送，但分别由当前 `ConfigSnapshot` 的真实 asset/hash/revision 和独立 span 归因。

**Rationale**: DeepSeek wire 只发送标准 role/content/tool 字段；共享 renderer 使 LLM、预算、estimator、provenance 和 TUI 面对同一结构，同时避免逐记录重复标签。内容绑定 id 让数据正文中的仿冒边界不能与应用生成的外层结构混淆。

**Alternatives considered**: 只保留 `Message.trust`/`RequestPart.trust` 会使 LLM 看不到边界；给 provider 增加自定义 `trust` 字段不受支持；每条 record 独立包装会增加大量重复标签和视觉噪声；把 persona 与 boundary instruction 整段只归因到 persona 文件会产生错误溯源。

## 12. 存储、来源工具、测试与文档边界

**Decision**: 不修改 raw/long-term 持久化格式，不写回 marker，不迁移 UTC 时间；`src/bai_agent/tools/memory_source.py` 的输入、输出、分页、权限和返回 JSON 逐字保持。该工具作为工具历史返回模型时，只允许 controller 在外层 message content 前添加时间 marker，工具自身返回 body 仍是精确子串。分层测试覆盖算法/配置/投影/预算、聊天/整理/工具集成、wire/provenance/debug 回归、10k 性能和 Ubuntu/WSL 时区；README 与 001/002/003 合同和 quickstart 随实现同步。

**Rationale**: 用户明确排除来源追溯工具，展示元数据也不应污染事实存储。黄金合同与 no-write 测试比仅依赖代码审查更能防止未来接入时越界。

**Alternatives considered**: 在来源工具返回数组内加 marker 会改变调用者可解析格式；把 marker 写入 raw 正文会使重复构建重复标注；只测 formatter 无法发现 provider 配对、预算或消费者遗漏。
