# Quickstart: 提示词追踪调试工具

本页是实现后的最小安装与验收流程。所有示例只使用占位凭据；不要把真实 API Key 写进命令历史、配置、测试或截图。

## 1. 安装开发环境

主要验收环境为原生 Ubuntu 24.04、Python 3.13/3.14；性能门禁固定使用 Python 3.13。Windows 11/PowerShell 仅做次要功能兼容验收，macOS 不在本功能范围内。

```bash
cd /path/to/Bai
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Windows 次要兼容环境：

```powershell
Set-Location D:\SchoolWork\Self\Bai
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

确认配置和 CLI 可加载：

```bash
python -m bai_agent --config-dir config --data-dir data config validate
python -m bai_agent --help
python -m bai_agent chat --help
```

预期：帮助中存在 `chat --debug-prompts`；配置验证显示 chat 与 memory curator 均为 `deepseek-v4-flash`、`thinking_enabled=false`、profile 输出限制 8192、provider context 1,000,000、输出能力上限 384,000 和 estimator，无真实凭据回显；迁移前后其他生成参数不变。

## 2. 安全注入凭据并启动

Linux 主要环境通过受控环境变量注入凭据并显式传递调试开关。以下代码块仅说明启动语法，使用明确无效占位符，不属于自动验收且不得向真实 DeepSeek API 发起请求；实际验收使用后续 fake provider 测试：

```bash
export DEEPSEEK_API_KEY='invalid-example-only'
python -m bai_agent --config-dir config --data-dir /tmp/bai-debug-acceptance chat --debug-prompts
unset DEEPSEEK_API_KEY
```

Windows 次要环境推荐沿用项目启动脚本的隐藏输入能力：

```powershell
.\start.ps1 -DebugPrompts
```

预期：每次新运行都显示“本地界面可能展示私人记忆”的提醒；退出再普通启动时调试默认关闭。

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
4. 核对输入估算明细之和加协议开销等于输入总估算；峰值等于输入总估算加最大输出预留。
5. 按 `A` 批准。TUI 必须在网络发送前退出并清除原文、来源和 presenter 引用，sender 只保留已批准的同一不可变载荷。
6. `send_once` 成功或失败后 sender 都必须释放载荷；若响应带 usage，只在普通聊天输出显示实际输入/输出/总量和估算误差，不重新打开 TUI 或恢复原提示正文。

## 4. 验收拒绝后整轮不存在

测试前记录持久状态摘要：

```bash
sha256sum data/memory/long_term.yaml
sha256sum data/memory/raw/*
```

Windows 次要兼容环境可使用 `Get-FileHash` 取得同一基线。

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

预期：整理、聊天、工具结果续接、未来辅助人格和 provider retry 每个物理 attempt 都按真实顺序独立出现；每次只有一个 approval，前一项未决定时不处理后一项；retry 不恢复上一项 TUI。

标题同时核对 turn、flow、call sequence、purpose、persona、state、provider、model、config revision、attempt 和 status；call sequence 由共享网关分配器生成，调用方不能覆盖。retry 保持逻辑 call 字段稳定，只增加 attempt 并保留前一失败状态。

## 6. 验收普通失败与 pending

```bash
pytest tests/integration/test_turn_transaction_pending.py -q
pytest tests/integration -k "resume_pending and provider" -q
```

预期：retry exhausted、non-retryable provider error 和网络中断均经 `READY_PENDING` 幂等发布且只发布一条 USER pending，可用既有 `--resume-pending` 恢复；维护者在 approval app 中按 `R` 的明确拒绝从 PREPARED 丢弃且不形成 pending。

## 7. 验收无色与终端边界

交互式 Linux TTY 设置标准无色变量：

```bash
NO_COLOR=1 python -m bai_agent --config-dir config --data-dir /tmp/bai-debug-acceptance chat --debug-prompts
```

Windows PowerShell 次要兼容验收：

```powershell
$env:NO_COLOR = "1"
.\start.ps1 -DebugPrompts
Remove-Item Env:NO_COLOR -ErrorAction SilentlyContinue
```

预期：无 ANSI 颜色，但调用、included/excluded/empty、来源类型、边界、缩进和操作含义仍完整。

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

- 可估算时 `input == sum(parts) + protocol_overhead`；
- `peak == input + max_output_tokens`；
- 容量未知时占比/剩余显示未知；
- 不支持的载荷显示不可估算及原因，不显示字符数/4 等伪精确值；
- 实际 usage 缺失/无效时不冒充实际值。

## 9. Linux 性能与全量验证

在原生 Ubuntu 24.04、Python 3.13、80×24 `xterm-256color` 中运行：

```bash
pytest tests/performance/test_prompt_tui_latency.py -q -s
pytest -q
git diff --check
python -m pytest tests/integration/test_repository_secret_safety.py -q
```

性能测试从 frozen request、来源和估算全部就绪测到标题、身份和上下文摘要完成 mounted；记录首次冷启动，但强制门禁只计算随后 30 次同一进程启动的 p95，要求不超过 500 ms。

Windows 次要功能兼容验收：

```powershell
pytest -q
git diff --check
python -m pytest tests/integration/test_repository_secret_safety.py -q
```

实现阶段还应完成：

- 200 次混合调用中 approval 数与实际出站数/顺序一一对应；
- 代表性 DeepSeek 请求估算误差目标；
- 1,000 次连续批准后已发送 prompt/source 调试引用为 0；
- 每个事务持久化步骤的崩溃恢复；
- README、001 quickstart/contracts 与本页命令同步。

## 10. 故障判断

| Symptom | Expected action |
|---|---|
| 来源未知、pointer/span 不匹配 | 阻止 TUI 批准与发送；检查 loader/assembler/adapter 来源传递 |
| 估算不可用 | 仍可查看和批准，但明确显示原因；补充兼容 estimator，不手填猜测值 |
| 预计峰值超限 | TUI 显示 `exceeded` 并区分主要输入 part 与输出预留贡献 |
| 非交互终端 | 改在真实交互终端运行；不得通过重定向绕过 |
| TUI 初始化/渲染失败 | 发送次数为 0；修复终端能力后重试 |
| PREPARED journal 残留 | 重启后自动丢弃，且不形成 pending/history |
| READY_PENDING journal 残留 | 重启后幂等发布且只发布一条 USER pending，可用 `--resume-pending`；不得发布 assistant/long-term |
| READY_TO_COMMIT journal 残留 | 重启后幂等发布完整轮次；冲突时停止新轮并按脱敏错误指引处理 |
| 普通 provider/网络失败 | 与明确 reject 区分；只形成一条 USER pending，重启或 `--resume-pending` 恢复 |
| 写工具无恢复能力 | 在任何副作用前拒绝；当前工具必须全部只读 |
| provider 无实际 usage | 保留“估算”标签，不显示伪造实际值 |
| 疑似凭据命中 | 请求和显示均阻断；轮换真实凭据并按现有安全事件流程检查 |
