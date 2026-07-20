# Bai Agent

Bai Agent 是一个 Python 单用户聊天 Agent。它不创建或切换“对话”：每次启动都从同一份永久原始记录、近期直接上下文和长期记忆继续工作。

当前实现包括：

- DeepSeek API 适配器，以及可替换的模型 Provider 接口；
- 永久保存完整轮次并允许安全放弃唯一未完成尾部 USER 的分段 JSONL、可直接编辑的长期 YAML 和同修订版 `MemoryCoverageOverview`；
- 独立管理的聊天人格、记忆整理人格、状态人格和提示模板；
- 在近期窗口边界运行的批量记忆整理，长期记忆保留一个或多个原始来源；
- 所有人格共用的只读来源查询工具；
- 可替换状态解析器、工具注册器和默认禁用的自主循环边界；
- 原子写入、单写者锁、明文文件权限检查、凭据阻断和泄露事件门禁。

## 安装

需要 Python 3.13 或 3.14：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

所有提示词和可变参数都在 `config/`。DeepSeek 凭据只通过外部秘密管理器或进程环境变量 `DEEPSEEK_API_KEY` 注入，不要写入配置、命令历史、人格或记忆文件。

```powershell
python -m bai_agent config validate --config-dir config
python -m bai_agent --config-dir config --data-dir data doctor
python -m bai_agent --config-dir config --data-dir data chat
```

普通启动发现合法尾部 USER pending 时，会只丢弃该未完成轮次并直接等待新输入；不会重发旧正文，也不会删除此前完整聊天或长期记忆。只有确认重试同一轮时显式执行：

```powershell
python -m bai_agent --config-dir config --data-dir data chat --resume-pending
```

也可显式表达丢弃意图；没有 pending 时三种启动方式都安全进入新输入：

```powershell
python -m bai_agent --config-dir config --data-dir data chat --discard-pending
.\start.ps1 -ResumePending
.\start.ps1 -DiscardPending -DebugPrompts
```

resume 与 discard 参数互斥；`--resume-pending` 是唯一允许重发旧 pending 内容的方式。丢弃只允许原子截去 raw archive 最末尾的未配对 USER，完整 USER/ASSISTANT 轮次继续永久不可删除。

## 提示词调试批准

仅在本地交互式 TTY 中按当前进程启用：

```bash
python -m bai_agent --config-dir config --data-dir data chat --debug-prompts
```

每个聊天、记忆整理、工具续接与 provider retry 都通过唯一 `ModelCallGateway`，在 DeepSeek `prepare()` 和唯一 `materialize_sdk_kwargs()` 后展示最终模型可见字段、完整正文及 `[config_file]`、`[data_file]`、`[runtime]`、`[generated]` 来源。界面会提醒私人记忆只在本机临时显示；按 `C` 或点击“复制框内全部内容”可复制最终载荷、提示片段和来源，且不会作出批准决定；按 `A` 逐请求批准，按 `R`/`Esc` 明确拒绝并无痕撤销整轮。批准绑定 call、attempt 与物化载荷摘要，不修改请求；批准后、网络发送前 TUI 清除正文和来源，`send_once()` 无论成功或失败都在 `finally` 释放 sender 载荷。

明确拒绝不会留下历史或 pending；恢复旧 pending 时发生 R/Esc/拒绝按钮/Ctrl+C，也会安全删除 raw 尾部的该 pending。已经批准的请求若因普通 provider/网络错误且重试结束，则只发布一条 USER pending；只有 `--resume-pending` 恢复，下一次普通启动或 `--discard-pending` 会丢弃并继续新对话。该模式默认关闭、退出即失效，也不会保存原始追踪。stdin/stdout 任一不是 TTY 时以 `DEBUG_TTY_REQUIRED`/exit 2 在任何持久化和模型发送前失败；Textual application-mode 预检同样先于应用构建。TUI 中 `Ctrl+C` 先按拒绝路径撤销再以 130 退出，EOF/终端丢失绝不批准并以脱敏 presentation failure 阻断。

同一轮的 memory curation → chat → tool continuation 按网关分配的严格 call sequence 逐项出现；tool continuation 会先回放 assistant/tool_calls，再追加匹配的 tool result。只有网络/超时和 HTTP 429/500/503 会形成新的 retry attempt 与批准项；400/401/402/403/422 等不可重试错误只审批/发送一次并立即返回脱敏错误，OpenAI SDK 内部重试已关闭。交互 TTY 的稳定色板只增强来源类别，`NO_COLOR=1` 或 `debug_prompt.color="never"` 时仍保留 `[config_file]`、`[data_file]`、`[runtime]`、`[generated]`、分组边界与缩进。输出重定向不是无色模式，会按非 TTY 规则失败。

批准前的上下文栏把输入近似估算、逐段估算、provider 协议开销、最大输出预留、预计峰值、模型容量、占比、剩余和 `normal/high/critical/exceeded` 分开显示；`≈` 表示 `deepseek_character_v1` 的保守离线估算，不是精确 tokenizer。容量未知或载荷不受支持时明确显示未知/不可估算。响应带合法 usage 时，TUI 已关闭，普通聊天输出只显示实际输入/输出/总量、实际占比和输入估算误差，不恢复原文。

模型能力数字来自受版本控制的 `config/providers.toml`，不是运行时探测：chat 与 memory curator 使用 `deepseek-v4-flash`，每个真实请求都显式发送 `thinking.type=disabled`，profile 输出预留仍为 8192；provider 元数据记录 1,000,000 context 和 384,000 output cap。审批载荷保持深度冻结，发送时只在 SDK 边界无损转换为可 JSON 编码的原生容器；真实 AsyncOpenAI 的本地 MockTransport 回归会比较最终 wire JSON。离线测试只含无凭据样本，不会访问真实 API。

调试界面会完整暴露当前请求中的私人记忆，只应在可信本地终端使用；复制功能会把框内内容写入由终端/操作系统管理的剪贴板，使用后可按需清除。API Key/Authorization 由 transport 单独持有，既不进入可展示载荷，也不进入 journal、日志或实际用量摘要。若 TUI 运行期失败，未决定的 `PREPARED` journal 会在下一次持锁启动时安全丢弃；`READY_PENDING` 先前滚为唯一 USER pending，再由默认丢弃、显式丢弃或显式恢复策略处理；`READY_TO_COMMIT` 按 USER、ASSISTANT、可选长期记忆顺序幂等发布。恢复或尾部校验冲突会阻止新输入和 provider，不覆盖人工修改。

## 历史时间段标注

模型看到的近期聊天会按聊天软件式的稀疏规则显示时间，而不是每条消息都重复显示。每个非空历史区块的第一项有标记；相邻事件间隔达到 30 分钟、本地日期改变、从最近标记起持续达到 120 分钟或时间倒退时，下一项重新显示标记。边界使用完整的带时区事件时间计算，正文中的仿冒标记不会改变分段。

默认策略位于独立文件 `config/history_timestamps.toml`：显示时区为 `Asia/Shanghai`，长间隔为 30 分钟，连续段刷新为 120 分钟，并启用跨本地日期分段。固定格式为 `[时间：YYYY-MM-DD HH:mm ±HH:MM]`、`[时间范围：YYYY-MM-DD HH:mm ±HH:MM 至 YYYY-MM-DD HH:mm ±HH:MM]` 和 `[记录时间：YYYY-MM-DD HH:mm ±HH:MM]`，标签与结构不可由配置覆盖。

时间标记只作用于历史数据；当前输入、基础人格、状态人格和系统规则不标注。聊天中的 `memory_overview`、`long_term_memories`、`recent_records` 分别从空状态分段，因此三个非空区块各有自己的首标记。recent 使用 raw `created_at` 点时间；长期记忆和 coverage overview 对全部已验证 `source_refs` 求最早—最晚事件时间并显示 `[时间范围：… 至 …]`，不会把整理时间冒充发生时间。

标记与对应历史正文都保持 `UNTRUSTED_DATA`，并分别记录时间配置、长期实体及原始记录来源；overview、长期选择和 recent 预算都按最终含标记文本计算。来源缺失、摘要不符、coverage 不连续或时间无效会在 provider 前失败，不使用记忆 `created_at` 降级。通用合同保留 `[记录时间：…]` 供未来明确 schema/version 的适配器复用，但当前没有持久化 `RECORDED` 入口。标记仅在提示构建期生成，不写回 `data/memory/raw/*.jsonl` 或长期记忆文件，既有文件无需迁移。

## 记忆与安全

运行数据默认位于 `data/memory/`：`raw/*.jsonl` 中已完成 USER/ASSISTANT 轮次不可变；唯一未配对的合法尾部 USER 是可放弃的未完成轮次。`long_term.yaml` 是可人工维护的长期记忆、来源索引、整理前沿和覆盖概览的共同事实来源。修改或恢复备份后先执行：

```powershell
python -m bai_agent --config-dir config --data-dir data memory validate
python -m bai_agent --config-dir config --data-dir data memory source mem-UUID
python -m bai_agent --config-dir config --data-dir data memory reset long-term
python -m bai_agent --config-dir config --data-dir data memory reset all
python -m bai_agent --data-dir data security incident check
```

`memory reset long-term` 保留永久原始聊天和近期窗口，只清空长期派生正文；`memory reset all` 清空全部聊天与长期记忆并恢复首次启动状态。两条命令立即执行且不可撤销，运行前必须先关闭聊天进程；安全事件状态不会随记忆重置而删除。

程序会尽力把 POSIX 权限收紧到目录 `0700`、文件 `0600`，并在 Windows 检查和收紧 DACL；无法证明为私有时验证失败关闭。若凭据可能进入 Git、配置、日志或运行记忆，立即停止聊天/整理，并按[凭据泄露事件处置流程](docs/security-incident-response.md)完成轮换、全仓库与历史扫描、运行数据扫描和显式解除门禁。

当前注册工具均为只读。未来写工具只有在注册时通过可恢复 `prepare/commit/rollback` 或明确补偿契约门禁后才能执行；执行、超时、结果大小或凭据校验失败都会在提交前 rollback/补偿，否则在任何副作用前拒绝。

## 开发与文档维护

项目工程约束以[项目宪章](.specify/memory/constitution.md)为准。每次完成核心业务逻辑、公共契约、架构或模块边界、数据结构、安全控制、关键依赖或平台迁移等重大更新时，必须在同一交付中同步更新受影响的 README、quickstart、运行手册、配置说明和契约文档。计划或评审若判定无需改文档，必须记录 `N/A` 及理由。

重大更新只有在适用测试通过、文档中的命令/路径/链接/示例与当前实现一致，并且相关代码与文档进入同一个原子提交后才算完成。详细验收步骤见[功能 quickstart](specs/001-persistent-memory-agent/quickstart.md)。

## 验证

```powershell
pytest
python -m bai_agent config validate --config-dir config
python -m bai_agent --config-dir config --data-dir .tmp\validation memory validate
python -m bai_agent --data-dir .tmp\validation security incident check
```

Ubuntu 24.04 × Python 3.13/3.14 的主要功能矩阵和 Windows 次要兼容矩阵在 `.github/workflows/compatibility.yml`；macOS 不在支持范围。提示 TUI 的 500 ms 强制性能门禁只在 Ubuntu 24.04/Python 3.13 固定环境运行，Windows 仅做功能验收；详细安装、人工维护、备份、来源查询和性能复现实验见[功能 quickstart](specs/001-persistent-memory-agent/quickstart.md)。

> [2026-07-19] 本文与 `specs/001-persistent-memory-agent/` 的规格、计划、契约和验收任务同步。
