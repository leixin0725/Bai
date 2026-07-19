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

## 记忆与安全

运行数据默认位于 `data/memory/`：`raw/*.jsonl` 是不可变原始记录，`long_term.yaml` 是可人工维护的长期记忆、来源索引、整理前沿和覆盖概览的共同事实来源。修改或恢复备份后先执行：

```powershell
python -m bai_agent --config-dir config --data-dir data memory validate
python -m bai_agent --config-dir config --data-dir data memory source mem-UUID
python -m bai_agent --data-dir data security incident check
```

程序会尽力把 POSIX 权限收紧到目录 `0700`、文件 `0600`，并在 Windows 检查和收紧 DACL；无法证明为私有时验证失败关闭。若凭据可能进入 Git、配置、日志或运行记忆，立即停止聊天/整理，并按[凭据泄露事件处置流程](docs/security-incident-response.md)完成轮换、全仓库与历史扫描、运行数据扫描和显式解除门禁。

## 验证

```powershell
pytest
python -m bai_agent config validate --config-dir config
python -m bai_agent --config-dir config --data-dir .tmp\validation memory validate
python -m bai_agent --data-dir .tmp\validation security incident check
```

Windows/Ubuntu/macOS × Python 3.13/3.14 的功能矩阵在 `.github/workflows/compatibility.yml`。Windows 参考启动性能门槛需显式启用，执行至少 100 次全新进程启动；详细安装、人工维护、备份、来源查询和性能复现实验见[功能 quickstart](specs/001-persistent-memory-agent/quickstart.md)。

> [2026-07-19] 本文与 `specs/001-persistent-memory-agent/` 的规格、计划、契约和验收任务同步。
