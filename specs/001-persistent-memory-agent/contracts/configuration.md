# Contract: 配置与提示词

## 1. 配置根

CLI 必须通过 `--config-dir` 选择一整套配置；未指定时使用项目约定的 `config/`。首版不做多层合并、用户覆盖或任意环境变量插值。`agent.toml` 是入口清单，所有引用必须显式存在。

```text
config/
├── agent.toml
├── providers.toml
├── states.toml
├── tools.toml
├── logging.toml
├── personas/
│   ├── chat.md
│   ├── memory_curator.md
│   └── states/default.md
└── prompts/
    ├── chat_context.md
    ├── memory_curation.md
    └── untrusted_memory_boundary.md
```

配置和提示文件可进入 Git；实际密钥、`data/`、本地运行日志和临时文件不可进入 Git。

## 2. `agent.toml`

必须声明而不得由 Python 行为默认值替代的字段：

```toml
schema_version = 1
agent_id = "bai"
data_root = "../data"

[personas]
chat = "personas/chat.md"
memory_curator = "personas/memory_curator.md"

[prompts]
chat_context = "prompts/chat_context.md"
memory_curation = "prompts/memory_curation.md"
untrusted_memory_boundary = "prompts/untrusted_memory_boundary.md"

[archive]
segment_max_records = 256
segment_max_bytes = 1048576
max_record_bytes = 262144

[short_term]
max_records = 48
reserved_records = 8
curation_batch_min_records = 8
curation_batch_max_records = 24

[context_budget]
max_input_tokens = 65536
trusted_instruction_tokens = 8192
long_term_tokens = 16384
short_term_tokens = 32768
tool_result_tokens = 4096

[memory_overview]
max_chars = 12000
max_spans = 2048

[manual_memory]
max_document_bytes = 8388608
max_items = 10000
reload_between_turns = true

[autonomous_loop]
enabled = false
policy = "disabled"
max_iterations = 0

[runtime]
writer_lock_timeout_seconds = 0
tool_deadline_seconds = 20
max_tool_rounds = 4
```

数值仅为首版示例配置，不构成代码默认值。缺少任何必需值、类型错误、相互矛盾或越界时必须拒绝启动；例如 `reserved_records >= max_records`、批次上限小于下限、各段预算之和超过总预算均无效。

`data_root` 是唯一允许指向配置根之外的运行数据路径；它必须规范化为本地文件系统绝对路径，不能是配置根、仓库根或文件系统根，且首版对网络共享给出拒绝/明确警告。人格和提示引用必须始终位于配置根内。

## 3. `providers.toml`

```toml
schema_version = 1

[[providers]]
id = "deepseek"
adapter = "deepseek_openai_compatible"
base_url = "https://api.deepseek.com"
api_key_env = "DEEPSEEK_API_KEY"
local_concurrency = 1

[providers.timeout]
connect_seconds = 10
read_seconds = 120
total_seconds = 150

[providers.retry]
max_attempts = 3
initial_backoff_seconds = 1
max_backoff_seconds = 8
jitter = true
retryable_statuses = [429, 500, 503]

[model_profiles.chat]
provider = "deepseek"
model = "deepseek-v4-flash"
stream = false
thinking_enabled = false
max_output_tokens = 8192
temperature = 0.7
tools_enabled = true
structured_output = false

[model_profiles.memory_curator]
provider = "deepseek"
model = "deepseek-v4-flash"
stream = false
thinking_enabled = false
max_output_tokens = 8192
tools_enabled = true
structured_output = true
output_schema = "memory_curation_v1"
```

约束：

- `api_key_env` 只保存环境变量名；值从进程环境或未来秘密存储读取。
- 不能支持会把任意环境变量展开进提示词/普通配置的 `${ENV}` 机制。
- 模型 profile 可被不同人格独立引用；模型名、URL 和能力会变化，代码不得枚举当前模型。
- 参数必须先与声明能力核对；例如思考模式下无效的采样参数不得静默发送。
- 首版 Assistant 结果默认完整接收、持久化后展示。即使未来 profile 启用流传输，UI 也不能在完整记录确认前展示未持久化 token。

## 4. `states.toml`

```toml
schema_version = 1
default_state_id = "default"
resolver = "static"

[[personas]]
id = "state_default"
role = "state"
prompt = "personas/states/default.md"
model_profile = "chat"

[[states]]
id = "default"
enabled = true
ordered_persona_ids = ["state_default"]
```

基础聊天人格和记忆整理人格由 `agent.toml` 指定；`states.toml` 只定义状态附加人格。缺失、空白、重复或越界引用必须拒绝启动。首版解析器不从用户文本、记忆或模型输出推断状态。

## 5. `tools.toml`

```toml
schema_version = 1

[[tools]]
id = "memory_source_query"
enabled = true
implementation = "builtin.memory_source_query"
allowed_personas = ["*"]
read_only = true
destructive = false
idempotent = true
open_world = false
page_size = 16
max_result_bytes = 131072

[future_tools]
default_enabled = false
```

首版唯一可启用实现是 `memory_source_query`。所有未来工具即使有配置条目也必须显式启用、被注册表识别并通过权限/Schema 校验；未知实现不得动态导入执行。

## 6. `logging.toml`

必须提供日志级别、输出位置、轮换策略、允许字段和脱敏策略。以下字段禁止进入普通日志：

- Authorization/API Key 和环境变量值；
- 用户/Agent/长期记忆正文；
- 提示正文和工具 arguments/result 正文；
- DeepSeek `reasoning_content`；
- 本地绝对数据路径（错误中仅显示逻辑路径或脱敏形式）。

允许记录稳定 ID、哈希、计数、时长、规范化错误码、Provider/model profile ID 和 token 用量。

## 7. 提示文件契约

- Markdown 只保存提示正文；职责、模型参数、权限和阈值留在 TOML。
- `chat.md`、`memory_curator.md` 和每个状态人格为独立文件，统一由同一加载器处理。
- 记忆整理人格必须明确要求一次完整 JSON 响应同时给出长期记忆候选与 `overview_update`，并声明只处理当前批次；不得通过 `memory_overview.md`、第二次模型调用或第二事实来源生成概览。
- 所有运行时插槽使用 `string.Template` 标识符；实际标识符必须与入口清单允许集合完全一致。
- 不使用 `safe_substitute()`；缺失、额外或畸形变量均 fail closed。
- 历史记录、长期记忆、工具结果只能填入标记为 `untrusted_data` 的插槽，不得进入可信指令插槽。
- 文件不能为空，必须在大小限制内；UTF-8/BOM、换行和哈希规则保持确定性。

## 8. 加载与热重载

1. 读取全部 TOML，校验 Schema 版本和引用图。
2. 解析路径并检查越界、文件类型、大小和编码。
3. 加载提示，核对模板变量和非空约束。
4. 校验 Provider、模型、人格、状态、工具和循环的交叉引用。
5. 只读取被引用秘密的存在性，不把值放入 ConfigSnapshot。
6. 对非秘密配置和提示内容计算 `config_revision`。

每一轮只使用一个不可变 ConfigSnapshot。文件在轮次中改变时不影响当前轮；下一轮重新验证成功后才原子替换快照。验证失败保留最近有效快照并明确告警，但若涉及凭据、人格缺失或安全策略则停止生成，不静默降级。

## 9. 配置契约测试

- 所有必填字段缺失、类型错误、边界值和交叉字段冲突。
- 人格/提示文件缺失、空白、过大、非法 UTF-8、模板变量缺失/额外。
- `..`、绝对路径、符号链接导致的配置根逃逸。
- Provider/model/persona/state/tool 的缺失和重复引用。
- 配置中出现疑似真实凭据或 `api_key_env` 指向不存在变量。
- 同一轮修改文件不改变 revision，下一轮才采用新快照。
- 源码扫描证明没有人格正文、模型名、URL、行为阈值、超时或采样值。
# 002 调试扩展同步（2026-07-20）

配置快照现在为 agent、provider、tool、state、persona 与 prompt 文件保留项目相对路径、UTF-8 SHA-256 和统一 revision。`agent.toml` 的 `[debug_prompt]` 只维护 `color`、上下文风险阈值与估算安全裕度；调试启用状态仍只来自 `chat --debug-prompts`，不得落入配置。Provider 能力单独声明 context window、output cap 与 estimator，非法上限必须报错，不能用本地 `context_budget` 冒充模型容量。

截至 2026-07-20，chat 与 memory curator 均为 `deepseek-v4-flash`、`thinking_enabled=false`、`max_output_tokens=8192`；chat 的 temperature/tools/structured-output 以及 curator 的 tools/structured-output/output schema 保持原值。DeepSeek 能力元数据为 1,000,000 context、384,000 output cap 与 `deepseek_character_v1`。该 estimator 对唯一物化的 messages/tools 整体估算，应用安全裕度并以前缀边际/协议开销保证分段守恒；不兼容结构返回 unavailable，不回退到字符数除四。
