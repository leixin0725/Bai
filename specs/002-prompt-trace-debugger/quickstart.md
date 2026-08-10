# Quickstart: 提示词追踪调试工具

本页是实现后的最小安装与验收流程。所有示例只使用占位凭据；不要把真实 API Key 写进命令历史、配置、测试或截图。

## 1. 安装开发环境

验收环境为原生 Ubuntu 24.04/WSL、Python 3.12/3.13/3.14；性能门禁固定使用 Python 3.13。原生 Windows 与 macOS 不在本功能范围内（Windows 用户经 WSL 使用）。

```bash
cd /path/to/bai-agent
bash scripts/bootstrap-ubuntu.sh --dev
source .venv/bin/activate
```

确认配置和 CLI 可加载：

```bash
DEEPSEEK_API_KEY=invalid-placeholder-only python -m bai_agent --config-dir config --data-dir data config validate
python -m bai_agent --help
python -m bai_agent chat --help
```

预期：帮助中存在 `chat --debug-prompts`、`--resume-pending` 和 `--discard-pending`，后两者互斥；配置验证显示 chat 与 memory curator 均为 `deepseek-v4-flash`、`thinking_enabled=false`、profile 输出限制 8192、provider context 1,000,000、输出能力上限 384,000 和 estimator，无真实凭据回显；迁移前后其他生成参数不变。

## 2. 安全注入凭据并启动

环境可由 `start.sh` 隐藏读取凭据并显式传递调试开关。实际验收使用后续 fake provider 测试，不向真实 DeepSeek API 发起请求：

```bash
bash start.sh --debug-prompts
bash start.sh --discard-pending
bash start.sh --resume-pending --debug-prompts
```

预期：每次新运行都显示“本地界面可能展示私人记忆”的提醒；退出再普通启动时调试默认关闭。

CLI 会先运行不含正文的 TTY/Textual application-mode probe；stdin/stdout 重定向或 probe 失败均在应用构建和 journal 写入前以 exit 2 失败。请求级 TUI 的 `Ctrl+C` 先回滚 fresh PREPARED 或删除 resumed pending 再返回 130，EOF/终端丢失不批准且由下次持锁恢复安全收敛。

## 3. 验收单次聊天与来源

> 实现验收说明（2026-07-20）：默认自动化使用三方法 fake adapter 经过唯一 `ModelCallGateway`，不会读取 `DEEPSEEK_API_KEY` 或访问真实 DeepSeek；`tests/contract/test_prompt_trace_provider.py` 单独捕获 SDK kwargs 证明物化与发送字段一致。

1. 输入一句包含唯一标记的消息，例如 `验收-运行时来源-001`。
2. 在 provider 收到请求前，TUI 应显示调用用途 `chat`、persona/state/provider/model/config revision/attempt。
3. 展开最终请求，核对：
   - persona 和 state 片段指向 `config/personas/...`；
   - 模板片段指向 `config/prompts/...`；
   - 长期记忆指向 `data/memory/long_term.yaml` 及稳定 memory id；
   - 短期记录指向 raw segment 及稳定 record id；
   - 当前输入标为 `runtime:user_input`，不伪造文件路径；
   - messages、正文顺序和 tools 均完整可见。
   - system message 中 persona 与 `untrusted_memory_boundary.md` 是两个独立精确 span；materialized payload 内每个不可信逻辑块真实包含一对匹配的 visible boundary。
   - 来源详情分列 `来源数`、`类型`、`路径`、`source_id`、`producer`、`entity_ids`；`entity_ids` 是实体 UUID，不是聊天顺序。
   - 左侧大纲只显示 `mN · 正文预览`（多行正文压成单行并截断），信任级别用低饱和绿/红整行提示、不出现 trust 字样；空白/空内容片段不进大纲，`[UNTRUSTED …#ID]` / `[/UNTRUSTED …#ID]` 边界标签默认折叠，按 `W` 切换显示并展开来源明细，对话历史等 `untrusted_data` 正文保持可见，复制文本始终保留完整审计块（含边界标签）。
4. 按 `C` 或点击“复制框内全部内容”，核对剪贴板包含最终 provider 载荷、全部提示片段和来源；界面应继续等待决定，且 provider 发送次数仍为 0。
5. 核对输入估算明细之和加协议开销等于输入总估算；峰值等于输入总估算加最大输出预留。
6. 按 `A` 批准。TUI 必须在网络发送前退出并清除原文、来源和 presenter 引用，sender 只保留已批准的同一不可变载荷。
7. `send_once` 成功或失败后 sender 都必须释放载荷；若响应带 usage，只在普通聊天输出显示实际输入/输出/总量和估算误差，不重新打开 TUI 或恢复原提示正文。

## 4. 验收拒绝后整轮不存在

测试前记录持久状态摘要：

```bash
sha256sum data/memory/long_term.yaml
sha256sum data/memory/raw/*
```

启动调试，输入唯一标记 `验收-必须撤销-002`，在任一 approval app 按 `R`：

- 当前待批请求发送次数为 0；
- 返回聊天输入界面；
- raw history、长期记忆、来源索引、state 与轮前一致；
- `data/memory/.state/turn-transaction.json` 不存在；
- 重启后没有 pending/cancelled/tombstone，也搜索不到该标记。

自动化覆盖整理、工具续接和 retry 等较难手工稳定触发的拒绝点：

```bash
pytest tests/integration -k "debug and reject" -q
pytest tests/fault_injection -k "turn_transaction" -q
```

## 5. 验收多次模型调用与逐次批准

```bash
pytest tests/integration -k "debug and (curation or tool or retry)" -q
pytest tests/contract -k "model_call_gateway" -q
```

预期：整理、聊天、工具结果续接、未来辅助人格和 provider retry 每个物理 attempt 都按真实顺序独立出现；每次只有一个 approval，前一项未决定时不处理后一项；retry 不恢复上一项 TUI。每个 DeepSeek 请求的最终载荷都包含 `extra_body.thinking.type=disabled`；工具续接在匹配 tool result 前包含产生调用的 assistant/tool_calls。

标题同时核对 turn、flow、call sequence、purpose、persona、state、provider、model、config revision、attempt 和 status；call sequence 由共享网关分配器生成，调用方不能覆盖。retry 保持逻辑 call 字段稳定，只增加 attempt 并保留前一失败状态。

## 6. 验收普通失败与 pending

```bash
pytest tests/integration/test_turn_transaction_pending.py -q
pytest tests/integration -k "resume_pending and provider" -q
pytest tests/contract/test_cli_chat.py tests/fault_injection/test_pending_discard_atomicity.py -q
```

预期：retry exhausted、non-retryable provider error 和网络中断均经 `READY_PENDING` 幂等发布且只发布一条 USER pending；显式 `--resume-pending` 复用原 turn 且不追加 USER，默认启动或 `--discard-pending` 只原子截去合法尾部 pending 后进入新输入。fresh approval app 的 R/Esc/Ctrl+C 丢弃 PREPARED；resumed approval app 的相同明确拒绝删除已有 raw pending。不存在 pending 时三种模式均直接进入新输入。

手工命令矩阵：

```bash
python -m bai_agent --config-dir config --data-dir data chat
python -m bai_agent --config-dir config --data-dir data chat --discard-pending
python -m bai_agent --config-dir config --data-dir data chat --resume-pending
python -m bai_agent --config-dir config --data-dir data chat --resume-pending --debug-prompts
```

禁止以手工编辑 JSONL 或 `memory reset all` 代替丢弃。debug 非 TTY 预检失败发生在 pending 修改前，因此该场景保留 pending。

## 7. 验收无色与终端边界

交互式 Linux TTY 设置标准无色变量：

```bash
NO_COLOR=1 python -m bai_agent --config-dir config --data-dir /tmp/bai-debug-acceptance chat --debug-prompts
```

经启动脚本的等价验收：

```bash
NO_COLOR=1 bash start.sh --debug-prompts
```

预期：无 ANSI 颜色或 Rich 样式 span，但调用、included/excluded/empty/unknown_source、trusted_instruction/trusted_metadata/user_instruction/untrusted_data、`message:N`、来源字段、边界、缩进和操作含义仍完整。启用颜色时 message index 使用确定性的低饱和基础色，同一历史 message 内按结构化 record 顺序 A/B 交错；颜色不进入复制文本、日志或 provider payload。主体为两栏审计视图：左侧 part 大纲、右侧选中项详情（虚拟化 `TraceView`），首帧展示身份/估算/操作区，正文按选择按需加载，超大内容后台换行并显示占位符；80×24 下操作按钮、大纲、详情与完整复制文本均可访问。

稳定色板为 config_file=cyan、data_file=green、runtime=yellow、generated=magenta；颜色不是唯一语义。`NO_COLOR`/`never`/终端不支持颜色只适用于 stdin/stdout 都是 TTY 的交互环境，输出重定向必须 fail closed。

非交互门禁由自动化验证，避免把私人 prompt 真正重定向到磁盘：

```bash
pytest tests/contract -k "debug_tty_required" -q
```

预期：stdin 或 stdout 非 TTY 时在任何模型请求和持久状态修改前以 `DEBUG_TTY_REQUIRED`/exit 2 失败，不自动批准。

## 8. 验收估算诚实性

```bash
pytest tests/unit -k "token_estimator or context_usage" -q
pytest tests/integration -k "actual_usage" -q
```

检查空白、中文、英文、混合 Unicode、长记忆、tools 和未知模型：

离线参考集位于 `tests/fixtures/prompt_trace/deepseek_usage_cases.json`，记录 `deepseek-v4-flash`、采集日期、官方 prompt usage、payload hash 与刷新说明；默认测试只读取该文件，不访问实时 API。

- 可估算时 `input == sum(parts) + protocol_overhead`；
- `peak == input + max_output_tokens`；
- 容量未知时占比/剩余显示未知；
- 不支持的载荷显示不可估算及原因，不显示字符数/4 等伪精确值；

时间 marker 已在 estimator 前成为普通 prompt fragment，因此 marker token 只应在 part 明细中出现一次。工具续接还应检查 assistant content 的 marker/body parts 与独立 `/tool_calls` part：同一 content pointer 的 spans 从 0 连续覆盖到正文结尾，不能存在 whole-content 重叠 fallback。

```bash
pytest tests/integration/test_prompt_debug_equivalence.py tests/unit/test_prompt_provenance.py -q
pytest tests/contract/test_temporal_tool_protocol.py tests/integration/test_temporal_tool_continuation.py -q
```

用相同 deterministic clock 分别运行 debug off/on，两个最终 SDK payload 必须逐字相同。provider retry 只在成功响应接受后生成一次 tool-call origin；后续 continuation 的 tool_calls part 必须继续指向该 origin，而不是当前 draft call id。
- 实际 usage 缺失/无效时不冒充实际值。

## 9. Linux 性能与全量验证

在原生 Ubuntu 24.04、Python 3.13、80×24 `xterm-256color` 中运行：

```bash
pytest tests/performance/test_prompt_tui_latency.py -q -s
pytest -q
git diff --check
python -m pytest tests/integration/test_repository_secret_safety.py -q
```

性能测试从 frozen request、来源和估算全部就绪测到标题、身份和上下文摘要完成 mounted；记录首次冷启动，但强制门禁只计算随后 30 次同一进程启动的 p95：小样本不超过 500 ms，300K 字符大载荷样本不超过 1000 ms，1M 字符样本不超过 2000 ms。

门禁基线为 `tests/performance/baselines/ubuntu-24.04-python-3.13.json`；原生 Windows 与 macOS 不在范围。

实现阶段还应完成：

- 200 次混合调用中 approval 数与实际出站数/顺序一一对应；
- 代表性 DeepSeek 请求估算误差目标；
- 1,000 次连续批准后已发送 prompt/source 调试引用为 0；
- 每个事务持久化步骤的崩溃恢复；
- README、001 quickstart/contracts 与本页命令同步。

US4 可执行回归：

```bash
pytest tests/contract/test_cli_prompt_debug.py tests/integration/test_prompt_debug_equivalence.py tests/integration/test_turn_transaction_security.py tests/performance/test_prompt_trace_release.py tests/integration/test_prompt_debug_runtime_lifecycle.py -q
pytest tests/contract/test_tool_transaction_capabilities.py tests/unit/test_logging.py -q
```

## 10. 故障判断

| Symptom | Expected action |
|---|---|
| 来源未知、pointer/span 不匹配 | 阻止 TUI 批准与发送；检查 loader/assembler/adapter 来源传递 |
| 估算不可用 | 仍可查看和批准，但明确显示原因；补充兼容 estimator，不手填猜测值 |
| 预计峰值超限 | TUI 显示 `exceeded` 并区分主要输入 part 与输出预留贡献 |
| 非交互终端 | 改在真实交互终端运行；不得通过重定向绕过 |
| TUI 初始化/渲染失败 | 发送次数为 0；修复终端能力后重试 |
| 批准后同一 call/attempt 反复出现 | 仅网络/超时或 HTTP 429/500/503 应产生新 attempt；400/401/402/403/422 必须只审批一次。运行 provider 合同测试并核对最终载荷含 `thinking.type=disabled` |
| 工具续接返回 HTTP 400 | 核对续接消息按 assistant/tool_calls → tool result 排列，且 tool_call_id 完全匹配；非思考模式不保存或回传 reasoning_content |
| 批准后立即返回 `PROVIDER_FAILED` 且没有 HTTP 状态 | 运行 `pytest tests/contract/test_prompt_trace_provider.py -q`；真实 AsyncOpenAI MockTransport 必须能编码冻结载荷。sender 只在 SDK 边界用 `thaw_json()` 恢复原生 JSON 容器 |
| PREPARED journal 残留 | 重启后自动丢弃，且不形成 pending/history |
| READY_PENDING journal 残留 | 重启后先幂等发布唯一 USER pending；默认/`--discard-pending` 随后截尾，`--resume-pending` 保留并恢复；不得发布 assistant/long-term |
| READY_TO_COMMIT journal 残留 | 重启后幂等发布完整轮次；冲突时停止新轮并按脱敏错误指引处理 |
| 普通 provider/网络失败 | 与明确 reject 区分；只形成一条 USER pending，只有 `--resume-pending` 恢复，默认重启将其丢弃 |
| pending 截尾校验失败 | 不修改 raw/long-term，不调用 provider；检查历史配对、segment/hash、expected turn 和长期来源引用 |
| 写工具无恢复能力 | 在任何副作用前拒绝；当前工具必须全部只读 |
| provider 无实际 usage | 保留“估算”标签，不显示伪造实际值 |
| 疑似凭据命中 | 请求和显示均阻断；轮换真实凭据并按现有安全事件流程检查 |
