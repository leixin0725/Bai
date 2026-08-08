# Research: 核心运行时与消息管道

> 2026-08-08。研究方式：直接审阅仓库内 `application.py`、`cli.py`、`config/loader.py`、`runtime/controller.py`、`domain/ports.py`、`tests/` 与既有 specs/001~003 契约；本功能不引入任何新外部 API，因此无需联网调研外部服务协议。结论全部复用仓库内已验证的模式。

## 1. 事件架构：统一串行管道，而非事件总线

- **Decision**: 新增进程内 FIFO 管道，单 worker 串行消费；对话输入、定时事件、系统事件统一经 `submit` 进入，按到达顺序处理。
- **Rationale**: 现有 `chat` 是 `for line in sys.stdin: asyncio.run(run_turn(...))`，天然串行但每次重建事件循环，且没有事件入口、状态与停止语义。单 worker 队列直接满足"统一进入、防重入、顺序可观测"，代码量最小。
- **Alternatives considered**:
  - 完整发布/订阅事件总线：为阶段 8 预留过多间接层，用户明确否决。
  - 保留 `asyncio.run` 逐轮调用 + 外部协调：无法表达"等待中的输入"与统一生命周期，且每轮重建循环与 TUI 审批运行方式不兼容。

## 2. 输入合并：零等待缓冲区判定

- **Decision**: 输入读取器逐行读取 stdin；读完一行后**非阻塞检查缓冲区**（如 `select` 0 超时/`peek`），若仍有已到达的行则继续读入当前动作；缓冲区空时提交整批为一次对话动作。管道输入以 EOF 为整批边界；TTY 逐行输入在缓冲区空时各自成动作。
- **Rationale**: 不依赖时间阈值（用户澄清）；"一次粘贴的多行"在终端层表现为连片到达并驻留缓冲区，零等待判定即可合并，慢速逐行输入不受影响；管道整批是自动化场景的明确边界。
- **Alternatives considered**:
  - 时间窗口/防抖合并：用户明确否决，且会人为延迟逐行输入。
  - 依赖 readline bracketed paste：跨平台行为不一致，且为次要体验增加复杂度。

## 3. 配置重载：整份快照原子切换 + 分组校验定位

- **Decision**: 沿用现有 `AgentApplication._reload_config()` 的"每轮加载整份 `ConfigSnapshot`，校验通过后原子替换控制器"；新增**配置分组表**（每个 TOML 清单 + 人格/提示文件为一个分组）用于校验与错误定位；`config validate` 输出各分组状态；`:status` 暴露最近一次重载的 revision、结果与错误。任一分组非法时整体保持最后有效快照，运行不中断。
- **Rationale**: 现有实现已满足"运行中修改立即在下一轮生效"与"非法配置保持旧值不中断"；分组只做可观测性与错误定位，不做跨组部分合并，避免半新半旧的不一致状态。这是对 BR-003 的最小可辩护解释。
- **Alternatives considered**:
  - 跨组部分合并生效（每组合并最新有效值）：需要重构 `load_config` 与控制器重建为可组合装配，跨文件引用（states→personas、providers→profiles）会引入不一致窗口；复杂度与风险不成比例，暂不采用（若用户要求，需先修订计划）。
  - 新增配置监听/守护线程：单用户 CLI 无必要，下一轮边界重载已满足需求。

## 4. 后台任务：最小串行执行器

- **Decision**: `runtime/executor.py` 提供 `submit(name, coro)`，同一进程内按提交顺序串行执行；`TaskRecord` 记录 等待/执行/成功/失败 与原因；失败不自动重试、不可取消、不持久化。
- **Rationale**: 阶段 1 没有真实后台负载（记忆整理/夜间维护在阶段 5/7）；最小执行器满足清单"排队、执行、状态查看"且是阶段 5/7 的直接底座。
- **Alternatives considered**:
  - 完整任务队列（优先级/取消/持久化/重试）：超前设计，用户明确否决。
  - 本阶段不做任务机制：会让"状态查看"与统一管道验收缺少对象，且后续阶段仍需回填，收益低。

## 5. 运行状态查看：会话内 `:status`

- **Decision**: `chat` 会话内拦截精确输入 `:status`，输出稳定 JSON：会话状态（idle/processing/stopping）、队列深度、当前处理项、后台任务、健康度、最近配置重载与处理计数。
- **Rationale**: 单写者锁下第二个进程无法并发读取实时状态；会话内命令是零新增外部接口的最小路径。`:status` 不会被写入 raw 记录，不进入模型调用。
- **Alternatives considered**:
  - 独立 `status` 子命令：与单写者锁冲突，只能读持久化文件（本功能任务不持久化），不可行。
  - Web/UI：明确不做。

## 6. 生命周期：信号驱动的优雅停止

- **Decision**: 运行时外壳持有单次循环；SIGINT（Ctrl+C）与 SIGTERM 触发"停止接收新工作 → 当前处理项按既有事务/pending 语义结束或中止 → 释放写锁 → 退出"；SIGINT 退出码保持 130，SIGTERM 为 0；第二次 SIGINT 立即中止（保持现有关键词中断语义）。
- **Rationale**: 保持既有 CLI 行为（Ctrl+C=130、pending 安全落盘）不变，只把"逐轮 asyncio.run"改为持久循环，停止路径显式化。
- **Alternatives considered**:
  - 强制取消当前轮：破坏既有 pending 与事务协议，否决。
  - 依赖默认 KeyboardInterrupt 传播：无法保证释放资源顺序，否决。

## 7. 定时/系统事件：仅投递入口与测试钩子

- **Decision**: 管道支持 `TimerEvent`/`SystemEvent` 处理项与按 kind 注册的处理函数（默认空）；本阶段不建调度器，测试与后续阶段通过同一入口投递。
- **Rationale**: 满足"定时事件、系统事件统一进入处理流程"的契约面，同时不给阶段 7 提前建调度器。
- **Alternatives considered**: 直接在阶段 1 建完整调度器（持久化/周期/自主执行）：属于阶段 7，否决。

## 8. 依赖与平台

- **Decision**: 不新增依赖；asyncio 标准库；Ubuntu 24.04 主支持，Windows 次要兼容（`select` 在 Windows 上不支持 stdin 时，输入读取器使用等价的非阻塞读取路径或文档化降级为逐行）。
- **Rationale**: 项目约束与兼容矩阵（`.github/workflows/compatibility.yml`）要求 Windows 功能可验收；输入合并的零等待判定在 Windows 控制台可用 `msvcrt` 等价实现，或在该平台明确降级为逐行并在文档记录。
- **Alternatives considered**: 引入 `selectors`/第三方输入库：不必要的依赖。
