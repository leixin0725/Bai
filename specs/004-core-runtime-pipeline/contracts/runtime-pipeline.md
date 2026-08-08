# Contract: 运行时管道、输入合并、后台执行器与状态

> 2026-08-08。本契约描述阶段 1 的进程内运行时行为；外部 CLI 合同见 [cli.md](./cli.md)，配置合同见 [configuration.md](./configuration.md)。

## 1. 统一串行管道

- 所有工作单元以 `PipelineItem` 进入同一个 FIFO；单 worker 按 `sequence` 顺序逐个处理。
- 同一时刻至多一个处理项在运行：对话输入处理期间到达的事件与输入进入等待，不得并发处理同一会话（BR-002）。
- 处理项结果与顺序可观测：`RuntimeStatus.current_item_id`、`queue_depth` 与 `counters` 在事件边界更新。
- 处理项失败语义：
  - `chat_input`：沿用控制器事务与 pending 协议（用户输入落盘 → 模型失败 → 唯一 USER pending；重试/丢弃策略不变）。
  - 事件处理函数失败：作为该事件的结果记录到统计，不得中断后续处理项。

## 2. 输入合并（一次输入动作）

- stdin 读取器逐行读取；合并判定**只基于缓冲区非空**，零等待、无时间阈值（BR-006）。
- 管道输入（stdin 非 TTY）：整批内容（直至 EOF）为一次 `ConversationAction`，只产生一次处理与一条回复。
- 交互式 TTY：读完一行后若缓冲区仍有已到达数据，继续读入同一动作；缓冲区空时立即提交当前动作。
- 单次动作内容不截断、不按行拆轮；逐行输入（缓冲区空）每行独立成动作。
- TUI 等独占终端的组件运行期间，输入源可被暂停：暂停即移除 fd 监听，独占方（Textual 驱动）可安全接管同一 stdin；恢复后重新监听并继续原合并语义，已缓冲字节不丢失、不误报 EOF。
- Windows 若无法对 stdin 做非阻塞判定，文档化降级为逐行处理；Ubuntu 主平台必须实现合并。

## 3. 最小后台执行器

- `submit(name, coro)` 按提交顺序排队；同一进程内串行执行。
- 状态只允许 `waiting → running → success/failure`；失败保留原因；无取消、无重试、无持久化（FR-005/BR-004）。
- 任务记录保留在本进程内，供 `RuntimeStatus.tasks` 查看；进程退出即消失，不承诺跨重启恢复。

## 4. 事件投递

- 管道接受 `timer_event` 与 `system_event` 处理项；处理函数按 `event_kind` 注册，默认无处理函数（阶段 1 无内置调度器）。
- 事件处理函数为 `async callable(payload) -> None`；注册表只做精确匹配，不做中间件/过滤器。
- 测试与后续阶段（阶段 7 调度器、阶段 8 钩子）通过同一 `submit` 入口投递。

## 5. 生命周期

- 启动：获取单写者锁与配置验证成功后进入循环；`--resume-pending` 的恢复轮作为首个 `chat_input` 处理项。
- 停止：收到 SIGINT/SIGTERM 后置 `stopping`，不再接收新处理项；当前处理项按既有事务/pending 语义结束或安全中止；释放写锁后退出。
- 第二次 SIGINT：立即中止当前处理项（保持现有 KeyboardInterrupt → 130 语义）。
- 退出码：SIGINT 为 130，SIGTERM 为 0；其他启动/校验错误沿用现有错误码。

## 6. 状态快照（`:status`）

- `RuntimeStatus` 至少包含：`session_state`、`queue_depth`、`current_item_id`、`tasks`、`health`、`last_reload`、`pending_turn_id`、`counters`、`uptime_seconds`。
- `health=warning` 当且仅当最近一次配置重载失败或有后台任务失败；恢复条件为一次成功重载/新任务成功。
- 快照不含正文、工具参数、记忆内容或凭据（BR-007）。

## 7. 配置重载失败可见性

- 每个对话动作开始前的配置重载失败：MUST 先向 stderr 输出明确警告（分组、字段、失败原因与继续使用的配置 revision），再按旧快照继续处理该动作；MUST NOT 吞掉异常或静默回退。
- 警告内容与 `RuntimeStatus.last_reload` 必须一致：`ok=false`、`error` 含同一分组/字段/原因、`revision` 为继续使用的旧 revision（BR-003）。
- 修复文件后下一个动作重载成功：警告消失，`last_reload.ok=true`，无需重启。
