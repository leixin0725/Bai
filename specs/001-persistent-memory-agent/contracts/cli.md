# Contract: CLI

## 1. 入口

```powershell
python -m bai_agent [GLOBAL_OPTIONS] COMMAND [COMMAND_OPTIONS]
```

全局选项：

- `--config-dir PATH`：选择完整配置目录。
- `--data-dir PATH`：仅供测试、迁移或恢复时显式覆盖配置的数据根；不能是文件系统/仓库/配置根。
- `--log-level LEVEL`：只允许收紧/放宽已配置日志级别，不关闭凭据过滤。
- `--json`：命令结果输出稳定 JSON；聊天正文除外。

CLI 不提供会话/线程 ID、创建新对话或选择历史对话。所有 `chat` 调用使用同一个数据根和连续 Agent 身份；记忆重置只通过带显式作用域的维护命令执行。

## 2. `config validate`

```powershell
python -m bai_agent config validate --config-dir config
```

行为：加载整套配置和提示、校验引用/模板/能力/秘密存在性并输出 `config_revision`。不调用 Provider、不创建记忆、不展示秘密值。

成功 JSON：

```json
{"ok":true,"config_revision":"sha256:...","provider_profiles":["chat","memory_curator"],"states":["default"],"tools":["memory_source_query"]}
```

## 3. `memory validate`

```powershell
python -m bai_agent memory validate
```

行为：取得写锁或只读一致快照，验证所有原始段、全局顺序、长期 YAML、来源、哈希、`MemoryCoverageOverview` 连续覆盖、修剪前沿、文件权限和凭据模式。POSIX 目录/文件必须为 `0700`/`0600`；Windows DACL 的 `too_broad` 或 `unverifiable` 均返回安全失败。不得修改主记忆文件；只有全部有效时才可原子刷新 last-valid 副本。

成功 JSON：

```json
{"ok":true,"raw_records":10000,"long_term_items":1000,"curated_through_sequence":9950,"coverage_spans":42,"coverage_gaps":0,"dangling_sources":0}
```

## 4. `memory source`

```powershell
python -m bai_agent memory source MEMORY_ID [--cursor CURSOR]
```

该命令是内置 `memory_source_query` 的维护者 CLI 包装，必须复用同一只读服务、排序、分页、凭据过滤和错误码，不能直接另写文件读取旁路。默认输出 JSON；正文输出到 stdout，审计日志不复制正文。

## 5. `memory reset`

```powershell
python -m bai_agent memory reset long-term
python -m bai_agent memory reset all
```

两个作用域均立即执行，不提供额外交互确认：

- `long-term`：保留永久原始记录、近期直接窗口、整理前沿和 coverage spans，清空长期条目并用中性概览替换可注入的旧概览正文；同一 revision 原子更新主 YAML 与 last-valid。
- `all`：先原子提交空长期文档，再从末段向前清除全部原始记录和原子临时副本，使任一中断状态仍不存在悬空来源；最终 revision、整理前沿和 coverage 均回到首次启动状态。

两者必须取得与聊天相同的单写者锁，不调用 Provider、不要求 Provider 凭据、不输出任何记忆正文。安全事件门禁和处置证据不属于记忆根，任何作用域都不得删除。

成功 JSON：

```json
{"ok":true,"scope":"all","raw_records_before":42,"raw_records_after":0,"long_term_items_before":3,"long_term_items_after":0,"coverage_spans_before":2,"coverage_spans_after":0,"curated_through_sequence":0}
```

## 6. `doctor`

```powershell
python -m bai_agent doctor
```

只读检查 Python/依赖版本、配置、数据目录、写锁状态、文件权限、凭据环境变量存在性和 Provider profile 结构。默认不发网络请求；`--probe-provider` 可显式执行最小 Provider 探测且不能把响应正文写入记忆。

## 7. `chat`

```powershell
python -m bai_agent chat
```

启动顺序：

1. 获取单写者锁、验证配置和记忆。
2. 显示连续 Agent 就绪状态，不显示“新对话”或历史选择。
3. 从 stdin 逐条读取用户输入；EOF 或 Ctrl+C 安全退出。
4. 每条输入依照 SingleTurnController 契约处理。

输出规则：

- 用户输入确认落盘后才发起模型调用。
- Assistant 完整响应确认落盘后才写 stdout；首版不显示未确认流增量。
- Provider/整理失败时 stderr 输出稳定安全错误；已持久化用户输入保留。
- 若启动时发现最后一个 `turn_id` 只有用户输入，先报告待恢复轮次；只有显式 `--resume-pending` 才重新调用模型，避免无提示重复计费。
- `--resume-pending` 复用原 `turn_id`，不得再写一条相同用户记录。
- 记忆整理或长期 YAML 写入失败且即将越过窗口时，本轮在 Provider 调用前停止。
- 不在终端状态行显示提示正文、API Key、Authorization 或 DeepSeek 推理内容。

## 8. 稳定退出码

| Code | Meaning |
|---:|---|
| 0 | 成功 |
| 2 | CLI 参数或配置无效 |
| 3 | 凭据缺失/无效或内容被凭据门禁拒绝 |
| 4 | 写锁不可用 |
| 5 | 记忆、来源或恢复状态无效 |
| 6 | Provider/网络生成失败 |
| 7 | 工具、权限或安全策略拒绝 |
| 8 | 原子持久化失败 |
| 130 | 用户取消/Ctrl+C |

凭据事件命令：

```powershell
python -m bai_agent security incident check
python -m bai_agent security incident acknowledge --rotation-reference REF --repository-scan-revision REV --runtime-scan-revision REV --disposition-record RECORD
```

`check` 在轮换引用、全仓库及全部可达 Git 历史扫描修订、运行数据/日志/追踪扫描修订和处置记录任一缺失时返回退出码 7；输出只含逻辑制品 ID、不可逆指纹、范围和确认状态，不含秘密值或真实绝对路径。该非零状态阻止阶段提交与交付。

错误 JSON：

```json
{"ok":false,"error":{"code":"MEMORY_DOCUMENT_INVALID","message":"长期记忆文件未通过校验；已保留原文件。","retryable":false}}
```

`message` 不包含正文、秘密、绝对路径或 SDK 堆栈。详细调试仍必须经过日志过滤。

## 9. CLI 验收

- 首次启动、正常轮次、Provider 失败、重启恢复和 pending retry。
- 不存在任何会话/线程创建或选择交互。
- 输入落盘早于 Provider 调用，输出落盘早于 stdout。
- 无效配置/人格/YAML、锁冲突和权限过宽具有稳定退出码。
- `memory source` 与人格工具调用在相同 revision 下返回相同记录顺序和错误。
- `memory reset long-term` 保留原始记录与覆盖索引但不保留长期正文；`memory reset all` 恢复空记忆且安全事件状态不变。
- `--json` 输出可由 Schema 解析，且 stderr/stdout 不泄漏测试凭据。
