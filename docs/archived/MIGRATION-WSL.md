# Bai WSL 迁移记录（2026-08-03）

> [2026-08-10] 归档说明：本项目已移除原生 Windows 支持，仅支持 Ubuntu/WSL Linux；本文保留为历史迁移记录，其中关于 Windows 兼容矩阵与 `start.ps1` 的描述不再代表当前支持范围。

本文记录 Bai Agent 迁移到 Ubuntu WSL 后的权威位置、数据完整性证据、Linux 适配内容和复验方法。后续开发以 Linux 工作树为准。

## 1. 迁移结果

| 项目 | 结果 |
| --- | --- |
| Linux 项目根目录 | `/home/leixin/Dev_project/Bai` |
| Windows 文件管理器入口 | `\\wsl.localhost\Ubuntu\home\leixin\Dev_project\Bai` |
| 父目录布局 | `Bai/` 与 `qqbot/` 并列，父目录没有散落的 Bai 文件 |
| 运行用户 | `leixin` |
| 操作系统 | Ubuntu 24.04.4 LTS |
| Python | 3.12.3，项目支持 3.12/3.13/3.14 |
| 安装方式 | 项目内 `.venv`，editable install |
| Git | 完整保留 `.git`、`main` 分支和既有历史 |

迁移包含源代码、配置、文档、测试、Git 历史和被 Git 忽略的真实 `data/memory/`。没有复制 Windows `.venv`、测试缓存、Hypothesis 数据库、临时目录、字节码、构建产物或 `.env`；这些内容必须在目标机器重建或重新注入。

## 2. 为 Linux 部署完成的适配

- `pyproject.toml` 支持 Python 3.12-3.14，Ubuntu 24.04 可直接使用系统 Python 3.12。
- `scripts/bootstrap-ubuntu.sh` 检查 Python 范围、创建 `.venv`，并按运行或开发模式安装依赖。
- `start.sh` 从脚本位置解析项目根目录，隐藏读取 `DEEPSEEK_API_KEY`，并安全透传 debug、resume 和 discard 参数。
- CI 的 Ubuntu 与 Windows 功能矩阵覆盖 Python 3.12/3.13/3.14；Python 3.13 继续承担固定性能门禁。
- [Ubuntu 部署手册](docs/ubuntu-deployment.md)记录新机器安装、升级、备份、权限和验证流程。
- `.gitattributes` 固定文本文件使用 LF，避免从其它平台引入混合换行。

## 3. 真实记忆完整性

迁移前后以下三个文件的 SHA-256 逐一一致：

```text
a77d1292ce184ddf4f3d4e68764ddb0c3fa92d72b472af3879297c80247b7e7f  data/memory/.state/long_term.last-valid.yaml
a77d1292ce184ddf4f3d4e68764ddb0c3fa92d72b472af3879297c80247b7e7f  data/memory/long_term.yaml
26f5d2ab331671e25b2b7345571564f0bb52b093b5be4215a5e8a05a028adde5  data/memory/raw/00000001.jsonl
```

总计 3 个文件、28,078 字节。目标侧 `data/` 与记忆目录权限为 `0700`，文件权限为 `0600`。应用级验证结果：

```text
raw_records=34
long_term_items=5
coverage_spans=2
coverage_gaps=0
dangling_sources=0
curated_through_sequence=20
direct_range=21..34
```

## 4. 验证证据

迁移后的 Ubuntu 原生 `.venv` 执行全量离线测试：

```text
357 passed, 3 skipped in 41.02s
```

三项跳过均为预期平台门禁：Windows PowerShell 参数绑定、Ubuntu/Python 3.13 专属 TUI 性能门禁、显式启用的 Windows 启动性能参考。以下检查也已通过：

- `python -m bai_agent --help`；
- `config validate`（只注入无效占位凭据，不发起网络请求）；
- 真实数据 `memory validate`；
- `doctor`，且 `network_probe=false`；
- `security incident check`，无开放事件；
- Linux `start.sh --help`、Bash 语法和互斥 pending 参数门禁；
- 旧 Windows 绝对路径扫描无结果。

## 5. 后续使用

在 WSL 终端中：

```bash
cd /home/leixin/Dev_project/Bai
bash scripts/bootstrap-ubuntu.sh --dev
DEEPSEEK_API_KEY=invalid-placeholder-only .venv/bin/python -m bai_agent config validate --config-dir config
.venv/bin/python -m bai_agent --config-dir config --data-dir data doctor
bash start.sh
```

迁移到其它 Ubuntu 机器时只复制或克隆仓库，并安全复制 `data/memory/`；不要复制 `.venv/`。随后按[Ubuntu 部署手册](docs/ubuntu-deployment.md)重建环境并复验。新开发应在 Linux 文件系统内完成，不应把 `/mnt/c` 或 `/mnt/d` 作为日常工作区。
