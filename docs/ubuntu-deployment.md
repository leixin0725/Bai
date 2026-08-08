# Ubuntu 部署手册

Bai Agent 的主要开发和部署环境是 Ubuntu 24.04。项目支持 Python 3.12、3.13 和 3.14；Ubuntu 24.04 自带的 Python 3.12 即可运行，不要求额外 PPA。

## 1. 系统准备

```bash
sudo apt-get update
sudo apt-get install -y git python3 python3-venv
```

把仓库放在 Linux 文件系统中，例如 `$HOME/projects/bai-agent`。不要把长期开发工作区放在 `/mnt/c`、`/mnt/d` 等 Windows 挂载目录中，否则文件权限、大小写语义和 I/O 性能都不能代表实际 Linux 部署环境。

首次安装运行依赖：

```bash
cd "$HOME/projects/bai-agent"
bash scripts/bootstrap-ubuntu.sh
```

开发机器需要测试依赖时改为：

```bash
bash scripts/bootstrap-ubuntu.sh --dev
```

脚本只接受 Python 3.12-3.14，在仓库内创建 `.venv`，并以 editable 模式安装项目。可通过 `PYTHON_BIN=python3.13` 选择已安装的其它解释器。

## 2. 配置与验证

配置保存在版本控制内的 `config/`，运行记忆保存在被 Git 忽略的 `data/memory/`。API Key 只能由秘密管理器、进程环境变量或 `start.sh` 的隐藏输入提供。

```bash
DEEPSEEK_API_KEY=invalid-placeholder-only .venv/bin/python -m bai_agent config validate --config-dir config
.venv/bin/python -m bai_agent --config-dir config --data-dir data memory validate
.venv/bin/python -m bai_agent --config-dir config --data-dir data doctor
```

占位值只满足配置引用检查，不会被保存；以上命令不会调用真实 Provider。开发环境还应执行：

```bash
.venv/bin/python -m pytest
git diff --check
```

## 3. 启动

```bash
bash start.sh
```

脚本会从自身位置定位项目根目录，使用 `.venv/bin/python`。无参数时默认进入 `chat`，并在未设置 `DEEPSEEK_API_KEY` 时通过交互式终端隐藏读取；`chat` 之外的命令不需要 API Key，直接透传运行。`start.sh` 是 `bai-agent` 的透传壳，任何 `bai-agent` 参数都可以手动传入，例如：

```bash
bash start.sh --debug-prompts
bash start.sh --resume-pending
bash start.sh --discard-pending
bash start.sh doctor
bash start.sh memory validate
bash start.sh --config-dir custom --data-dir custom chat
```

没有显式命令时脚本会自动补 `chat`，所以 `bash start.sh --debug-prompts` 等价于 `bash start.sh chat --debug-prompts`；`bash start.sh --help` 显示 `bai-agent` 的完整帮助。`--resume-pending` 与 `--discard-pending` 互斥，且会在读取凭据前拒绝。若由外部秘密管理器启动，也可直接向该进程注入 `DEEPSEEK_API_KEY`；不要把值写进脚本、配置或 shell 历史。

## 4. 数据、权限与备份

应用会把记忆目录收紧为 `0700`、文件收紧为 `0600`。部署用户应独占项目和数据目录，且不要用 root 运行 Agent。

升级或迁移前先停止 Agent，然后备份整个 `data/memory/`。恢复后必须先运行 `memory validate` 和 `doctor`。`.venv/`、`.pytest_cache/`、`.hypothesis/`、`.tmp/`、`__pycache__/`、`build/` 与 `dist/` 都是可重建内容，不应跨机器搬运。

## 5. 更新与迁移到其它 Ubuntu 机器

1. 在目标机器安装 `git`、`python3` 和 `python3-venv`。
2. 复制或克隆仓库，保留 `.git`；单独安全复制 `data/memory/`，不要复制旧 `.venv/`。
3. 在目标仓库运行 `bash scripts/bootstrap-ubuntu.sh`，需要开发测试时增加 `--dev`。
4. 运行配置、记忆和 doctor 三项离线校验。
5. 只在可信交互终端通过 `bash start.sh` 启动。

代码、配置和文档只使用项目相对路径；机器专属位置由部署者选择，不需要修改源码。
