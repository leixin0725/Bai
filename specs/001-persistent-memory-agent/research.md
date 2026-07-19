# Phase 0 Research: 持久记忆聊天 Agent

**Date**: 2026-07-19
**Scope**: Python 技术栈、DeepSeek 接入、配置、文件持久化、提示边界及扩展契约

## 1. Python 与应用形态

**Decision**: 以 Python 3.13 为最低/主验证版本，并在 Python 3.14 上做兼容测试；首版交付可复用包和 `python -m bai_agent` CLI，不建设 Web 服务。

**Rationale**: Python 3.13 仍处于官方维护周期，第三方依赖成熟；CLI 已足以验证连续记忆、人格配置、模型调用和恢复行为。Python 官方版本状态可在 [Developer's Guide](https://devguide.python.org/versions/) 核对。

**Alternatives considered**:

- Python 3.14 only：可使用最新语言版本，但会无必要地缩小依赖兼容范围。
- Web/API 服务：当前没有多用户、远程访问或图形界面需求，会增加部署和权限面。

## 2. 内部稳定契约与边界适配器

**Decision**: 核心使用 `typing.Protocol` 和冻结 dataclass DTO 定义记忆仓库、模型供应商、工具、状态解析及运行控制契约。跨边界 DTO 可序列化为 JSON；供应商 SDK 类型、异常和字段不得进入核心层。`pydantic` 只用于 TOML/供应商响应/整理结果/工具参数等外部边界的解析、约束和 JSON Schema，不作为领域服务框架。

**Rationale**: Python 的 [Protocol](https://docs.python.org/3/library/typing.html#typing.Protocol) 支持结构化子类型；[dataclasses](https://docs.python.org/3/library/dataclasses.html) 可表达冻结、紧凑的值对象；Pydantic 官方支持从模型生成 [JSON Schema](https://docs.pydantic.dev/latest/concepts/json_schema/)。这能以较少代码隔离变化，又不必引入依赖注入框架。

**Alternatives considered**:

- 在全项目直接使用 OpenAI SDK 对象：代码起步快，但会把供应商细节扩散到记忆和工具层。
- 大型多供应商/Agent 框架：首版只有一个供应商和一个内置工具，抽象和升级成本超过收益。
- 无类型 `dict`：表面简单，但错误只能运行时发现且契约难以测试。

## 3. DeepSeek Provider

**Decision**: 定义项目自己的 `ModelProvider`；首个 `DeepSeekProvider` 在适配器内部使用 OpenAI Python SDK访问 `https://api.deepseek.com` 的 Chat Completions。模型名和全部能力/参数来自 `providers.toml`，示例配置采用当前正式模型 `deepseek-v4-flash`，不得在 Python 中固化。

**Rationale**: DeepSeek 官方提供 OpenAI 格式的[快速开始](https://api-docs.deepseek.com/)；但思考模式、`reasoning_content`、Beta 严格工具 URL 和部分参数语义仍属供应商差异。适配器负责转换统一请求/响应、流事件、结束原因、用量与错误。当前模型和能力以官方[模型与价格页](https://api-docs.deepseek.com/quick_start/pricing/)为准，配置化可避免模型升级时改业务代码。

首版行为：

- 首版聊天使用完整响应，确认持久化后才展示；适配器保留流事件契约，但即使未来启用流传输，也不得在完整记录确认前展示未持久化 token。
- 记忆整理采用非流式 JSON Output，完整解析、Schema 校验后才提交。
- 启用工具的聊天默认关闭思考模式；未来支持“思考 + 工具”时，仅适配器按官方要求回传 `reasoning_content`，且该内容不得进入记忆或普通日志。参见 [DeepSeek Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)。
- 400/401/402/422 不自动重试；429、500、503、网络暂时错误和 `insufficient_system_resource` 按配置做有界退避；流中断按整次失败处理。错误语义依据官方[错误码](https://api-docs.deepseek.com/quick_start/error_codes/)。

**Alternatives considered**:

- 直接用 HTTP 客户端实现 SSE 与协议：减少一个 SDK 依赖，却增加协议和流式解析代码，不符合最简单实现。
- 让 OpenAI 兼容配置直接贯穿核心：无法正确隔离思考字段和供应商错误。

## 4. 配置与提示词组织

**Decision**: `config/agent.toml` 是唯一入口清单，引用 `providers.toml`、`states.toml`、`tools.toml`、日志策略、独立人格 Markdown 和提示模板 Markdown。TOML 使用标准库 `tomllib` 只读加载；模板使用 `string.Template.substitute()` 严格替换。每轮捕获不可变 `ConfigSnapshot`，只在轮次间重新加载。

**Rationale**: [tomllib](https://docs.python.org/3/library/tomllib.html) 无写入接口，适合程序只读、人工维护的配置。`string.Template` 的标识符可预先比对允许变量；不能使用会静默保留缺失占位符的 `safe_substitute()`，相关行为见 [template strings](https://docs.python.org/3/library/string.html#template-strings)。

强制约束：

- 模型名、Base URL、环境变量名、采样参数、上下文预算、窗口/批次、超时、重试、分页、工具轮数、状态和扩展开关全部配置化。
- 相对引用规范化后必须仍在配置根下，拒绝 `..`、绝对路径和符号链接逃逸。
- 提示变量集合必须与清单完全一致；缺失、额外、空文件、超大文件和无效引用均 fail closed。
- 每轮只记录配置修订号和提示文件哈希，不记录提示正文或凭据。

**Alternatives considered**:

- Python/YAML 保存全部配置：TOML 更适合简单参数且无需执行代码；人格长文本仍使用 Markdown。
- 环境变量保存全部参数：不利于版本化和人工维护，仅秘密值适合外部环境。

## 5. 原始记录存储

**Decision**: 原始用户/Agent 记录以全局递增序号组织为有界 JSONL 分段。当前小段每次通过同目录临时文件写全、flush、`os.fsync()`、`os.replace()` 原子替换；达到配置上限后封存为不可变段并创建下一段。启动时校验段号、记录序号、内容哈希和尾段完整性。

**Rationale**: 分段兼顾永久明文、人工读取、10,000 条规模加载和故障隔离；只重写受限尾段，避免对无限增长文件做全量替换。Python 的 [`os.fsync`](https://docs.python.org/3/library/os.html#os.fsync) 与 [`os.replace`](https://docs.python.org/3/library/os.html#os.replace) 提供所需的持久刷新和同文件系统原子替换基础。

**Alternatives considered**:

- 单个无限 JSONL 直接追加：断电可能留下半行，恢复和确认语义更复杂。
- 每条记录一个文件：原子性直观，但大目录扫描和人工管理较差。
- SQLite：事务可靠，但长期记忆必须直接人工管理，首版会形成两种存储和额外迁移边界。

## 6. 长期记忆、来源与修剪前沿

**Decision**: `long_term.yaml` 是长期记忆的单一事实来源。每个长期记忆项内嵌一个或多个 `source_record_ids`；文档根同时保存 `curated_through_sequence`。内容、全部来源关系和新的修剪前沿在同一次临时写入 + fsync + replace 中共同成功或失败。提交前逐一验证来源记录存在；原始记录永不删除。

**Rationale**: 把正文、来源索引和“可从直接注入窗口移出”的前沿放在同一文档，避免跨文件事务和无来源记忆。YAML 易于人工编辑；`ruamel.yaml` 的[往返模式](https://yaml.dev/doc/ruamel.yaml/overview/)尽量保留注释、键顺序和块样式。每次成功加载后更新 `long_term.last-valid.yaml`；若人工编辑无效，保留错误文件并只读回退到最近有效副本，不自动覆盖人工内容。

**Alternatives considered**:

- 独立来源索引文件：需要处理记忆正文与索引的跨文件原子一致性。
- 自动删除已整理原始记录：违反永久归档和按需原文查询要求。
- PyYAML：能读写 YAML，但程序更新会更容易抹去人工注释和格式。

## 7. 单写者和崩溃恢复

**Decision**: 程序启动时取得进程级 `.state/writer.lock`，超时后明确失败；每项文件提交仍使用原子替换。锁只保证单写者，原子写/校验/有效副本负责崩溃恢复。锁超时由配置提供，`filelock` 的行为见官方[超时指南](https://py-filelock.readthedocs.io/en/stable/how-to.html#handle-lock-timeouts)。

关键顺序：

1. 用户输入在模型调用前确认写入。
2. 若下一轮会使一批未整理记录离开直接注入窗口，先调用记忆整理人格。
3. 仅在长期记忆、全部来源和新前沿原子提交成功后，提示选择器才采用新前沿。
4. 任一整理/校验/写入失败时前沿不变；本轮停止生成，保留待整理原文以便重试。
5. 最终 Agent 输出在展示前确认写入；流式内容先缓存在内存，完整成功后持久化并展示。

**Alternatives considered**:

- 仅靠“单进程假设”而不加锁：重复启动可能破坏序号和尾段。
- 在模型调用期间允许并行写：首版无并发需求，增加快照冲突和幂等复杂度。

## 8. 提示上下文与不可信数据边界

**Decision**: `PromptAssembler` 以固定顺序构造：安全策略/基础人格、状态人格、长期记忆摘要/选中项、短期原文、工具定义、当前输入。只有配置根中已校验的文件可以形成可信指令；历史记录、长期记忆、整理结果和工具结果始终标记为不可信数据，不能提升为 system 指令。

**Rationale**: 这是从记忆派生文本进入提示时的确定性安全边界。OWASP 的[提示注入防护指南](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)建议结构化分隔、最小权限、输入/输出验证和审计。本项目不把模型检测当作授权机制；即使不可信文本诱导成功，确定性代码仍限制工具、状态、写入和循环。

**Alternatives considered**:

- 把长期记忆拼进 system prompt：会把历史对话中的恶意文本升级为指令。
- 每轮逐字注入全部历史：超出上下文后不可持续，且违背按需来源查询设计。

## 9. 内部工具协议和来源查询

**Decision**: 内部工具采用 Provider-neutral JSON Schema function contract：`ToolDefinition`、`ToolCall`、宿主创建的 `ToolExecutionContext` 与结构化 `ToolResult`。首版只注册 `memory_source_query`，所有人格经同一个 `ToolRegistry`、权限判断、参数/结果 Schema 和错误码调用。

**Rationale**: DeepSeek 当前的[工具调用](https://api-docs.deepseek.com/guides/tool_calls/)以 JSON Schema 描述函数，但官方明确要求调用方验证模型生成的参数。内部契约也可映射到未来 MCP 的 [`inputSchema`/`outputSchema`](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)，无需首版部署 MCP Server。

来源查询按长期记忆 ID 和不透明游标分页，按全局序号稳定返回原始记录；页大小由可信配置决定。工具不接受文件路径、任意查询或人格 ID，不提供写端口。返回原文只进入当前 `flow_id`，审计仅记录调用人格、流程、记忆 ID、返回记录 ID 和结果码，不复制正文。

**Alternatives considered**:

- 首版直接采用 MCP 作为进程内总线：唯一内置工具不需要额外传输和生命周期复杂度。
- 每个人格实现独立查询逻辑：会产生权限、排序和错误语义旁路。

## 10. 状态和自主循环扩展

**Decision**: `StaticStateResolver` 从 `states.toml` 返回 `default` 及有序人格 ID；解析器不读取用户文本。`SingleTurnController` 执行一个用户轮次和配置限制内的工具子循环。`DisabledLoopPolicy` 始终停止；未来自主 Runner 只能重复调用同一 Controller，并受最大迭代、deadline、token/成本预算、取消和检查点约束。

**Rationale**: 首版最小实现能实际验证扩展接口，且不会提前实现完整状态机或无人值守调度。未来状态解析器只能返回已配置 ID，提示仍由统一加载器解析；未来循环不能复制或绕开记忆、人格、工具和安全门禁。取消处理遵循 Python 的 [`asyncio` cancellation](https://docs.python.org/3/library/asyncio-task.html#task-cancellation)，清理后重新抛出 `CancelledError`。

**Alternatives considered**:

- Controller 中硬编码 `default`：违反配置化并把状态扩展散入业务流程。
- 现在引入状态机/任务调度框架：没有首版业务状态或自主任务，不必要。

## 11. 测试策略

**Decision**: 核心业务按三层验证：纯领域单元/属性测试、端口契约测试、文件与模型替身的集成/故障注入测试。性能测试使用 10,000 条原始记录和 1,000 条长期记忆；凭据测试覆盖写入、日志、工具结果和提交扫描。

关键门禁包括：

- 所有 Provider 适配器运行同一契约套件，SDK 异常必须归一化。
- 在每个写入点注入中断，恢复后无半条记录、无无来源长期记忆、无提前前沿。
- `memory_source_query` 调用前后原始段和长期记忆哈希不变；所有人格同参得到相同顺序/错误。
- 无效 JSON、额外工具参数、虚构工具、重复 call ID 在执行前拒绝。
- 默认自主循环的模型/工具调用次数均为 0；测试实现可在不改记忆核心的情况下替换状态解析器和控制器。
- 直接、间接和持久记忆提示注入样例不能扩大权限、写文件或开启无限循环。

**Alternatives considered**:

- 只做端到端模型测试：外部服务非确定且无法精确覆盖崩溃点。
- 只做单元测试：无法证明真实文件替换、重启恢复和配置引用边界。
