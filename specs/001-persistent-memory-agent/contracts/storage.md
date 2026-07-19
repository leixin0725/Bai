# Contract: 记忆存储、来源与恢复

## 1. 权威文件

```text
data/memory/
├── raw/
│   ├── 00000001.jsonl
│   ├── 00000002.jsonl
│   └── ...
├── long_term.yaml
└── .state/
    ├── writer.lock
    └── long_term.last-valid.yaml
```

- `raw/*.jsonl`：全部已确认用户/Agent 原文，永久权威归档。
- `long_term.yaml`：长期记忆、来源关系和直接注入修剪前沿的联合权威文档。
- `long_term.last-valid.yaml`：只用于人工编辑损坏后的只读回退，不接受人工直接维护。
- 运行时 record/source 索引均可重建，不持久化为第二份权威状态。

## 2. 原始记录格式

每行是一个完整 JSON 对象，UTF-8、`ensure_ascii=false`、`allow_nan=false`、紧凑稳定键序：

```json
{"schema_version":1,"record_id":"rec-00000000-0000-4000-8000-000000000001","global_sequence":1,"turn_id":"turn-00000000-0000-4000-8000-000000000001","role":"user","content":"示例文本","created_at":"2026-07-19T00:00:00Z","state_id":"default","config_revision":"sha256:example","content_sha256":"sha256:example"}
```

示例中的 ID/哈希均为不可用占位符。记录行必须以 `\n` 结束；内容换行由 JSON 转义保存。未知字段、缺失字段、重复 ID/序号、序号缺口、校验和不符或尾部半行均使该段无效。

## 3. 原始段提交

程序在启动时取得 `.state/writer.lock` 并持有至退出；失败则以稳定错误结束，不退化为多写者。进程内所有写操作由一个串行写服务执行。

追加协议：

1. 对正文执行凭据拒绝/脱敏；不通过则不创建记录。
2. 取得下一 `global_sequence`，构造完整 RawRecord 并序列化为一行。
3. 若尾段加入新行后超过记录数或字节上限，则封存尾段并以新段作为目标。
4. 在目标目录创建唯一临时文件，写入目标段的完整新字节。
5. `flush()` 后 `os.fsync()` 文件句柄；支持时同步目录项。
6. `os.replace(temp, target)`；只有成功替换的正式文件视为已确认。
7. 更新内存索引；若此时进程终止，重启从正式段重建即可。

用户输入的步骤 6 必须早于 Provider 调用；Assistant 输出的步骤 6 必须早于终端展示。首版不展示模型流增量，避免把未确认内容呈现为已保存响应。

## 4. 长期记忆格式

```yaml
schema_version: 1
revision: 1
curation:
  curated_through_sequence: 0
  last_batch_id:
  updated_at:
  covered_record_ids: []
memories:
  - memory_id: mem-00000000-0000-4000-8000-000000000001
    kind: preference
    text: 用户偏好简洁的技术说明。
    status: active
    source_refs:
      - record_id: rec-00000000-0000-4000-8000-000000000001
        relation: supports
        record_sha256: "sha256:example"
    created_by: memory_curator
    created_at: 2026-07-19T00:00:00Z
    updated_at: 2026-07-19T00:00:00Z
    supersedes: []
    tags: [communication]
```

该 YAML 是便于人工维护的领域文档，不是模型输出原文。模型只返回受限 JSON 候选，程序校验并合并到 round-trip YAML 对象。

## 5. 整理与修剪原子协议

整理只选择旧前沿之后、即将移出直接注入窗口的最旧连续完整轮次。

1. 原始批次已全部确认持久化，计算 `batch_id` 和输入哈希。
2. 使用独立记忆整理人格和 `memory_curator` model profile 请求完整 JSON。
3. 校验 Schema、数量/长度、凭据、来源、关系和人工项优先级。
4. 重新读取 `long_term.yaml` 并比较基准哈希；发现人工并发修改则中止，不能覆盖。
5. 在内存中合并新/修正记忆及其全部 `source_refs`，递增 revision，并将 `curated_through_sequence` 推进到批次末尾。
6. 对联合文档执行完整校验，包括每个来源在原始索引中存在且哈希一致。
7. 同目录临时文件写全 YAML、flush、fsync、replace。
8. 替换成功后才更新内存快照；提示选择器据新前沿停止直接注入该批次。
9. 原始 JSONL 不删除、不截断、不重写。

整理没有提取到记忆时仍可提交只推进前沿的有效批次，但必须保留 `last_batch_id`、覆盖记录及整理审计。任何失败均保持旧文档和旧前沿，下一轮可使用同一 `batch_id` 幂等重试。

## 6. 人工维护

推荐流程：停止 Agent → 备份 `data/memory/` → 编辑 `long_term.yaml` → 执行 `memory validate` → 重启。

人工维护约束：

- 可以修改正文、状态、标签、关系，或增加带有效原始来源的 `created_by: manual` 项。
- 不应直接编辑 `curation` 系统字段；首版验证器拒绝未经维护命令确认的前沿变更。
- 不能删除仍被 `supersedes` 引用的项、制造关系环或留下不存在的来源。
- 人工新项与自动项一样必须有至少一个来源；`manual_basis` 用于标记人工判断依据。
- 自动整理不得静默覆盖人工项；冲突时保留人工项并报告候选。

每次有效加载后原子刷新 `.state/long_term.last-valid.yaml`。若编辑无效：

- 保留用户文件原样，给出路径、行列和稳定错误码；不自动修复或覆盖。
- 使用 last-valid 仅提供只读聊天/查询能力，并明确告警。
- 禁止自动整理和长期记忆写入，直至主文件修复并重新验证。
- 若没有有效副本，停止依赖长期记忆的模型生成，不以空记忆静默启动。

## 7. 启动与恢复

1. 获取单写者锁。
2. 只扫描严格命名的正式 JSONL 段；临时/未知文件不参与状态。
3. 校验段号、每行、全局顺序和校验和，建立内存 offset 索引。
4. 加载并校验 `long_term.yaml`；校验所有来源和前沿不超过最大序号。
5. 计算最近未整理/直接注入范围，无需任何 Provider 网络调用。
6. 加载配置和人格后才进入首轮输入。

恢复规则：

- 原子替换前崩溃：正式文件仍是完整旧状态。
- 原子替换后、内存更新前崩溃：重启读取完整新状态。
- 临时文件残留：报告并保留为恢复材料；不当成提交，不自动删除。
- 原始段损坏：隔离内容、保留原字节并禁止继续写入，不能跨序号缺口扩展历史。
- 长期 YAML 损坏：按人工维护回退规则进入只读/禁止整理状态。

首版仅支持本地文件系统。备份必须在 Agent 停止或持有同一锁时复制整个 `data/memory/`；不能单独复制长期记忆而遗漏来源原文。

## 8. 直接注入选择

- `global_sequence <= curated_through_sequence` 的记录不再因“近期窗口”自动逐字注入，但仍是长期记忆来源候选。
- 前沿之后的记录按完整轮次和配置预算直接注入；不得截断单条内容形成伪原文。
- 若未整理记录已达到必须移出的边界，Controller 必须先完成整理；失败则本轮在 Provider 调用前停止，不能提前丢弃原文或越过预算。
- 长期记忆按状态、相关性、时间和预算选择；未选中的有效项仍由类别/范围摘要表示且保留查询资格。
- 来源原文从不因使用某条长期记忆而自动注入；仅显式调用 `memory_source_query` 后进入该 flow。

## 9. 只读来源读取

存储端口为来源工具提供：

```text
get_memory_revision(memory_id)
list_source_refs(memory_id)
read_raw_records(record_ids)
```

不提供 create/update/delete 方法。结果必须：

- 验证记忆与来源在同一已加载 revision 中一致；
- 按全局序号稳定排序并按配置页大小分页；
- 校验记录哈希并应用与直接读取相同的凭据过滤；
- 不暴露真实路径、offset 或锁信息；
- 调用前后所有权威文件内容哈希相同。

## 10. 存储错误码

| Code | Meaning |
|---|---|
| `WRITER_LOCKED` | 另一实例持有写锁 |
| `RAW_SEGMENT_INVALID` | 段、序号或校验和无效 |
| `RAW_SEQUENCE_GAP` | 全局序号存在缺口 |
| `MEMORY_DOCUMENT_INVALID` | YAML/字段/关系无效 |
| `MEMORY_SCHEMA_UNSUPPORTED` | 未知 Schema 版本 |
| `SOURCE_RECORD_MISSING` | 来源不存在 |
| `SOURCE_HASH_MISMATCH` | 来源正文与引用摘要不一致 |
| `CONCURRENT_MANUAL_EDIT` | 提交前发现人工修改 |
| `CREDENTIAL_REJECTED` | 内容疑似含可用凭据，未持久化原值 |
| `ATOMIC_WRITE_FAILED` | 临时写、fsync 或 replace 失败 |

所有错误消息必须避免包含正文、凭据、Authorization、完整绝对路径和临时文件内容。

## 11. 必测故障点

- 临时文件创建后、写入中、flush 后、fsync 前后、replace 前后。
- 段恰好滚动的边界与单条记录超过上限。
- YAML 替换后但内存更新前。
- 整理返回空、截断、非法 JSON、悬空/跨批次来源或覆盖人工项。
- 人工编辑发生在加载与提交之间。
- 两个进程同时启动，只允许一个取得锁。
- 10,000 条原始记录/1,000 条长期记忆的 20—30 次冷启动 p95。

每个崩溃测试恢复后只能观察到完整旧状态或完整新状态，不得出现半条记录、无来源记忆、提前前沿或原始记录减少。
