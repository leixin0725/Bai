# Contract: 调试显示、模型容量与估算配置

## 原则

- 所有可变参数位于 `config/`，Python 代码只定义类型和安全校验，不硬编码生产模型能力或展示阈值。
- 调试启用状态不属于配置，只由 `chat --debug-prompts` 决定。
- 配置快照必须保留每个参与文件的项目相对路径、原文摘要与统一 revision，供 SourceRef 使用。

## `config/agent.toml`

新增：

```toml
[debug_prompt]
color = "auto"                       # auto | always | never
high_context_percent = 80
critical_context_percent = 95
estimate_safety_margin_percent = 10
```

### 校验

- `color` 只能为列举值。
- `0 < high_context_percent < critical_context_percent <= 100`。
- `0 <= estimate_safety_margin_percent <= 50`。
- 未配置时可采用文档化默认值；类型错误、阈值逆序或越界必须产生含配置路径/字段但不含敏感值的错误。
- `NO_COLOR` 环境变量在 `auto` 下强制无色；配置不得让非 TTY 通过批准门禁。

## `config/providers.toml`

provider capability 与 profile request 分开维护：

```toml
[[providers]]
id = "deepseek"
adapter = "deepseek_openai_compatible"
base_url = "https://api.deepseek.com"
api_key_env = "DEEPSEEK_API_KEY"
local_concurrency = 1
max_output_cap = 384000
token_estimator = "deepseek_character_v1"

[model_profiles.chat]
provider = "deepseek"
model = "deepseek-v4-flash"
context_window_tokens = 1000000
max_output_tokens = 8192
stream = false
thinking_enabled = false
temperature = 0.7
tools_enabled = true
structured_output = false

[model_profiles.memory_curator]
provider = "deepseek"
model = "deepseek-v4-flash"
context_window_tokens = 1000000
max_output_tokens = 8192
stream = false
thinking_enabled = false
tools_enabled = true
structured_output = true
output_schema = "memory_curation_v1"
```

数值以 2026-07-20 的 [DeepSeek 官方模型说明](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/) 为当前依据：V4 Flash 能力为 1M context/384K 最大输出，`deepseek-chat` alias 将弃用。迁移只替换两个 profile 的 model id 并补充能力元数据；`thinking_enabled=false`、`max_output_tokens=8192` 及各 profile 现有 temperature、tools、structured-output 等生成参数必须逐字段保持不变。后续模型升级只改配置和相应文档/测试夹具。

### 校验

- `context_window_tokens` 可缺失；缺失时 TUI 显示容量/比例/剩余未知，不从 `context_budget` 或其他模型推断。
- 已配置时必须为正整数。
- `max_output_tokens` 必须为正整数且不超过 provider `max_output_cap`；不得静默截断。
- `max_output_tokens <= context_window_tokens`（容量已知时）。
- `chat` 与 `memory_curator` 必须使用 `deepseek-v4-flash` 且 `thinking_enabled=false`；迁移合同测试必须证明除 model id 和新增能力字段外的既有生成参数差异为 0。
- `token_estimator` 必须在 estimator registry 注册；未知 estimator 使配置校验失败，而非回退到无依据算法。
- profile/model 与 estimator 不兼容时可将估算标记 unavailable，但 provider capability 配置自身仍须明确。

## `context_budget` 的边界

现有：

```toml
[context_budget]
max_input_tokens = 65536
trusted_instruction_tokens = 8192
long_term_tokens = 16384
short_term_tokens = 32768
tool_result_tokens = 4096
```

这些值只控制 Bai Agent 选择和组装输入的本地预算。它们：

- 不等于 provider tokenizer 的最终输入 token；
- 不代表模型 context window；
- 不包含当前请求的最大输出预留；
- 不得用于伪造缺失的模型容量。

## 配置资产身份

loader 对 `agent.toml`、`providers.toml`、`states.toml`、`tools.toml` 及其引用的 persona/prompt 文件生成 `ConfigAsset`：

- 读取时使用的项目相对路径；
- UTF-8 原文和 SHA-256；
- 原有 snapshot revision；
- asset kind/id。

prompt assembler 必须从 asset 传递来源，不能只接收裸字符串。配置在请求构建后被修改时，TUI 仍显示本次 snapshot 的 path/hash/revision，不重新读取并冒充旧来源。

## DeepSeek token estimator 配置语义

`deepseek_character_v1`：

- 对最终 `messages`、`tools` 和模型可见 JSON 字段整体估算；
- 使用 DeepSeek 官方文档给出的英文/中文字符近似作为基础，并计算 JSON、Unicode、工具 schema 与协议开销；
- 应用 `estimate_safety_margin_percent`；
- 输出 method/version、`conservative` confidence 和 `≈` 标签；
- 通过前缀累计边际分配保证 part + protocol overhead 等于总输入估算。

如果载荷含该 estimator 不支持的结构，整次输入估算返回 unavailable(reason)，不输出精确数字或部分总数。

## 文档和兼容性

- README/quickstart 必须说明模型能力数字来自配置而非实时 API 探测。
- 从 `deepseek-chat` alias 迁移到 `deepseek-v4-flash` 时，更新现有 001 配置合同和运行指南；不得仅改生产 TOML。
- 新 provider 必须提供 capability 配置、estimator 或明确 unavailable 行为，以及 `prepare()`、唯一 `materialize_sdk_kwargs()`、`send_once()` adapter 合同测试。
- 主要兼容矩阵为 Ubuntu 24.04/Python 3.13 与 3.14；Windows runner 只承担次要功能兼容，macOS 不在本功能范围内；500 ms 性能门禁只在 Ubuntu 24.04/Python 3.13 执行。
