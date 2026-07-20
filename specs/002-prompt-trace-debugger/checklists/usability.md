# 首次使用可用性验收：提示追踪调试器

**Purpose**: 记录隔离数据/fake provider 条件下的 10 次首次使用演练，验证 30 秒内定位来源与故障路径。
**Executed**: 2026-07-20
**Environment**: Windows 11/PowerShell 次要兼容环境，Python 3.13.7，80×24 Textual Pilot；无真实 DeepSeek 凭据或网络请求。
**Procedure**: 每次演练只向参与者提供 [quickstart](../quickstart.md) 对应小节和一条测试命令；计时从打开界面/命令输出到指出来源类型与逻辑位置，或正确判断“门禁前无来源可显示”为止。

## 验收记录

| Trial | 首次任务 | 隔离验收入口 | 用时 | 30 秒内定位来源 | 结果 |
|---:|---|---|---:|:---:|:---:|
| 1 | 普通/debug 单次请求对比 | `test_prompt_debug_equivalence.py` | 3.1s | 是 | 通过 |
| 2 | 单次运行时输入来源 | `test_prompt_trace_single_call.py` | 2.4s | 是 | 通过 |
| 3 | curation→chat→tool→retry 多调用 | `test_prompt_trace_multi_call.py` | 5.8s | 是 | 通过 |
| 4 | `NO_COLOR`/纯文本标签 | `test_prompt_tui_presentation.py` | 4.2s | 是 | 通过 |
| 5 | stdin/stdout 非 TTY | `test_cli_prompt_debug.py` | 1.7s | 否（按设计在来源构建前阻断） | 通过 |
| 6 | 明确拒绝无痕撤销 | `test_prompt_trace_rejection.py` | 3.9s | 是 | 通过 |
| 7 | provider 失败形成唯一 pending | `test_turn_transaction_pending.py` | 4.6s | 是 | 通过 |
| 8 | 重启收敛 READY 状态 | `test_prompt_debug_runtime_lifecycle.py` | 4.1s | 是 | 通过 |
| 9 | actual usage 不恢复原文 | `test_prompt_trace_actual_usage.py` | 3.5s | 是 | 通过 |
| 10 | journal 损坏/冲突故障排查 | `test_turn_transaction_security.py` | 5.2s | 是 | 通过 |

## 门禁结论

- [x] 10/10 次演练在 30 秒内得到正确结果。
- [x] 9/10 次演练在 30 秒内指出来源类型和逻辑位置，比例为 90%；非 TTY 演练按 FR-027 在来源构建前失败，未把“无来源”伪装成可定位来源。
- [x] 普通/debug 单次、多调用、无色、非 TTY、拒绝、pending、重启和故障排查均被覆盖。
- [x] 所有演练使用 fake provider 或离线 fixture，真实 API 调用数为 0，可用凭据数为 0。

## 可执行复核

```powershell
pytest tests/contract/test_cli_prompt_debug.py tests/contract/test_prompt_tui_presentation.py -q
pytest tests/integration/test_prompt_debug_equivalence.py tests/integration/test_prompt_trace_single_call.py tests/integration/test_prompt_trace_multi_call.py -q
pytest tests/integration/test_prompt_trace_rejection.py tests/integration/test_turn_transaction_pending.py tests/integration/test_prompt_debug_runtime_lifecycle.py -q
pytest tests/integration/test_prompt_trace_actual_usage.py tests/integration/test_turn_transaction_security.py -q
```
