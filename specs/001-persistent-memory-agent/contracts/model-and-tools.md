# Contract: 模型供应商、工具、状态与运行控制

## 1. 领域边界

核心层通过 Python `Protocol` 和不可变对象依赖以下端口：

```python
class ModelProvider(Protocol):
    async def complete(self, request: CompletionRequest) -> CompletionResult: ...
    def capabilities(self, model_profile_id: str) -> ProviderCapabilities: ...

class Tool(Protocol):
    @property
    def definition(self) -> ToolDefinition: ...
    async def invoke(
        self,
        arguments: JsonObject,
        context: ToolExecutionContext,
    ) -> ToolResult: ...

class StateResolver(Protocol):
    def resolve(self, context: StateResolutionContext) -> StateResolution: ...

class LoopPolicy(Protocol):
    async def next(self, context: LoopContext) -> LoopDecision: ...
```

上述签名表达契约形状，不要求代码逐字相同。核心层不得导入 OpenAI/DeepSeek SDK 类型或检查其异常类。

## 2. 模型请求/响应

### CompletionRequest

```text
request_id
flow_id
turn_id
model_profile_id
messages[] / prompt_segments[]
tools[]
response_schema | null
deadline
metadata (仅稳定 ID/修订，不含正文副本)
```

### CompletionResult

```text
request_id
text | null
tool_calls[]
finish_reason
usage
provider_metadata (已过滤)
```

统一 `finish_reason`：`completed`、`tool_calls`、`length`、`content_filtered`、`temporary_failure`、`cancelled`。未知供应商值归一化为协议错误，不能泄漏到业务分支。

### ProviderError

```text
kind: authentication | balance | rate_limited | invalid_request |
      unavailable | timeout | protocol | cancelled
retryable: bool
safe_message: str
provider_request_id: str | null
```

认证、余额、无效请求不重试；限流、服务暂不可用和网络暂时错误按配置做有界重试。只有幂等、安全重放的完整请求可重试；流式中断不得把部分文本转为成功结果。

## 3. DeepSeek Adapter 映射

| Internal | DeepSeek/OpenAI-compatible |
|---|---|
| model profile | `model` 及配置参数 |
| trusted/user/data segments | `system`/`user`/`assistant`/`tool` 消息，保持信任边界 |
| ToolDefinition | `tools[].type=function` + `function.parameters` |
| ToolCall | `tool_calls[].id/name/arguments` |
| ToolResult | 相同 `tool_call_id` 的 `tool` message |
| response_schema | `response_format={"type":"json_object"}` + 人格中的 JSON 结构要求 |
| finish reason/error | 归一化领域枚举 |

DeepSeek 专属字段只在适配器内：

- `thinking`、`reasoning_effort` 和 SDK `extra_body`；
- `reasoning_content` 的回传协议；
- Beta 严格工具 Base URL；
- SSE keep-alive、usage/cache 细分字段；
- HTTP/SDK 异常映射。

`reasoning_content` 不得写入原始记录、长期记忆、普通日志或提示追踪。首版启用工具的 profile 使用非思考模式和完整响应；JSON 整理结果遇到空内容、`length` 或 Schema 错误时不写记忆，只做配置限制内的安全重试。

## 4. ToolDefinition

```text
name: str                     # [A-Za-z0-9_-]，1..64
description: str
input_schema: JSON Schema
output_schema: JSON Schema
safety:
  read_only: bool
  destructive: bool
  idempotent: bool
  open_world: bool
```

- 输入/输出 Schema 固定声明版本，object 默认 `additionalProperties=false`。
- 本地必须再次做 JSON 解析、Schema、工具存在性、启用状态、人格权限、大小和超时校验；供应商 `strict` 不能替代授权。
- Provider 只翻译定义与消息，不执行工具。

## 5. ToolExecutionContext

```text
flow_id
turn_id
persona_id
state_id
config_revision
tool_round
deadline
```

该对象只由宿主 Controller 创建，绝不能从模型 arguments 合并。模型不能通过参数伪造人格、flow、权限、轮数或 deadline。

## 6. ToolCall / ToolResult

```text
ToolCall:
  call_id
  tool_name
  arguments

ToolResult:
  call_id
  outcome
  text_content[]
  structured_content | null
  error | null
  audit_metadata
```

固定 outcome：`success`、`invalid_arguments`、`not_found`、`denied`、`timeout`、`execution_failure`。非成功结果必须有稳定错误码，不要求模型解析自然语言错误。

工具按模型返回顺序串行执行，保持首版确定性。Controller 强制配置的最大工具轮数、单工具超时、总 deadline 和结果字节上限；达到任一上限即停止，不允许模型扩大。

当前注册工具必须声明 `read_only=true`。未来写工具注册前必须具备可恢复的 `prepare/commit/rollback`，或同时声明补偿契约并实现 `compensate`；缺少能力时在调用 `execute` 前拒绝。事务型写工具只在结果 Schema/凭据/大小校验成功后 `commit`；execute、超时、非成功结果、结果校验或 commit 失败均调用 `rollback`。补偿型工具在相同失败边界调用 `compensate`，恢复失败只报告稳定 `TOOL_ROLLBACK_FAILED`，不回显参数或结果正文。

## 7. `memory_source_query`

首版唯一内置工具，所有人格获得完全相同的定义、权限路径、排序和错误语义。

### Input Schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "properties": {
    "memory_id": {"type": "string", "pattern": "^mem-[A-Za-z0-9-]+$"},
    "cursor": {"type": ["string", "null"]}
  },
  "required": ["memory_id"],
  "additionalProperties": false
}
```

不接受页大小、文件路径、SQL/正则/任意全文查询、人格 ID 或 flow ID。页大小由可信配置决定；游标是不透明、带当前 memory/index revision 绑定的值。

### Output Schema

```text
memory_id
memory_revision
index_revision
total_sources
sources[]:
  record_id
  global_sequence
  role
  text
  created_at
  state_id
  relation
  content_sha256
next_cursor | null
```

规则：

- 按 `global_sequence` 升序，ID 作为稳定并列顺序。
- 同一 revision、同一参数返回相同结果；多页无丢失/重复。
- 原文只从已确认、已通过凭据门禁且哈希有效的记录读取。
- 结果仅加入调用者当前 `flow_id` 的不可信工具上下文，不自动写长期记忆或传播给其他 flow。
- 审计记录 persona/flow/memory/返回 record ID/结果码，不复制 text。
- 调用前后原始段、长期 YAML 及来源关系哈希必须不变。

稳定错误码：`MEMORY_NOT_FOUND`、`INVALID_CURSOR`、`SOURCE_INDEX_INCONSISTENT`、`SOURCE_RECORD_MISSING`、`ACCESS_DENIED`、`RESULT_TOO_LARGE`、`INTERNAL_ERROR`。

## 8. StateResolver

### Input

`turn_id`、previous state、trigger kind、可信运行事实、config revision。用户文本、长期记忆和工具正文不得作为首版静态解析依据。

### Output

`state_id`、`ordered_persona_ids`、resolver ID/version 和 reason code。Resolver 不返回提示正文；PromptAssembler 再从同一配置快照解析人格。

首版 `StaticStateResolver` 始终返回配置的默认状态。缺失状态/人格引用时停止生成，不退化成无状态或无人格。未来模型只能建议候选状态 ID，确定性代码仍验证允许集合和转换规则。

## 9. SingleTurnController

强制顺序：

1. 获取/确认单写者锁，捕获 ConfigSnapshot 和本轮 ID。
2. 凭据门禁后持久化用户输入。
3. 解析状态和有序人格。
4. 检查短期窗口；需要时先运行记忆整理事务。
5. 组装带信任标签和预算的 PromptContext。
6. 调用 Provider。
7. 如返回工具调用，在限制内执行同一轮工具子循环并重新请求。
8. 得到完整最终输出；凭据门禁后持久化。
9. 持久化确认后才展示并返回 TurnOutcome。

失败保留已确认用户输入和可重试元数据；不生成伪响应、不修剪未整理记录、不改变既有记忆。相同 `turn_id` 的重试必须幂等。

## 10. 自主循环扩展

首版 `DisabledLoopPolicy.next()` 无条件返回 STOP，且模型/工具调用数为 0。代码中不得出现无界 `while True`。

未来 `AutonomousLoopRunner`：

- 每次 RUN 只调用一次同一 SingleTurnController，不复制 Provider、记忆、人格、工具或凭据逻辑。
- `trigger_kind=autonomous`，不能冒充用户输入。
- 宿主强制 `max_iterations`、deadline、token/成本预算和人工停止信号。
- 每轮持久化幂等 run/turn checkpoint；重启不重复已完成迭代。
- 取消时完成清理和检查点后重新抛出 `CancelledError`。

## 11. 契约测试

- 每个 Provider adapter 运行同一请求、工具、错误、取消和用量归一化套件。
- DeepSeek 响应空内容、非法 JSON、截断、多个工具调用、重复 call ID、SSE 中断和供应商未知字段。
- 无效/额外工具参数、虚构工具、未启用工具和权限伪造均在 invoke 前拒绝。
- 基础、整理、状态和两个测试辅助人格同参查询获得相同有序结果与错误。
- 来源查询分页无重复/遗漏，前后权威文件哈希不变，原文只在调用 flow 出现。
- 任意用户/记忆/工具文本不能改变 StaticStateResolver 结果。
- 替换测试 StateMachineResolver 或 Provider 无需改 Controller/Memory/PromptAssembler。
- 默认 loop 的 Provider/Tool 调用均为 0；测试 Runner 达到迭代、deadline、预算或取消必停。

## 12. 统一历史时间合同（2026-07-20）

`PromptAssembler` 把每条近期 `RawRecord` 适配为统一日志项：正文仍是原有的 `role: content`，点时间严格使用已持久化的 `created_at`，来源绑定 raw record identity/hash。`memory_overview`、`long_term_memories`、`recent_records` 三个区块分别从空状态分段。`current_input` 是第四个独立时间化区块，严格复用本轮 provisional `RawRecord.created_at`；retry/resume 不重采样。仅当前输入的时间 marker 投影为边界外的可信时间元数据，用户正文位于 `current_input` 不可信块内且仍是 `USER_INSTRUCTION`；历史 marker 保持不可信。基础人格和状态人格不进入时间线。

默认 `config/history_timestamps.toml` 使用 `Asia/Shanghai`、30 分钟 gap、120 分钟 refresh 并启用跨日。首项、`gap >= 30m`、本地日期改变、从最近 marker 起达到 120 分钟或时间倒退时，在承载项正文前生成一个固定中文 marker；多原因不会生成多行，未命中时不逐条标记。

长期记忆与 coverage overview 复用同一次 raw 快照建立的 immutable index，逐一校验 record ID/hash/sequence 后，对全部来源 `created_at` 求 UTC min/max；引用顺序、重复引用和相关性顺序不会被时间排序改变，相等端点仍保持 `SOURCE_RANGE` 标签。任何来源损坏都 fail closed。`RECORDED` 只保留为未来明确 schema/version 的扩展语义，当前 schema v1 与未知格式没有该入口。

marker 与 body 是同一 message content 下互不重叠、可逐字回读的 `RequestPart`。marker 来源同时包含 `config:history_timestamps` 与承载 raw/长期实体，信任级别固定为 `UNTRUSTED_DATA`；正文保持自身来源。overview、long-term 和 recent 字符预算检查最终含 marker 的文本，不能在预算后追加或因超限单独删除 marker。时间标记不进入 raw/YAML 存储，也不改变 `memory_source_query` 合同。

记忆整理提示中的 `batch_records`、`existing_memories`、`current_overview` 也分别从空状态调用同一分段器，并在各区块内按来源事件时间排序。三个变量采用“marker 行 + 紧凑 canonical JSON 行”：raw 仅含 `time/role/text/source_alias`，既有记忆仅含 `kind/text/status/source_time`，概览仅含 `text` 与必要覆盖范围。真实 ID/hash/revision/完整 coverage DTO 仍保留在 `RequestPart.sources`、raw index 和持久化文档中，但不进入 provider `content`。`memory_curation_v2` 只解析候选 `kind/text/sources` 与 `overview`；应用确定性解析 `rN`、附加全批 coverage 并执行原有来源/连续性校验。模板展开仍同步平移 fragment span，禁止用正文搜索推断重复 JSON 的来源。

工具历史是第八个独立时间化 block。网关仅在成功 provider attempt 已解析并接受后为整个 tool-call batch 记录一次 `accepted_at`；executor 仅在结果校验、权限/大小/安全与可恢复事务处理结束、已形成可发送 `ToolResult` 后记录一次 `completed_at`。两者均为进程内 metadata，`CompletionResult.model_dump()`、`ToolResult.model_dump()`、canonical result JSON 与 DeepSeek wire 不含这些字段。

所有历史、长期记忆、覆盖概览、整理输入和工具事件在最终标准 `content` 字段中使用共享的一对 `[UNTRUSTED block#8位ID]`/`[/UNTRUSTED block#8位ID]` 内容绑定边界；不向 DeepSeek 发送自定义 trust 字段。`chat.md`/`memory_curator.md` 与 `untrusted_memory_boundary.md` 共同组成 system content 时，各自保留当前 `ConfigSnapshot` asset/hash/revision 对应的独立 part/span。可见 wrapper 在字符预算、token 估算、TUI 和 provider materialization 前已经存在。

每次 tool continuation 都从当前轮全部未标注 `ToolHistoryEvent` 重建 assistant/tool 消息：marker 只进入对应 message content，assistant 的 tool_calls 与 tool 的 `tool_call_id`、canonical body 保持原值和原顺序。DeepSeek content 的 marker/body 使用同一 pointer 下完整、无重叠 spans；已有上游 fragments 时 adapter 不再创建 whole-content fallback，tool_calls 来源保持最初 provider response origin。`memory_source_query` 直接输入、分页、权限、错误和 JSON 返回合同不变。

# 002 模型与工具安全边界同步（2026-07-20）

所有 provider 调用统一采用 `prepare()`、唯一 `materialize_sdk_kwargs()` 与 `send_once()`；认证仍由 transport 单独注入，不属于可展示提示载荷。载荷在显示前和发送前复用凭据门禁，命中时只返回脱敏错误并沿用安全事件阻断。当前注册工具必须声明 `read_only=true`；未来写工具必须具备可恢复 `prepare/commit/rollback` 或明确补偿契约，否则在任何副作用前拒绝。

应用层只依赖 `ModelCallGateway.complete(ModelCallDraft)`；adapter 内不得重试或暴露旧 `complete(CompletionRequest)` 旁路。每次 retry 重新执行 prepare、唯一 materialization、来源/凭据校验与批准，批准令牌不能复用。TUI 与 sender 读取同一不可变 materialized payload；TUI 先清除，sender 再发送一次并在 `finally` 释放。
