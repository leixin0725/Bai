# Contract: 统一模型调用与提示来源追踪

## 目标

定义所有聊天、记忆整理、工具续接、重试和未来辅助人格调用必须遵守的唯一出站边界，保证最终请求可观察、可批准、可证明与实际发送一致。

## 端口

以下为语义契约，具体 Python 命名可在不改变职责的前提下细化。

```python
class ProviderAdapter(Protocol):
    def prepare(self, draft: ModelCallDraft, attempt: int) -> PreparedProviderRequest: ...
    def materialize_sdk_kwargs(
        self,
        request: PreparedProviderRequest,
    ) -> MaterializedSendPayload: ...
    def send_once(self, payload: MaterializedSendPayload) -> CompletionResult: ...

class ApprovalPresenter(Protocol):
    def decide(
        self,
        request: PreparedProviderRequest,
        estimate: ContextUsageEstimate,
        warning: PrivacyWarning,
    ) -> ApprovalDecision: ...

class TokenEstimator(Protocol):
    def estimate(
        self,
        request: PreparedProviderRequest,
    ) -> ContextUsageEstimate: ...

class ModelCallGateway(Protocol):
    def complete(self, draft: ModelCallDraft) -> CompletionResult: ...
```

应用服务只能依赖 `ModelCallGateway.complete()`；不得持有 provider SDK client，也不得直接调用 `prepare()`、`materialize_sdk_kwargs()` 或 `send_once()`。

## 网关处理顺序

对每个物理 attempt 严格执行：

1. 由 adapter 从 draft 生成深度不可变 `PreparedProviderRequest`。
2. 对该 attempt 恰好调用一次 `materialize_sdk_kwargs()`，得到无认证的深度不可变 `MaterializedSendPayload`；任何后续层不得再次生成 SDK kwargs。
3. 以 materialized `sdk_kwargs` 校验 JSON 类型、最终 pointer/span、included part 覆盖、来源完整性和调用顺序。
4. 对 materialized 载荷执行凭据检测；命中则报告脱敏安全事件并终止。
5. 基于 materialized 载荷计算上下文估算；不可估算是可显示状态，不绕过批准。
6. 调试开启时，由 TUI 完整呈现 prepared 来源和同一个 materialized payload；全部内容 mounted/validated 后才允许显式 approve/reject。
7. reject：不调用 sender，抛出领域级 `TurnRejected`，由 controller 从 PREPARED 丢弃整轮且不得形成 pending。
8. approve：生成绑定 call/attempt/materialized digest 的 token；在网络发送前关闭并清除 TUI，释放 prepared、part、SourceRef 和 renderable 引用。
9. sender 只接管同一个 `MaterializedSendPayload`；发送前再次执行凭据检测并重算 digest，任一不匹配则阻断。
10. `send_once()` 使用 payload 内的同一 `sdk_kwargs` 发出一次物理请求；认证只在 client 层单独注入，且无论成功或失败都在 `finally` 释放 sender 对 payload 的引用。
11. 成功返回 response 和可选实际 usage；适配器关闭 SDK 内部重试并按配置分类失败。只有网络连接/超时与 HTTP 429/500/503 可重试；400/401/402/403/422 及未知本地异常立即失败。可重试时 attempt + 1 并从步骤 1 开始，不能复用批准或恢复上一 TUI；重试结束的普通 provider/网络失败由 controller 将轮次转为 READY_PENDING 并只发布一条 USER pending。
12. DeepSeek V4 的每个物理请求显式物化 `extra_body.thinking.type=disabled`；工具续接在 tool result 前回放 assistant/tool_calls，不保存或要求 `reasoning_content`。

## 最终请求边界

`materialize_sdk_kwargs()` 生成的 `sdk_kwargs` 必须包含 SDK chat-completion 方法实际接收的全部模型可见字段，例如：

- `model`
- 有序 `messages`（role、content、tool_calls、tool_call_id 等实际存在字段）
- `tools` 与 tool schema
- `tool_choice`
- `max_tokens`/当前 SDK 对应字段
- temperature、structured output 等会改变模型行为的字段

以下内容必须排除并保持在 sender/client 层：

- API Key、Authorization header、cookie、proxy credential
- HTTP client、timeout 对象、连接池
- 仅传输相关且模型不可见的 header
- 来源元数据、TUI 样式、approval token

## 来源完整性

- 参与请求的每个内容 part 都必须有可解析 JSON Pointer；字符串正文还必须有不重叠或有文档解释的 span。
- 同一载荷字段由多段组成时，part 顺序必须与拼接顺序一致；分隔符若由 adapter 生成，作为 `generated` 来源显式记录。
- messages 的 role、工具 schema 等非自由文本但影响上下文的字段必须有 config/runtime/generated 来源。
- 相同正文不能据此合并来源；只使用构建时记录的真实加载和选择关系。
- `unknown_source`、pointer 失效、span 不匹配、included 内容遗漏或来源数量被截断都使该 attempt 不可发送。

## 准批一致性

- `materialize_sdk_kwargs()` 是唯一 SDK 参数生成边界；TUI、摘要器和 sender 不得分别序列化业务对象。
- approve token 必须包含当前 call、attempt 和 materialized canonical digest；sender 只接受全部匹配的 token。
- canonicalization 只用于摘要，不得把规范 JSON 反向作为发送内容；SDK 参数原类型与数组顺序保持不变。
- debug-on/off 对同一 draft 的 `MaterializedSendPayload.sdk_kwargs` 深度相等。

## 错误语义

| Error | Result |
|---|---|
| `TraceIntegrityError` | 发送次数 0；当前轮失败关闭，可返回输入界面或按 controller 策略退出 |
| `CredentialExposureError` | 显示前阻断；脱敏事件；发送次数 0 |
| `DebugPresentationError` | 不自动批准；发送次数 0 |
| `TurnRejected` | 当前 attempt 发送次数 0；触发整轮回滚 |
| network/timeout 或 HTTP 429/500/503 | 当前 attempt 已发送；下一 attempt 重新展示与批准 |
| HTTP 400/401/402/403/422 或未知本地异常 | 当前 attempt 已发送；脱敏失败且不再展示同一调用 |
| retry exhausted / non-retryable provider / network error | 不再构建后续 attempt；事务转 `READY_PENDING`，幂等发布且只发布一条 USER pending；只有显式 `--resume-pending` 重发，默认启动或 `--discard-pending` 放弃该尾部 pending |

## 实际用量

- provider 返回的合法 `prompt_tokens`、`completion_tokens`、`total_tokens` 映射为 `ActualUsageSummary`。
- gateway 可把发送前数值复制到 usage summary 以计算误差，但 summary 不得持有 prompt、prepared/materialized request、part、SourceRef、widget 或 renderable。
- provider 未返回或返回负数/不守恒用量时标为 unavailable，不用估算值冒充实际值。
- prompt TUI 已在 approve 后关闭；实际用量只由普通聊天输出显示，不能重新打开或重建已发送 trace。

## 合同测试

1. 捕获 `prepare()` 结果、唯一 `materialize_sdk_kwargs()` 输出、SDK kwargs 与 mock HTTP JSON，逐字段对比 messages/tools/order/content，并断言每 attempt materializer 只调用一次。
2. 覆盖空字符串、多行、简体中文、Emoji、控制字符和超长内容。
3. 每个 chat/curation/tool/retry attempt 恰有一个批准项；未批准发送为 0。
4. 修改嵌套 payload 后旧 approval token 必须失效。
5. unknown source、坏 pointer/span、凭据命中和 TUI failure 均 fail closed。
6. debug-on/off prepared payload 相等；批准本身不改变 draft 或 payload。
7. 新 provider fake 只实现 `prepare()`、`materialize_sdk_kwargs()`、`send_once()` 三个 adapter 方法即可复用完整网关语义。
8. provider/网络普通失败只形成一条可恢复 USER pending；明确 reject 不形成 pending。
9. approve 后 presenter 立即释放正文/来源，`send_once` 成功和失败路径均释放 sender payload，actual usage 不恢复原文。
