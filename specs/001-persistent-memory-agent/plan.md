# Implementation Plan: 持久记忆聊天 Agent

**Branch**: `001-persistent-memory-agent` | **Date**: 2026-07-19 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-persistent-memory-agent/spec.md`

## Summary

实现一个面向单用户的 Python CLI 聊天 Agent。程序不创建会话或聊天线程，而是跨启动维护一个连续记忆空间：原始用户/Agent 记录永久明文归档，近期记录直接注入模型上下文；只有记录即将移出直接注入窗口时，才由独立的记忆整理人格通过一次结构化响应批量产生长期记忆候选与 `MemoryCoverageOverview` 更新，并将概览、来源索引、长期记忆和整理前沿在同一 `long_term.yaml` revision 原子提交。

核心业务通过领域模型和端口隔离文件存储、模型供应商、提示组装、工具、状态解析与运行控制。首个模型适配器接入 DeepSeek 的 OpenAI 兼容接口；首版仅启用 `default` 状态、内置只读记忆来源查询工具和单轮控制器。所有提示词、模型名、URL、窗口/预算、重试和扩展开关均放在 `config/`，Python 代码不保存这些可变值。

## Technical Context

**Language/Version**: Python 3.13（同时在 Python 3.14 上运行兼容测试）

**Primary Dependencies**: `openai`（仅限 DeepSeek 适配器）、`pydantic`（配置与领域边界校验）、`ruamel.yaml`（保留人工注释的 YAML 往返编辑）、`filelock`（跨平台单写者锁）；标准库 `argparse`、`asyncio`、`tomllib`、`json`、`pathlib`、`logging`

**Storage**: 本地明文文件；原始记录使用有界 JSONL 分段，长期记忆、来源索引、`MemoryCoverageOverview` 及整理前沿使用单个可人工编辑的 YAML 文档，提示追踪使用不含正文的原子 JSON；不引入独立概览文件、第二事实来源或数据库

**Testing**: `pytest`、`pytest-asyncio`、`hypothesis`、`respx`；包含单元、属性、契约、集成、故障注入与 10,000/1,000 规模测试

**Core Business Logic**: BR-001—BR-018 全部建立自动化映射；重点覆盖输入先存后生成、输出先存后展示、跨启动连续性、仅在窗口边界整理、先整理后修剪、原始记录永久保留、长期记忆与多来源原子一致、人工修改校验/回退、冲突优先级、统一只读来源查询、按需原文注入、人格/状态组合、扩展默认禁用及供应商故障恢复

**Comment Impact**: 新增模块边界、持久化不变量、恢复分支、安全边界和非直观算法的简体中文注释；每条新增或必要更新使用 `[2026-07-19]` 或后续版本号标记。若注释仍准确则不改写、不删除

**Sensitive Credentials**: DeepSeek API Key 只通过配置中指定的环境变量名（默认示例为 `DEEPSEEK_API_KEY`）读取；配置、人格、记忆、提示追踪、日志和测试不得保存实际值。写入前运行凭据检测/拒绝或不可逆脱敏，日志统一过滤 Authorization、提示正文、工具参数和供应商推理字段；提交前执行凭据扫描

**Git Milestones**: 设计工件完成并校验后提交；持久化/恢复核心及测试完成后提交；配置/模型/工具/状态边界完成后提交；端到端 CLI、故障注入、性能与安全验证通过后提交。每个提交仅包含对应重大修改

**Target Platform**: Windows、Linux、macOS 的本地终端；UTF-8 文件系统；单用户、单写进程

**Project Type**: 可复用 Python 包 + CLI 应用

**Performance Goals**: 指定 Windows 参考环境中，10,000 条永久原始记录和 1,000 条长期记忆下至少 100 次全新进程启动的 nearest-rank p95 不超过 3 秒；计时从进程创建到配置、原始索引、长期 YAML 与覆盖概览可供首轮组装，网络调用为 0。Windows/Ubuntu/macOS × Python 3.13/3.14 另运行功能矩阵，不在非参考环境判定 3 秒门槛

**Constraints**: 所有可变参数与提示词配置化；原始记录永久保留；长期记忆可直接人工编辑；任何写入中断不得暴露半条确认记录；单次模型输入受配置预算限制；外部工具和自主循环默认禁用；明文记忆的安全边界必须明确可见

**Scale/Scope**: 首版 1 个连续 Agent、1 个用户、1 个写进程、1 个默认状态、2 个核心人格、1 个内置只读工具；验收规模 10,000 条原始记录、1,000 条长期记忆、100 轮连续交互和 10 次重启

## Constitution Check

*GATE: Phase 0 前检查；Phase 1 设计完成后已再次检查。*

| Gate | Required Evidence | Status |
|------|-------------------|--------|
| I. Clarity, extensibility, maintainability | `domain` 定义稳定对象/端口；存储、供应商、提示、工具、状态和控制器职责及依赖方向在契约中明确 | PASS |
| II. Decoupling and readability | 业务编排只依赖端口；供应商 SDK 类型不越过适配器；长期记忆正文与来源同文档提交，避免跨存储共享可变状态 | PASS |
| III. Simplest understandable implementation | 单包 CLI、本地文件和静态状态解析满足首版；不引入数据库、Web 服务、完整状态机或自主调度器 | PASS |
| IV. zh-CN traceable comments | 已识别持久化、安全、恢复和扩展边界的注释范围，并规定简体中文日期/版本标记 | PASS |
| V. Git discipline | 四个重大修改里程碑及原子提交边界已列出 | PASS |
| VI. Core business tests | BR-001—BR-018 映射到成功、边界、故障和恢复层级；详见 `data-model.md` 与 `quickstart.md` | PASS |
| VII. Credential protection | 凭据仅外部注入，写前拒绝/脱敏，日志过滤、最小授权、权限检查和提交扫描均已设计 | PASS |

**Post-design re-check**: PASS。Phase 1 未引入任何违背宪章的新复杂度；所有扩展点均有首版最小实现和默认关闭语义。

## Project Structure

### Documentation (this feature)

```text
specs/001-persistent-memory-agent/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── cli.md
│   ├── configuration.md
│   ├── model-and-tools.md
│   └── storage.md
└── tasks.md                  # 由 /speckit-tasks 生成
```

### Source Code (repository root)

```text
pyproject.toml
config/
├── agent.toml               # 预算、窗口、路径、恢复和运行参数
├── providers.toml           # Provider、模型、能力、超时与重试
├── states.toml              # 状态到人格文件的组合
├── tools.toml               # 工具启用、权限和调用上限
├── logging.toml             # 日志级别与脱敏策略
├── personas/
│   ├── chat.md
│   ├── memory_curator.md
│   └── states/
│       └── default.md
└── prompts/
    ├── chat_context.md
    ├── memory_curation.md
    └── untrusted_memory_boundary.md

src/bai_agent/
├── __init__.py
├── __main__.py
├── cli.py
├── application.py           # 用例编排；只依赖领域端口
├── domain/
│   ├── models.py
│   ├── errors.py
│   └── ports.py
├── config/
│   ├── loader.py
│   └── validation.py
├── memory/
│   ├── archive.py
│   ├── long_term.py
│   ├── curation.py
│   ├── selection.py
│   └── recovery.py
├── prompting/
│   ├── personas.py
│   └── assembler.py
├── providers/
│   ├── registry.py
│   └── deepseek.py
├── tools/
│   ├── registry.py
│   ├── executor.py
│   └── memory_source.py
├── states/
│   └── resolver.py
├── runtime/
│   ├── controller.py
│   ├── loops.py
│   └── tracing.py
└── security/
    ├── credentials.py
    ├── incidents.py
    ├── permissions.py
    └── redaction.py

data/
├── memory/
│   ├── raw/
│   │   └── 00000001.jsonl
│   ├── long_term.yaml
│   └── .state/
│       ├── writer.lock
│       └── long_term.last-valid.yaml
└── runtime/
    └── prompt-traces/

tests/
├── unit/
├── contract/
├── integration/
├── fault_injection/
├── performance/
└── fixtures/

.github/workflows/
└── compatibility.yml
```

**Structure Decision**: 采用单一 Python 包，保持从 `application` 到领域端口、再到文件/供应商适配器的单向依赖。`config/` 是所有提示词和可变参数的唯一维护入口；`data/` 只保存运行数据，不混入人格或代码。工具、状态解析和运行控制各自只有一个首版实现，但接口允许未来新增实现而不改变记忆核心。

## Complexity Tracking

无宪章例外。文件分段、跨进程锁和适配器三个机制分别直接满足原子恢复、单写者假设与供应商可替换性，不属于可删减的预先复杂化。
