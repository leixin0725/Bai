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

核心运行时（2026-08-08 起逐步落地）：

- 统一串行消息处理管道：对话消息、定时事件与系统事件按到达顺序进入同一流程，同一会话防重入（不引入事件总线）；
- 优雅启动/停止：EOF 正常退出；Ctrl+C 第一次等待当前轮完成后退出（130），第二次立即中止；SIGTERM 优雅停止后返回 0；pending 与写锁语义保持不变。
- 一次输入动作合并：管道输入整批内容作为一次处理；交互式终端使用 raw 模式行编辑器——Enter 发送、Shift+Enter（或 Ctrl+J）换行，退格按终端 cell 宽度删除（CJK/emoji/组合字符均按显示宽度擦除；退格时始终整行重绘当前逻辑行，避免终端右边缘自动换行语义差异造成残留或错位；Shift+Enter 换出的行同样可用退格删回上一行），括号粘贴（`CSI 200~…201~`）内容整体进入缓冲且标记不回显，粘贴后按 Enter 提交；Shift+Enter 通过 Kitty 键盘协议（请求标志 1|4，`CSI >1;4u`）识别 `CSI 13;2u`，并兼容部分终端/键位注入的 `ESC+CR` 传统编码；不支持时回退缓冲/`select` 尽力合并（无时间窗口）；
- 最小后台执行器与事件投递入口：后台任务串行执行、状态可查，定时/系统事件经统一管道投递；
- 会话内 `:status`：随时查看当前会话、队列、后台任务与健康度（含最近配置重载结果）。

## 安装

主要开发和部署环境是 Ubuntu 24.04（或 WSL 中的 Ubuntu），支持 Python 3.12、3.13 或 3.14；不支持原生 Windows。Ubuntu 24.04 的系统 Python 3.12 可以直接使用：

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv git
bash scripts/bootstrap-ubuntu.sh --dev
```

不需要开发/测试依赖的运行机器可省略 `--dev`。完整的 Ubuntu 迁移、部署、升级和备份方法见 [Ubuntu 部署手册](docs/ubuntu-deployment.md)。所有提示词和可变参数都在 `config/`。DeepSeek 凭据只通过外部秘密管理器、进程环境变量 `DEEPSEEK_API_KEY` 或启动脚本的隐藏输入注入，不要写入配置、命令历史、人格或记忆文件。

```bash
DEEPSEEK_API_KEY=invalid-placeholder-only .venv/bin/python -m bai_agent config validate --config-dir config
.venv/bin/python -m bai_agent --config-dir config --data-dir data doctor
bash start.sh
```

`start.sh` 是 `bai-agent` 的透传壳：无参数时默认进入 `chat`，其它任何 `bai-agent` 参数都可以手动传入，例如 `bash start.sh doctor`、`bash start.sh memory validate`、`bash start.sh --config-dir custom --data-dir custom chat`。没有显式命令时脚本会自动补 `chat`，因此 `bash start.sh --debug-prompts` 等价于 `bash start.sh chat --debug-prompts`；`bash start.sh --help` 显示 `bai-agent` 的完整帮助。API Key 只在运行 `chat` 时由脚本隐藏提示，`doctor`、`memory validate` 等离线命令直接运行；需要凭据的 `config validate` 可沿用上方占位值注入方式。

普通启动发现合法尾部 USER pending 时，会只丢弃该未完成轮次并直接等待新输入；不会重发旧正文，也不会删除此前完整聊天或长期记忆。只有确认重试同一轮时显式执行：

```bash
bash start.sh --resume-pending
```

也可显式表达丢弃意图；没有 pending 时三种启动方式都安全进入新输入：

```bash
bash start.sh --discard-pending
bash start.sh --resume-pending --debug-prompts
```

resume 与 discard 参数互斥；`--resume-pending` 是唯一允许重发旧 pending 内容的方式。丢弃只允许原子截去 raw archive 最末尾的未配对 USER，完整 USER/ASSISTANT 轮次继续永久不可删除。

项目只支持 Ubuntu/WSL Linux。Windows 用户可在 Windows Terminal 等终端中打开 WSL 后按本文与[Ubuntu 部署手册](docs/ubuntu-deployment.md)操作；仓库必须位于 WSL 文件系统（例如 `$HOME/Dev_project/Bai`），不要放在 `/mnt/c`、`/mnt/d` 等 Windows 挂载目录中。

## 提示词调试批准

仅在本地交互式 TTY 中按当前进程启用：

```bash
.venv/bin/python -m bai_agent --config-dir config --data-dir data chat --debug-prompts
```

每个聊天、记忆整理、工具续接与 provider retry 都通过唯一 `ModelCallGateway`，在 DeepSeek `prepare()` 和唯一 `materialize_sdk_kwargs()` 后展示最终模型可见字段、完整正文及 `[config_file]`、`[data_file]`、`[runtime]`、`[generated]` 来源。界面会提醒私人记忆只在本机临时显示；按 `C` 或点击“复制框内全部内容”可复制最终载荷、提示片段和来源，且不会作出批准决定；按 `A` 逐请求批准，按 `R`/`Esc` 明确拒绝并无痕撤销整轮。批准绑定 call、attempt 与物化载荷摘要，不修改请求；批准后、网络发送前 TUI 清除正文和来源，`send_once()` 无论成功或失败都在 `finally` 释放 sender 载荷。

明确拒绝不会留下历史或 pending；恢复旧 pending 时发生 R/Esc/拒绝按钮/Ctrl+C，也会安全删除 raw 尾部的该 pending。已经批准的请求若因普通 provider/网络错误且重试结束，则只发布一条 USER pending；只有 `--resume-pending` 恢复，下一次普通启动或 `--discard-pending` 会丢弃并继续新对话。该模式默认关闭、退出即失效，也不会保存原始追踪。stdin/stdout 任一不是 TTY 时以 `DEBUG_TTY_REQUIRED`/exit 2 在任何持久化和模型发送前失败；Textual application-mode 预检同样先于应用构建。TUI 中 `Ctrl+C` 先按拒绝路径撤销再以 130 退出，EOF/终端丢失绝不批准并以脱敏 presentation failure 阻断。

同一轮的 memory curation → chat → tool continuation 按网关分配的严格 call sequence 逐项出现；tool continuation 会先回放 assistant/tool_calls，再追加匹配的 tool result。只有网络/超时和 HTTP 429/500/503 会形成新的 retry attempt 与批准项；400/401/402/403/422 等不可重试错误只审批/发送一次并立即返回脱敏错误，OpenAI SDK 内部重试已关闭。交互 TTY 使用低饱和莫兰迪色：`message:N` 决定稳定基础色，历史 message 内按结构化 `rec-*` 记录顺序使用 A/B 近似色交错，同一记录的 marker、分隔符和正文保持同组；来源类型只作次级强调。`NO_COLOR=1` 或 `debug_prompt.color="never"` 时不产生 ANSI/样式，但仍保留全部文本标签、分组边界与缩进（大纲不附加信任文字，选中条目后在详情标题查看信任级别）。输出重定向不是无色模式，会按非 TTY 规则失败。

左侧大纲只显示简短预览：每行由 `mN · 正文预览` 组成，预览把多行正文压成单行并截断；信任级别不写文字，而以低饱和绿（可信/当前用户指令）与低饱和红（不可信数据）整行提示，无颜色模式下不附加文字标签，选中条目后可在详情标题查看 `信任=` 与 `状态=`。空白/空内容片段永远不进大纲；`[UNTRUSTED …#ID]` / `[/UNTRUSTED …#ID]` 边界标签默认折叠，按 `W` 切换显示，对话历史等 `untrusted_data` 正文保持可见。图例同时解释 `included/excluded/empty/unknown_source`、`trusted_instruction/trusted_metadata/user_instruction/untrusted_data` 与 `message:N`；来源详情明确分列 `来源数`、`类型`、`路径`、`source_id`、`producer` 与 `entity_ids`，其中 `entity_ids` 是 `rec-*`、`turn-*` 等实体 UUID，不代表聊天顺序。详情视图默认隐藏来源明细，按 `W` 展开后显示全部来源明细；`C` 复制始终包含完整审计块（含边界标签）。该折叠与配色只改变 TUI renderable，不改变 provider payload、RequestPart、span 或 token 估算；80×24 终端仍可滚动查看完整 trace 并操作按钮。

批准前的上下文栏把输入近似估算、逐段估算、provider 协议开销、最大输出预留、预计峰值、模型容量、占比、剩余和 `normal/high/critical/exceeded` 分开显示；`≈` 表示 `deepseek_character_v1` 的保守离线估算，不是精确 tokenizer。容量未知或载荷不受支持时明确显示未知/不可估算。响应带合法 usage 时，TUI 已关闭，普通聊天输出只显示实际输入/输出/总量、实际占比和输入估算误差，不恢复原文。

模型能力数字来自受版本控制的 `config/providers.toml`，不是运行时探测：chat 与 memory curator 使用 `deepseek-v4-flash`，每个真实请求都显式发送 `thinking.type=disabled`，profile 输出预留仍为 8192；provider 元数据记录 1,000,000 context 和 384,000 output cap。审批载荷保持深度冻结，发送时只在 SDK 边界无损转换为可 JSON 编码的原生容器；真实 AsyncOpenAI 的本地 MockTransport 回归会比较最终 wire JSON。离线测试只含无凭据样本，不会访问真实 API。

调试界面会完整暴露当前请求中的私人记忆，只应在可信本地终端使用；复制功能会把框内内容写入由终端/操作系统管理的剪贴板，使用后可按需清除。API Key/Authorization 由 transport 单独持有，既不进入可展示载荷，也不进入 journal、日志或实际用量摘要。若 TUI 运行期失败，未决定的 `PREPARED` journal 会在下一次持锁启动时安全丢弃；`READY_PENDING` 先前滚为唯一 USER pending，再由默认丢弃、显式丢弃或显式恢复策略处理；`READY_TO_COMMIT` 按 USER、ASSISTANT、可选长期记忆顺序幂等发布。恢复或尾部校验冲突会阻止新输入和 provider，不覆盖人工修改。

## 历史时间段标注

模型看到的近期聊天会按聊天软件式的稀疏规则显示时间，而不是每条消息都重复显示。每个非空历史区块的第一项有标记；相邻事件间隔达到 30 分钟、本地日期改变、从最近标记起持续达到 120 分钟或时间倒退时，下一项重新显示标记。边界使用完整的带时区事件时间计算，正文中的仿冒标记不会改变分段。

默认策略位于独立文件 `config/history_timestamps.toml`：显示时区为 `Asia/Shanghai`，长间隔为 30 分钟，连续段刷新为 120 分钟，并启用跨本地日期分段。固定格式为 `[时间：YYYY-MM-DD HH:mm ±HH:MM]`、`[时间范围：YYYY-MM-DD HH:mm ±HH:MM 至 YYYY-MM-DD HH:mm ±HH:MM]` 和 `[记录时间：YYYY-MM-DD HH:mm ±HH:MM]`，标签与结构不可由配置覆盖。

`long_gap_minutes` 的单位为分钟、范围 `1..1440`；`continuous_segment_refresh_minutes` 范围 `1..10080` 且不得小于 gap；跨日开关必须是布尔值，显示时区必须是可解析的 IANA 名称。标准库 `zoneinfo` 配合运行依赖 `tzdata`，使各 Ubuntu/WSL 环境不依赖本机 locale 或固定 offset。有效改动在下一轮把 assembler、curation、tool bridge 与 provider/executor 整组替换；缺失、未知字段、类型/范围/关系或时区错误会在 raw、工具和 provider 边界前失败，旧对象不部分更新。修复文件后直接重试下一轮即可，无需迁移记忆。

当前输入也复用同一个 annotator，并严格使用本轮已经建立的 provisional `RawRecord.created_at`；retry、恢复 pending、TUI 审批与实际发送不重新读取 wall clock。只有当前输入的时间 marker 作为可信时间元数据放在边界外，用户正文放入 `current_input` 不可信边界内，同时仍保持本轮 `USER_INSTRUCTION` 语义；历史记录中的 marker 暂时继续与历史正文一起位于不可信边界内。基础人格、状态人格和系统规则不标注。聊天中的 `memory_overview`、`long_term_memories`、`recent_records` 分别从空状态分段，因此三个非空区块各有自己的首标记。recent 使用 raw `created_at` 点时间；长期记忆和 coverage overview 对全部已验证 `source_refs` 求最早—最晚事件时间并显示 `[时间范围：… 至 …]`，不会把整理时间冒充发生时间。

记忆整理同样把 `batch_records`、`existing_memories`、`current_overview` 作为三个独立历史区块，并按来源事件时间自然排列。模型可见的 `memory_curation_v2` 语义视图只含：原始记录的时间/角色/正文/`rN` 短别名，既有记忆的五类类别/正文/状态/来源时间，以及概览正文和时间覆盖范围；真实 UUID、摘要、配置修订、完整 coverage DTO 与批次元数据不进入 provider 正文。模型只返回候选的 `kind/text/sources` 和 `overview`；应用本地把别名解析为真实来源、自动附加整批 coverage，并继续校验来源 hash、连续前沿和精确 provenance。输出契约使用短小固定结构，不再展开完整自动 Schema 或要求回显批次 ID。构建器仍在模板展开时累计每个 marker/JSON 片段的绝对位置，因此重复正文也有精确、互不混淆的来源 span。

当前共有八个时间化逻辑区块：聊天的 `memory_overview`、`long_term_memories`、`recent_records`、`current_input`，整理的 `batch_records`、`existing_memories`、`current_overview`，以及当前轮 `tool_history`。工具调用批次以成功响应被网关接受的时刻为 EVENT，工具结果以校验、大小/安全检查和事务处理完成后的可发送时刻为 EVENT；这些进程内时间不写入 DTO JSON。每次续接从整轮未标注事件重建同一个 block，仍保持相邻 assistant(tool_calls)→tool、原 call id/name/arguments/tool_call_id 和 canonical result body。`memory_source_query` 直接返回完全不变，只有作为普通 tool message 回放时可在外层正文前出现 marker。

未来日志消费者只需提供有序原始 body、稳定 identity、真实来源与 EVENT/SOURCE_RANGE，再复用同一策略并把 fragments 映射为无重叠 spans；不得自行复制时间规则。调试开关只增加审批门禁，固定时钟下最终物化载荷逐字相同，界面看到的 marker 就是实际发送且已计入估算的 marker。

每个不可信逻辑块只在最终 provider 正文中出现一对简短的内容绑定边界：`[UNTRUSTED 块名#8位ID]` 与匹配的 `[/UNTRUSTED 块名#8位ID]`。历史、长期记忆、覆盖概览、整理输入以及每个工具事件都使用同一个 renderer；不会发送 provider 不支持的自定义字段，也不会逐条堆叠标签。system message 中 `chat.md`/`memory_curator.md` 与 `untrusted_memory_boundary.md` 虽共同发送，却各自保留真实 `ConfigSnapshot` asset/hash/revision 的精确 span；reload 后整组原子替换。边界、标记与历史正文都进入最终字符预算、token 估算和 provenance 校验。来源缺失、摘要不符、coverage 不连续或时间无效会在 provider 前失败，不使用记忆 `created_at` 降级。通用合同保留 `[记录时间：…]` 供未来明确 schema/version 的适配器复用，但当前没有持久化 `RECORDED` 入口。标记和边界仅在提示构建期生成，不写回 `data/memory/raw/*.jsonl` 或长期记忆文件，既有文件无需迁移。

## 记忆与安全

运行数据默认位于 `data/memory/`：`raw/*.jsonl` 中已完成 USER/ASSISTANT 轮次不可变；唯一未配对的合法尾部 USER 是可放弃的未完成轮次。`long_term.yaml` 是可人工维护的长期记忆、来源索引、整理前沿和覆盖概览的共同事实来源。修改或恢复备份后先执行：

```bash
.venv/bin/python -m bai_agent --config-dir config --data-dir data memory validate
.venv/bin/python -m bai_agent --config-dir config --data-dir data memory source mem-UUID
.venv/bin/python -m bai_agent --config-dir config --data-dir data memory reset long-term
.venv/bin/python -m bai_agent --config-dir config --data-dir data memory reset all
.venv/bin/python -m bai_agent --data-dir data security incident check
```

`memory reset long-term` 保留永久原始聊天和近期窗口，只清空长期派生正文；`memory reset all` 清空全部聊天与长期记忆并恢复首次启动状态。两条命令立即执行且不可撤销，运行前必须先关闭聊天进程；安全事件状态不会随记忆重置而删除。

程序会把 POSIX 权限收紧到目录 `0700`、文件 `0600`；无法证明为私有时验证失败关闭。若凭据可能进入 Git、配置、日志或运行记忆，立即停止聊天/整理，并按[凭据泄露事件处置流程](docs/security-incident-response.md)完成轮换、全仓库与历史扫描、运行数据扫描和显式解除门禁。

当前注册工具均为只读。未来写工具只有在注册时通过可恢复 `prepare/commit/rollback` 或明确补偿契约门禁后才能执行；执行、超时、结果大小或凭据校验失败都会在提交前 rollback/补偿，否则在任何副作用前拒绝。

## 开发与文档维护

项目工程约束以[项目宪章](AGENTS.md)为准。每次完成核心业务逻辑、公共契约、架构或模块边界、数据结构、安全控制、关键依赖或平台迁移等重大更新时，必须在同一交付中同步更新受影响的 README、quickstart、运行手册、配置说明和契约文档。计划或评审若判定无需改文档，必须记录 `N/A` 及理由。

重大更新只有在适用测试通过、文档中的命令/路径/链接/示例与当前实现一致，并且相关代码与文档进入同一个原子提交后才算完成。详细验收步骤见[功能 quickstart](specs/001-persistent-memory-agent/quickstart.md)。

## 验证

```bash
.venv/bin/python -m pytest
.venv/bin/python -m bai_agent config validate --config-dir config
.venv/bin/python -m bai_agent --config-dir config --data-dir .tmp/validation memory validate
.venv/bin/python -m bai_agent --data-dir .tmp/validation security incident check
```

Ubuntu 24.04 × Python 3.12/3.13/3.14 的功能矩阵在 `.github/workflows/compatibility.yml`；原生 Windows 与 macOS 不在支持范围（Windows 用户经 WSL 使用）。提示 TUI 的 500 ms 强制性能门禁只在 Ubuntu 24.04/Python 3.13 固定环境运行；详细安装、人工维护、备份、来源查询和性能复现实验见[功能 quickstart](specs/001-persistent-memory-agent/quickstart.md)。

> [2026-07-19] 本文与 `specs/001-persistent-memory-agent/` 的规格、计划、契约和验收任务同步。
