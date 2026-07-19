# Contract: CLI 与类 TUI 批准界面

## 启动命令

```powershell
python -m bai_agent --config-dir config --data-dir data chat --debug-prompts
```

- `--debug-prompts` 仅属于 `chat` 子命令，只对当前进程有效。
- 未提供参数时调试关闭；配置文件不得启用或记住该状态。
- 进程运行中不提供切换命令；重启时必须重新显式传参。
- `start.ps1` 若提供对应参数，只做安全透传，不把状态写入环境持久区或配置。

## 终端能力门禁

调试启动后，在读取并暂存第一条用户输入、构建请求或调用 provider 之前检查：

- stdin `isatty()` 为真；
- stdout `isatty()` 为真；
- Textual 能进入 application mode。

任一不满足时：

- 输出不含提示正文的 `DEBUG_TTY_REQUIRED` 可操作错误；
- 退出码为 `2`；
- provider 调用、raw 归档、长期记忆和事务日志变更均为 0；
- 不退化为普通打印、不自动批准。

该门禁不做平台替代：原生 Ubuntu 24.04 是主要支持环境，Windows 11/PowerShell 是次要功能兼容环境，macOS 不在范围内。

## 界面生命周期

每个物理模型 attempt 对应一个短生命周期 approval app：

1. 进入全屏 application mode。
2. frozen request、来源和估算已就绪后，在 Ubuntu 24.04/Python 3.13/80×24 `xterm-256color` 的 30 次同进程启动中，以 p95≤500 ms 完成标题、调用身份和上下文摘要 mounted；首次冷启动单独记录且不纳入门禁。
3. 正文与所有来源加载完成后才启用批准操作。
4. 用户 approve/reject。
5. 退出 app，恢复进入前终端。
6. approve 时在网络发送前清除 TUI 的正文/来源/PreparedProviderRequest 引用，再把同一个不可变 `MaterializedSendPayload` 交给 sender；reject 时触发整轮回滚且不形成 pending。
7. sender 在 `send_once` 成功或失败后的 `finally` 释放 payload；普通 provider/网络失败若重试结束，由事务层发布一条 USER pending。

provider 响应后的实际用量使用不含原文的普通聊天输出摘要显示，不重新打开或重建已发送 prompt trace；`ActualUsageSummary` 不引用 prompt、payload、part、SourceRef 或 TUI 对象。

## 布局契约

```text
┌ 调用 2 / attempt 1 ─ chat · persona=chat · state=default ┐
│ provider=deepseek · model=... · config=<revision>          │
│ ⚠ 本地界面可能显示私人记忆；不会保存原始追踪             │
├ 上下文 ────────────────────────────────────────────────────┤
│ 输入≈... + 输出预留... = 峰值... / 容量... (...%) [状态]   │
├ 最终请求 / 来源（可滚动）──────────────────────────────────┤
│ [messages/0/system] [included] [trusted]                   │
│   正文……                                                   │
│   来源 config_file: config/personas/chat.md @ <revision>   │
│ [messages/1/user] [included] [untrusted]                   │
│   正文……                                                   │
│   来源 runtime:user_input turn=<id> record=<id>            │
├ 操作 ───────────────────────────────────────────────────────┤
│ [A] 批准并发送                         [R] 拒绝并撤销整轮   │
└─────────────────────────────────────────────────────────────┘
```

实际控件可按终端宽度纵向重排，但不得隐藏以下字段：

- turn/flow/call sequence/purpose/persona/state/provider/model/config revision/attempt/status；
- 最终 provider payload 的全部提示承载字段；
- included part 的正文、顺序、trust、来源数量和全部来源；
- excluded/empty/unknown_source 的状态与原因；
- 输入总估算、每段估算、协议开销、最大输出预留、峰值、容量、占比、剩余和风险；
- approve/reject 的明确后果。

## 操作语义

| Input | Semantic |
|---|---|
| `A` 或点击“批准并发送” | 仅在完整渲染/校验后 approve 当前 attempt |
| `R`、`Esc` 或点击“拒绝并撤销整轮” | reject 当前 attempt，provider 不发送，整轮回滚并返回聊天输入 |
| `Ctrl+C` | 等同 reject 当前 attempt；完成/安排可恢复回滚后退出进程，退出码 130 |
| EOF/终端丢失 | 不 approve；按 presentation failure 安全阻断并使事务恢复可收敛 |

`Enter` 不作为默认批准键，避免滚动或焦点操作造成误发。界面不得设置倒计时或默认选择 approve。

## 颜色与无颜色

- `auto` 时仅在终端明确支持颜色且未设置标准 `NO_COLOR` 环境变量时使用颜色。
- `always` 仅影响交互式 TUI 的样式，不能绕过 TTY 门禁。
- `never` 和自动降级都保留 `[included]`、`[runtime]`、`[config_file]`、调用序号、边界和缩进。
- 用户正文中的 ANSI escape/control 字符按可见转义或安全文本渲染，不能改变边界、颜色或伪造标签。

## 隐私与清理

- 每次以调试参数启动都显示私人记忆本地暴露提醒，首个 approval app 中保持可见。
- TUI 不支持复制到持久 trace、导出、历史回看或重发。
- approve 且 app 退出后，presenter 的 prompt/provenance/PreparedProviderRequest/Rich renderable 引用应清空；sender 仅保留同一个不可变 materialized payload，并在发送成功或失败后的 `finally` 释放。
- 错误、traceback 和日志只输出 call id、错误码及脱敏指引，不回显正文。

## TUI 合同测试

- Textual Pilot 覆盖批准、拒绝、Esc、Ctrl+C、滚动、80x24、窄宽度、resize、无色与控制字符。
- 在正文尚未完整 mounted/validated 时触发 A，发送次数必须为 0。
- stdout/stdin 非 TTY、重定向及 app 初始化失败均安全失败。
- app 退出后断言 widget/renderable/trace registry 不再引用正文和来源。
- `send_once` 成功和失败后断言 sender 不再引用 materialized payload；actual usage 普通输出不得重建 TUI 或原文。
