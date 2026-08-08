# Phase 0 Research: 提示词追踪调试工具

**Date**: 2026-07-20
**Status**: Complete — 无待澄清项

## 1. 终端界面框架

**Decision**: 使用 Textual `>=8.2,<9`，每次待批模型调用启动一个短生命周期全屏应用；界面退出后恢复原终端内容。原生 Ubuntu 24.04 是主要支持与性能门禁平台，Windows 11/PowerShell 只承担次要功能兼容验收，macOS 不在范围内。

**Rationale**: 需求包含超长正文滚动、固定操作区、交互式颜色/无色降级、终端尺寸变化、显式批准和可重复的无头交互测试。Textual 的 application mode、`RichLog` 与 Pilot 正好覆盖这些边界，单一依赖比自行维护 ANSI alternate-screen、键盘读取、重绘和 Windows 兼容代码更清晰；固定 Linux 门禁环境避免把 Windows runner 波动混入 500 ms 强制指标。

**Alternatives considered**: Rich 单独使用缺少完整输入/生命周期模型；`prompt_toolkit` 可行但长内容布局和测试需更多自定义代码；手写 ANSI/Win32 状态机会扩大安全清屏和兼容风险。

**Sources**: [Textual application mode](https://textual.textualize.io/guide/app/)、[RichLog](https://textual.textualize.io/widgets/rich_log/)、[Textual testing](https://textual.textualize.io/guide/testing/)

## 2. 模型调用统一边界

**Decision**: 新增唯一 `ModelCallGateway`；provider 适配器统一为无 I/O 的 `prepare()`、唯一一次把 prepared request 转为深度不可变 SDK 参数的 `materialize_sdk_kwargs()`，以及只进行一次物理请求的 `send_once()`。重试由网关编排，每次尝试都重新准备、物化、展示并批准。

**Rationale**: 只有展示唯一物化后的 SDK 参数才能证明真实最终字段；同一个 materialized object 既用于摘要和 TUI，又原样交给 sender，避免展示、摘要和发送各自序列化。只有把物化、发送与重试收口到一个入口，才能证明聊天、整理、工具续接和未来辅助人格没有未追踪旁路。重试作为新物理调用必须具有新的尝试号和批准。

**Alternatives considered**: 在 controller 内插入调试回调会漏掉整理和 provider 内部重试；让 `prepare()` 与 `send_once()` 各自生成 SDK kwargs 会产生双重 materialization；在 OpenAI SDK transport 层抓包无法可靠恢复业务来源；让各 provider 自行做 UI 会破坏复用与测试隔离。

**2026-07-20 defect finding**: OpenAI SDK 自带重试会绕过网关逐物理请求审批，因此生产 client 固定 `max_retries=0`。DeepSeek 官方只把网络失败及配置中的 429/500/503 视为本项目可重试错误；400/401/402/403/422 必须一次失败。错误 body 不进入领域错误、日志或 journal。

**2026-07-20 serialization defect finding**: `MaterializedSendPayload` 为防止批准后突变而把嵌套 dict/list 冻结成 `mappingproxy`/tuple；OpenAI SDK 的 JSON encoder 不接受 `mappingproxy`，真实发送会在网络前抛出 TypeError。保留冻结对象作为唯一审批事实，在 `send_once()` 最后一层以 `thaw_json()` 生成规范值完全相同的原生容器，是同时满足不可变批准与 SDK 编码的最小方案；真实 AsyncOpenAI + MockTransport 合同测试验证 wire JSON。

## 3. 最终请求真实性与批准绑定

**Decision**: `PreparedProviderRequest` 保留 provider 适配结果和来源，`materialize_sdk_kwargs()` 唯一一次生成深度不可变、可 JSON 序列化且不含认证的 `MaterializedSendPayload`。TUI 展示与 sender 发送引用同一 materialized object，批准令牌绑定 `call_id + attempt + canonical_payload_sha256`，发送前重新计算摘要。

**Rationale**: 同一物化对象消除展示副本与发送副本分叉；规范化摘要能发现批准后的突变。approve 后先关闭 TUI 并释放 prepared/part/SourceRef，sender 只保留不可变发送载荷，`send_once` 在 `finally` 释放；合同测试同时捕获 SDK 参数和 mock HTTP 请求，验证提示承载字段逐字段相同。

**Alternatives considered**: UI 展示 provider-neutral request 会遗漏适配差异；字符串化后再反序列化可能改变类型或顺序；仅靠对象身份不能阻止嵌套对象突变。

## 4. 来源模型与完整性

**Decision**: 配置加载结果由裸文本升级为带路径、摘要和修订的 `ConfigAsset`；构建过程产生 `RequestPart` 和有序 `SourceRef`。provider 适配后以 JSON Pointer 和 `[start,end)` 正文区间将 part 映射到最终载荷，并在展示前校验所有参与内容均可归因。

**Rationale**: 来源必须来自真实加载/选择关系，不能根据相同文本猜测。文件、记忆记录、运行时输入、工具结果和 provider 协议开销采用同一引用结构；被排除、空内容和来源未知也保留明确状态，但只有参与内容进入最终请求。

**Alternatives considered**: 仅在最终字符串上反向搜索会误归因重复正文；只保留文件名无法定位数据记录或构建修订；在 TUI 临时拼来源会丢失上游选择信息。

## 5. 上下文估算与守恒分摊

**Decision**: `TokenEstimator.estimate(materialized, request_parts)` 一次处理最终 provider 载荷，再按确定性的前缀累计差值或已知 chat template 边界分摊边际 token；角色、分隔符、工具 schema 和协议包装记为可见的 `generated:provider_protocol_overhead`。强制 `estimated_input_tokens = sum(part_tokens) + protocol_overhead_tokens`。

DeepSeek 首版采用官方公开字符比例、UTF-8/JSON/工具包装开销和可配置安全裕度形成标为“≈”的保守估算；若当前 provider/model 无可信方法则返回 `unavailable(reason)`，不使用“字符数/4”冒充精确 token。

**Rationale**: 分段独立计数会因跨段 token 合并和 chat 协议开销导致分段之和不等于总数。单次估算最终载荷并显式分配协议开销既满足守恒，也保持估算语义诚实。响应返回的 `usage` 始终作为实际值单独展示。

**Alternatives considered**: 为首版捆绑完整 tokenizer 增加体积和升级负担；逐段独立估算不守恒；固定字符除数对中文、Emoji、JSON 和工具定义偏差不可控。

**Sources**: [DeepSeek token usage](https://api-docs.deepseek.com/quick_start/token_usage/)、[Chat Completion usage fields](https://api-docs.deepseek.com/api/create-chat-completion)

## 6. 模型容量与 DeepSeek 配置

**Decision**: 在每个 model profile 中显式维护 `context_window_tokens`、`max_output_tokens`、provider `max_output_cap` 和 `token_estimator`，启动时验证预留不超过能力上限；chat 与 memory curator 从即将弃用的 `deepseek-chat` 迁移至 `deepseek-v4-flash`，保留 `thinking_enabled=false`、`max_output_tokens=8192` 及两个 profile 各自现有的 temperature、tools、structured-output 参数。本地 `context_budget.max_input_tokens` 继续只表示 Agent 组装预算，不冒充模型容量。

**2026-07-20 defect finding**: V4 的 thinking 默认值是 enabled，配置布尔值本身不会影响 API；OpenAI SDK 请求必须通过 `extra_body={"thinking":{"type":"disabled"}}` 显式关闭。非思考工具续接仍必须回放产生调用的 assistant/tool_calls，再追加匹配 tool_call_id 的 tool result；否则 provider 返回不可重试 400。

**Rationale**: 峰值占用必须基于当前请求的输出预留与当前模型容量，不能从其他模型或本地预算推断。DeepSeek 当前文档给出 V4 Flash/Pro 的 1M context 和 384K 最大输出，并公告旧 alias 的弃用时间，因此能力元数据必须可维护且经校验。

**Alternatives considered**: 在 Python 中硬编码容量会违反配置原则且难以升级；继续用 alias 会引入临近弃用风险；静默 `min()` 修正非法 max output 会隐藏配置错误。

**Sources**: [DeepSeek 官方模型与价格说明](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)

## 7. 拒绝整轮回滚与崩溃恢复

**Decision**: 增加 `TurnUnitOfWork` 和单文件事务日志，状态为 `PREPARED`、`READY_PENDING` 与 `READY_TO_COMMIT`。`PREPARED` 只持久化暂存输入和轮前基线；维护者明确拒绝或未决定的该阶段重启时删除暂存并回到 `ABSENT`。普通 provider/网络失败且重试结束时原子转为 `READY_PENDING`，幂等发布且只发布一条 USER pending。事务恢复完成后，只有显式 `--resume-pending` 重发；默认启动和 `--discard-pending` 在结构/hash/长期引用校验后以原子尾段替换放弃该 pending。整轮成功后写入 assistant 和可选长期记忆目标，转为 `READY_TO_COMMIT`，再幂等发布完整 raw turn 与长期记忆，最后删除日志；重启见任一 READY 状态必须按其目标前滚完成。

**Rationale**: 该方案同时满足 001 的“输入在生成前持久化”、provider 失败后可显式恢复和 002 的“明确拒绝后轮次从未存在”。完整 USER/ASSISTANT 仍不可删除；未配对尾部 USER 被明确定义为未完成、可放弃轮次。尾段原子替换在每个故障点只留下完整旧状态或完整新状态，不需要按任意 turn id 删除或重写长期记忆。

**Alternatives considered**: 纯内存暂存违反生成前持久化；任意 turn id 删除或物理 unlink 尾 segment 会破坏归档和崩溃语义；为一次尾部原子替换增加数据库或第二份删除 journal 对单用户文件存储过重。最终只允许截去最后一条经校验的 USER，并允许最后一个 segment 为空以复用现有 atomic replace。

## 8. 记忆整理与轮次工作视图

**Decision**: 将 `CurationService` 拆为 `propose()` 与最终 `commit()`；整理响应、长期记忆候选、工具结果和状态候选先进入 `TurnWorkingSet`，只有整轮完成后随事务发布。提示构建读取“已提交基线 + 当前轮暂存”的虚拟视图。

**Rationale**: 若整理调用已批准而后续聊天调用被拒绝，其本地派生记忆仍必须丢弃；同时聊天调用又应看到当前轮正常路径本会看到的整理结果。提案/提交分离避免补偿式删除。

**Alternatives considered**: 整理后立即写长期记忆无法无痕回滚；拒绝时反向修改 YAML 会和人工编辑/修订冲突；调试模式绕过整理会改变实际请求。

## 9. 工具副作用策略

**Decision**: 审计并声明当前工具全部为 `read_only=true`，其结果只进入 `TurnWorkingSet`。未来有写副作用的工具必须实现可持久恢复的 `prepare/commit/rollback` 或明确补偿协议；能力缺失、声明与实现不一致或 prepare 失败时，执行器必须在任何副作用前拒绝。

**Rationale**: 外部模型调用无法撤回但其本地结果可丢弃；任意外部写工具则可能破坏“本轮从未存在”。将事务能力作为工具契约可防止未来扩展绕过回滚语义。

**Alternatives considered**: 假定所有未来工具可回滚不安全；拒绝所有工具不符合现有工具续接范围；通用分布式事务不现实。

## 10. 安全、生命周期与非交互环境

**Decision**: 在创建轮次暂存或构建任何模型调用前验证 stdin/stdout 均为 TTY；否则以可操作错误安全失败。无色降级只适用于交互式 TTY。最终载荷在显示前和发送前执行凭据检测。approve 后立即退出 approval app、清屏并释放正文/来源，sender 仅保留同一不可变 materialized payload 且在 `send_once` 成功或失败后的 `finally` 释放；实际用量由普通聊天输出显示不含原文的摘要，不重新打开 TUI。

**Rationale**: 重定向环境无法取得明确批准，也可能把私人提示写入管道或日志。两次安全检查覆盖构建期和批准后变化。短生命周期 app 让终端恢复与对象释放有清晰边界。

**Alternatives considered**: 非 TTY 自动批准违反 FR-027；普通打印会泄漏到重定向目标；持久化 trace 便于回看但直接违反不落盘要求。

## 11. 测试与文档同步策略

**Decision**: 单元测试覆盖摘要、来源校验、估算守恒和三态事务；合同测试覆盖 CLI/TUI/provider/gateway/写工具能力；集成测试覆盖多调用、调试开关等价、明确拒绝和普通失败 pending；故障注入覆盖每个 journal/fsync/replace/READY/raw/YAML/cleanup 点；性能测试在 Ubuntu 24.04/Python 3.13/80×24 `xterm-256color` 中从 frozen request、来源和估算就绪测到标题/身份/上下文摘要 mounted，记录首次冷启动并以 30 次同进程启动 p95≤500 ms 为小样本门禁、300K 字符大载荷 p95≤2000 ms 为门禁，同时覆盖 1,000 次释放。trace 区域使用虚拟化 `RichLog` 只渲染可见行，首帧后异步填充正文。DeepSeek 估算 fixture 至少 40 项，记录模型 id、采集日期、官方 usage、payload hash 和刷新说明。README、001 合同/quickstart、002 产物和兼容性 workflow 随对应实现同提交更新。

**Rationale**: 该功能横跨安全边界、物理网络调用和多个持久化文件，仅 happy-path UI 测试无法证明真实性、无旁路、恢复和尾部丢弃语义。分层测试能直接对应 BR-001—BR-011，同时控制定位成本。

**Alternatives considered**: 只做端到端测试难以稳定注入所有崩溃点；只做 mock 单元测试无法证明 SDK/HTTP 出站一致；发布后补文档违反项目宪章。
