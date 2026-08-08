# Quickstart: 核心运行时与消息管道（阶段 0、1）

> 2026-08-08。本文给出阶段 1 的可运行验证路径；契约细节见 [contracts/](./contracts/)，实体见 [data-model.md](./data-model.md)。验证前先按 [specs/001 安装说明](../../specs/001-persistent-memory-agent/quickstart.md) 准备开发环境并注入 `DEEPSEEK_API_KEY`（或使用测试夹具，见第 6 节）。

## 1. 管道输入合并（一次输入动作）

前置：空数据目录（避免既有记录干扰计数）。

```bash
.venv/bin/python -m bai_agent --config-dir config --data-dir .tmp/pipeline-merge memory reset all
printf '第一行\n第二行\n第三行\n' | .venv/bin/python -m bai_agent --config-dir config --data-dir .tmp/pipeline-merge chat
.venv/bin/python -m bai_agent --config-dir config --data-dir .tmp/pipeline-merge memory validate
```

预期：管道整批输入只产生一次处理；`memory validate` 中 `raw_records` 相比启动前增加 **2**（一条 USER + 一条 ASSISTANT），而不是 6；模型只回复一次，回复覆盖全部三行内容。

## 2. 会话内状态查看（`:status`）

```bash
.venv/bin/python -m bai_agent --config-dir config --data-dir .tmp/pipeline-status chat
```

交互式输入 `:status` 并回车。预期输出稳定 JSON，至少包含 `session_state`、`queue_depth`、`current_item_id`、`tasks`、`health`、`last_reload`、`pending_turn_id`、`counters` 与 `uptime_seconds`（示例见 [contracts/cli.md](./contracts/cli.md) 第 4 节）；该输入不写入 raw 记录。

## 3. 配置热重载与分组校验

```bash
.venv/bin/python -m bai_agent --config-dir config --data-dir .tmp/pipeline-reload chat
```

运行中修改 `config/history_timestamps.toml` 的 `long_gap_minutes` 并保存，然后发送任意一条消息；下一轮即按新值工作（提示构造与时间标记行为变化可对比前后两轮输出）。再把该文件改为非法值（如 `long_gap_minutes = 0`）并发送消息：

- 预期：发送消息时终端（stderr）**立即出现明确失败提示**，包含分组（`history_timestamps`）、字段（`long_gap_minutes`）、失败原因与系统继续使用的旧配置 revision；系统按旧快照继续处理该消息，不中断、不静默；
- 同一时刻 `:status` 显示 `health=warning`、`last_reload.ok=false`，且 `last_reload.error` 与终端提示中的分组/字段/原因一致；
- 修复文件后发送下一条消息：失败提示消失，`:status` 恢复 `health=ok`、`last_reload.ok=true`，无需重启。

启动时校验：

```bash
.venv/bin/python -m bai_agent config validate --config-dir config
```

预期成功输出新增 `groups` 字段，八个分组全部为 `ok`（见 [contracts/configuration.md](./contracts/configuration.md) 第 3 节）。

## 4. 优雅停止与 pending

```bash
.venv/bin/python -m bai_agent --config-dir config --data-dir .tmp/pipeline-stop chat
```

- 空闲时按 Ctrl+C：进程完成清理后退出，退出码 130；已完成的轮次不重发。
- 处理中按 Ctrl+C：当前轮按既有事务语义安全中止；若模型调用失败，raw 尾部保留唯一 USER pending；重启后默认丢弃或 `--resume-pending` 显式恢复，行为与既有契约一致。

## 5. 后台任务与事件投递

阶段 1 不提供面向用户的提交入口；最小执行器（提交/串行执行/状态记录）与定时/系统事件投递由自动化测试验收，供阶段 5/7 作为底座使用。相关测试见第 6 节。

## 6. 自动化验收

```bash
.venv/bin/python -m pytest tests/unit tests/contract tests/integration -q
```

新增重点用例（任务清单细化）：

- 管道：并发提交多条输入与事件，验证串行、顺序、防重入（BR-001/002）。
- 输入合并：管道 EOF 整批、TTY 缓冲连片、逐行三种场景（BR-006）。
- 执行器：等待→执行→成功/失败 状态机与积压（BR-004）。
- 生命周期：队列非空、pending 存在/不存在时停止，重启数据完整（BR-005）。
- 状态：`:status` 快照与真实状态一致，无重复计数（BR-007）。
- 配置：整份快照原子切换、分组错误定位、失败保持旧值（BR-003）。

全部通过后执行既有全量回归：

```bash
.venv/bin/python -m pytest
.venv/bin/python -m bai_agent config validate --config-dir config
.venv/bin/python -m bai_agent --config-dir config --data-dir .tmp/validation memory validate
```

## 7. 文档一致性

本功能为重大更新，README、`specs/001` 的 `contracts/cli.md`、`contracts/configuration.md`、`quickstart.md` 与本文档必须与最终实现一致，并与对应实现处于同一提交（见 [plan.md](./plan.md) Git Milestones）。
