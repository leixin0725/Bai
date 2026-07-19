# Quickstart: 实现与验证持久记忆聊天 Agent

> 本文描述计划完成后的开发/验收路径；当前 `/speckit-plan` 阶段尚未生成实现代码。

## 1. 开发环境

PowerShell：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Linux/macOS：

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

实际依赖版本由 `pyproject.toml` 和锁文件固定；不得在业务模块内按需安装依赖。

## 2. 配置检查

所有提示词和可变参数在 `config/`。先检查至少存在：

```text
config/agent.toml
config/providers.toml
config/states.toml
config/tools.toml
config/logging.toml
config/personas/chat.md
config/personas/memory_curator.md
config/personas/states/default.md
config/prompts/*.md
```

API Key 由进程环境或秘密管理器注入。下面只有明显不可用的占位说明，不要把真实值写进命令历史、配置、人格或记忆文件：

```powershell
$env:DEEPSEEK_API_KEY = "<由秘密管理器注入的值>"
```

验证配置：

```powershell
python -m bai_agent config validate --config-dir config
python -m bai_agent doctor
```

预期：输出配置 revision、`default` 状态、聊天/整理模型 profile 和唯一内置工具 `memory_source_query`；不输出 Key 值或提示正文。

## 3. 自动化测试

不需要真实 DeepSeek 凭据的默认测试：

```powershell
pytest
pytest tests\unit tests\contract
pytest tests\integration tests\fault_injection
pytest tests\performance -m performance
```

关键门禁：

- BR-001—BR-018 的成功、边界和关键失败路径均有测试。
- 用户输入写入发生在 Provider 替身被调用前；Assistant 输出写入发生在 stdout 替身收到文本前。
- 窗口阈值前整理调用为 0；到边界后按最旧完整轮次批量整理。
- 整理/Schema/YAML 写入任一步失败时，`curated_through_sequence` 和直接注入范围不变。
- 每个临时写、fsync、replace 故障点恢复后只有完整旧状态或完整新状态。
- 多人格同参调用来源工具得到相同有序结果，调用前后权威文件哈希不变。
- 默认自主循环调用 Provider/Tool 次数为 0。
- 测试凭据不出现在 data、stdout/stderr、日志、提示追踪或 Git diff。

真实 DeepSeek smoke test 必须通过显式 marker/环境开关运行，不进入默认 CI，且只使用隔离测试数据目录和最小 token 配额。

## 4. 首次聊天与跨启动连续性

```powershell
python -m bai_agent memory validate
python -m bai_agent chat
```

输入两条可核对信息，使用 EOF/Ctrl+C 退出，再运行同一命令继续提问。预期：

- 没有创建/选择会话的交互；
- `data/memory/raw/` 中用户和 Assistant 记录按一个全局序列增长；
- 重启后直接继承最近原文与已有长期记忆；
- Assistant 文本只在记录完整落盘后显示。

在模型调用期间模拟网络失败后，原用户输入仍存在；重启时程序报告 pending turn。只有显式运行以下命令才重试同一轮，且不会重复写用户记录：

```powershell
python -m bai_agent chat --resume-pending
```

## 5. 验证窗口整理

使用专用测试配置目录，把窗口、保留量和批次值调小；不要改 Python 常量：

```powershell
python -m bai_agent --config-dir tests\fixtures\config-small-window --data-dir .tmp\memory-window chat
```

持续输入直到一批最旧完整轮次即将离开直接注入窗口。预期：

1. 阈值前不调用 `memory_curator`。
2. 边界处由 `config/personas/memory_curator.md` 批量生成结构化候选。
3. `long_term.yaml` 中新项含一个或多个有效 `source_refs`。
4. `curation.curated_through_sequence` 与新记忆/来源在同一次 revision 中推进。
5. JSONL 原始记录数量和内容哈希不减少。

用故障注入配置令整理模型或 YAML replace 失败；本轮应在聊天 Provider 调用前停止，前沿不变。恢复故障后重试才允许修剪直接注入范围。

## 6. 验证来源查询

从 `long_term.yaml` 选择一个 `memory_id`：

```powershell
python -m bai_agent memory source mem-EXAMPLE
```

预期按 `global_sequence` 返回全部来源（必要时分页），而不是暴露文件路径。相同 ID/游标通过聊天人格、整理人格和测试辅助人格调用 `memory_source_query` 时，结果顺序、权限和错误码相同。

未显式调用工具时，长期记忆的来源原文自动注入数量必须为 0；调用结果只存在于发起调用的当前 flow。

## 7. 人工维护长期记忆

1. 停止 Agent。
2. 备份整个 `data/memory/`。
3. 用普通文本编辑器修改 `data/memory/long_term.yaml`。
4. 人工新增项使用 `created_by: manual`，并引用至少一条真实原始记录。
5. 不直接修改 `curation` 系统字段。
6. 执行：

```powershell
python -m bai_agent memory validate
```

有效修改在下次启动生效，并尽量保留 YAML 注释/顺序。无效来源、重复 ID、关系环或非法前沿应失败，原文件不被覆盖；程序只读回退 last-valid，并阻止自动整理直到修复。

## 8. 更换人格和 Provider

更换聊天或整理人格只编辑各自 Markdown 或 `agent.toml` 引用，再运行 `config validate`。历史记忆不得随人格变化而重写。

切换 DeepSeek 模型只编辑 `providers.toml` 的 model profile。新增其他供应商时实现 `ModelProvider` adapter 并复用契约测试；不得让新的 SDK 对象进入 Controller、Memory 或 Tool 层。

## 9. 性能验收

性能 fixture 生成 10,000 条原始记录和 1,000 条长期记忆，随后在目标 Windows 环境执行至少 20—30 次全新进程启动：

```powershell
pytest tests\performance\test_startup.py -m performance
```

预期 p95 在 3 秒内使首轮记忆可用。报告分别列出配置读取、JSONL 索引、YAML 解析和来源校验耗时；启动阶段不得发 DeepSeek 网络请求。只有实测失败后才考虑可重建索引缓存，缓存不能成为权威数据。

## 10. 提交门禁

每个重大里程碑提交前执行：

```powershell
pytest
python -m bai_agent config validate --config-dir config
python -m bai_agent memory validate
git diff --check
git status --short
```

再运行仓库采用的秘密扫描器，确认配置、人格、记忆 fixture、日志和测试中没有可用凭据。只 stage 当前里程碑文件；用户已有或无关的未跟踪文件不得混入提交。
