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
