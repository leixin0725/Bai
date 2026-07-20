# Bai Agent

Bai Agent 是一个 Python 单用户聊天 Agent。它不创建或切换“对话”：每次启动都从同一份永久原始记录、近期直接上下文和长期记忆继续工作。

当前实现包括：

- DeepSeek API 适配器，以及可替换的模型 Provider 接口；
- 永久分段 JSONL 原始记录、可直接编辑的长期 YAML 和同修订版 `MemoryCoverageOverview`；
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

聊天中断且已有用户输入落盘时，普通启动会报告 pending turn。确认重试同一轮：

```powershell
python -m bai_agent --config-dir config --data-dir data chat --resume-pending
```

## 提示词调试批准

仅在本地交互式 TTY 中按当前进程启用：

```bash
python -m bai_agent --config-dir config --data-dir data chat --debug-prompts
```

每个聊天、记忆整理、工具续接与 provider retry 都通过唯一 `ModelCallGateway`，在 DeepSeek `prepare()` 和唯一 `materialize_sdk_kwargs()` 后展示最终模型可见字段、完整正文及 `[config_file]`、`[data_file]`、`[runtime]`、`[generated]` 来源。界面会提醒私人记忆只在本机临时显示；按 `A` 逐请求批准，按 `R`/`Esc` 明确拒绝并无痕撤销整轮。批准绑定 call、attempt 与物化载荷摘要，不修改请求；批准后、网络发送前 TUI 清除正文和来源，`send_once()` 无论成功或失败都在 `finally` 释放 sender 载荷。

明确拒绝不会形成历史或 pending；已经批准的请求若因普通 provider/网络错误且重试结束，则只发布一条 USER pending，可用 `--resume-pending` 恢复。该模式默认关闭、退出即失效，也不会保存原始追踪。stdin/stdout 任一不是 TTY 时以 `DEBUG_TTY_REQUIRED` 在任何持久化和模型发送前失败。

同一轮的 memory curation → chat → tool continuation 按网关分配的严格 call sequence 逐项出现；provider retry 保持逻辑 call 身份，但以新的 attempt、状态和批准项展示，前一项未决定时不会处理后一项。交互 TTY 的稳定色板只增强来源类别，`NO_COLOR=1` 或 `debug_prompt.color="never"` 时仍保留 `[config_file]`、`[data_file]`、`[runtime]`、`[generated]`、分组边界与缩进。输出重定向不是无色模式，会按非 TTY 规则失败。

## 记忆与安全

运行数据默认位于 `data/memory/`：`raw/*.jsonl` 是不可变原始记录，`long_term.yaml` 是可人工维护的长期记忆、来源索引、整理前沿和覆盖概览的共同事实来源。修改或恢复备份后先执行：

```powershell
python -m bai_agent --config-dir config --data-dir data memory validate
python -m bai_agent --config-dir config --data-dir data memory source mem-UUID
python -m bai_agent --config-dir config --data-dir data memory reset long-term
python -m bai_agent --config-dir config --data-dir data memory reset all
python -m bai_agent --data-dir data security incident check
```

`memory reset long-term` 保留永久原始聊天和近期窗口，只清空长期派生正文；`memory reset all` 清空全部聊天与长期记忆并恢复首次启动状态。两条命令立即执行且不可撤销，运行前必须先关闭聊天进程；安全事件状态不会随记忆重置而删除。

程序会尽力把 POSIX 权限收紧到目录 `0700`、文件 `0600`，并在 Windows 检查和收紧 DACL；无法证明为私有时验证失败关闭。若凭据可能进入 Git、配置、日志或运行记忆，立即停止聊天/整理，并按[凭据泄露事件处置流程](docs/security-incident-response.md)完成轮换、全仓库与历史扫描、运行数据扫描和显式解除门禁。

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

Windows/Ubuntu/macOS × Python 3.13/3.14 的功能矩阵在 `.github/workflows/compatibility.yml`。Windows 参考启动性能门槛需显式启用，执行至少 100 次全新进程启动；详细安装、人工维护、备份、来源查询和性能复现实验见[功能 quickstart](specs/001-persistent-memory-agent/quickstart.md)。

> [2026-07-19] 本文与 `specs/001-persistent-memory-agent/` 的规格、计划、契约和验收任务同步。
