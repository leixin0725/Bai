# Contract: 可拒绝轮次事务与恢复

## 目的

在不删除已确认完整原始轮次的前提下，同时保证：

1. 用户输入在任何模型生成前已持久化；
2. 调试模式拒绝任一待批调用后，整轮不进入历史、记忆、索引、状态、pending 或后续上下文；
3. 已批准请求的普通 provider/网络失败只进入一条 USER pending；仅显式 `--resume-pending` 恢复，默认或 `--discard-pending` 放弃；
4. 每个中断点重启后都能收敛到清晰状态。

## 存储位置与权限

- 固定路径：`data/memory/.state/turn-transaction.json`。
- 只允许一个活动事务，与现有 `WriterLease` 一致。
- 使用同目录临时文件、flush、fsync、原子 replace；权限至少与现有长期记忆私有文件要求相同。
- journal 不属于 raw history；正常结束后不存在，也不作为 cancelled/tombstone/pending 暴露。

## PREPARED schema

```json
{
  "schema_version": 1,
  "state": "PREPARED",
  "transaction_id": "tx-...",
  "turn_id": "turn-...",
  "checkpoint": {
    "raw_count": 0,
    "raw_tail_id": null,
    "raw_sha256": "sha256:...",
    "long_term_revision": 12,
    "long_term_sha256": "sha256:...",
    "agent_state": "default"
  },
  "provisional_user_record": {
    "schema_version": 1,
    "record_id": "rec-00000000-0000-4000-8000-000000000001",
    "global_sequence": 1,
    "turn_id": "turn-00000000-0000-4000-8000-000000000001",
    "role": "user",
    "content": "...",
    "created_at": "2026-07-20T00:00:00Z",
    "state_id": "default",
    "config_revision": "sha256:...",
    "content_sha256": "sha256:..."
  }
}
```

允许用户输入存在于该私有临时 journal，是为了满足生成前持久化；禁止放入模型最终 payload、来源追踪、provider 响应、工具凭据、认证信息和取消原因。

## READY_PENDING schema

普通 provider/网络失败且重试结束时，在保留 PREPARED 字段基础上新增：

```json
{
  "state": "READY_PENDING",
  "pending_failure_code": "PROVIDER_UNAVAILABLE"
}
```

- `pending_failure_code` 只允许脱敏枚举，不含异常正文、HTTP body、prompt、来源或凭据。
- READY_PENDING 的唯一发布目标是 `provisional_user_record` 对应的一条 USER pending；不得包含 assistant、长期记忆目标、工具结果或拒绝标记。
- 维护者明确 reject 不得转入 READY_PENDING。

## READY_TO_COMMIT schema

在保留 PREPARED 字段基础上新增：

```json
{
  "state": "READY_TO_COMMIT",
  "assistant_record": {
    "record_id": "...",
    "turn_id": "...",
    "role": "ASSISTANT",
    "content": "...",
    "created_at": "..."
  },
  "target_long_term_document": {},
  "target_long_term_sha256": "..."
}
```

- `target_long_term_document` 在本轮无整理变化时省略。
- READY_TO_COMMIT 表示整轮本地结果已经确认，之后只能幂等前滚，不再按拒绝路径丢弃。

## 正常流程

1. controller 接到用户输入，捕获 `PreTurnCheckpoint`。
2. 原子写 PREPARED journal；成功后才允许整理或聊天模型生成。
3. 各辅助调用的响应、工具结果、状态和整理结果只进入 `TurnWorkingSet`。
4. 任一待批请求 reject：停止调用链，丢弃工作集，原子删除 PREPARED journal，验证 checkpoint 未变化，返回输入界面。
5. 已批准请求发生普通 provider/网络失败且重试结束：原子写 READY_PENDING，调用 `append_pending_user()` 幂等发布一条 USER pending，验证后删除 journal；不发布 assistant/long-term。
6. 最终 assistant 结果就绪：把 assistant record 与可选目标长期记忆写入 READY_TO_COMMIT journal。
7. `append_complete_turn(user, assistant)` 以 record id/turn id 幂等发布连续完整轮次。
8. 以基线 revision/hash 比较并幂等发布目标长期记忆。
9. 验证 raw/long-term 目标身份后删除 journal。

raw 与 long-term 的发布顺序必须固定，并由恢复器使用相同顺序。无论中断在哪一步，READY_PENDING/READY_TO_COMMIT journal 都足以识别各自已完成部分并继续，不重复追加或把 pending 升级为完整轮次。

## 可放弃尾部 pending

READY_PENDING 发布后的单条 USER 不是已确认完整轮次。只有同时满足下列条件才允许放弃：

- 位于全局 raw 最末尾，角色为 USER，且同 turn 不存在 ASSISTANT；
- 此前 raw 严格由同 turn 的 USER/ASSISTANT 连续对组成；
- record/turn/sequence/content hash 与最后一个 segment 的最后一行一致；
- curation frontier、covered record ids、coverage spans 和长期记忆 source refs 均未引用该记录；
- 调用方持有 WriterLease，expected turn id 若提供则完全匹配。

丢弃端口只把最后一个 segment 原子替换为移除末行后的完整字节。若末行是该 segment 唯一记录，目标是合法空尾 segment；不得 unlink、重编号 segment、按任意 turn id 删除、改写此前 sequence 或修改 long-term。替换前故障保留完整旧 pending，替换后故障保留完整已删除状态；临时文件不是正式 segment，重启不得读成历史。

## 启动恢复

恢复顺序：

1. 获取 `WriterLease`。
2. 检查 journal schema、权限和内容安全。
3. 无 journal：继续现有启动恢复。
4. PREPARED：删除 journal；不向 raw 写 cancelled/pending，不调用 provider，不执行整理。
5. READY_PENDING：核对 checkpoint 和已发布记录，幂等完成单条 USER pending，验证后删除 journal。
6. READY_TO_COMMIT：核对 checkpoint 和已发布记录，幂等完成完整 raw turn 与 long-term，验证后删除 journal。
7. 恢复完成后才允许读取 pending；默认/`--discard-pending` 原子放弃，`--resume-pending` 保留并恢复。策略完成后才允许接受新输入或访问 provider。

恢复器的安全 trace 只记录 turn id 与 `recovery_absent/recovery_discarded/committed` 等枚举状态；不得记录 journal 正文、prompt、来源或凭据。实际用量同样只允许数值元数据。

## 冲突和损坏

- journal JSON 损坏、schema 未知、权限过宽或字段包含禁区数据：fail closed，保留文件供人工处理，输出不含内容的恢复指引。
- PREPARED 的已提交基线被外部修改：若事务从未发布，仍可安全丢弃 journal；不得回写 checkpoint 快照覆盖人工更改。
- READY_PENDING 发布时 raw tail 与“基线或已达单条 pending 目标”都不匹配：停止恢复，禁止新轮和 provider，避免覆盖人工编辑。
- READY_TO_COMMIT 发布时 raw tail 或 long-term revision 与“基线或已达完整目标”两者都不匹配：停止恢复，禁止新轮和 provider，避免覆盖人工编辑。
- journal 删除失败：任一 READY 可在下次重启幂等重放；PREPARED 仍阻止新轮，直到删除成功。
- 尾部 pending 校验发现历史中间未配对 USER、已有 ASSISTANT、expected turn 不匹配、segment/hash 损坏或长期引用：停止丢弃，不修改 raw/long-term，不调用 provider。

## 记忆整理契约

```python
proposal = curator.propose(working_view, gateway)
# 不写 long_term.yaml
working_set.stage(proposal)
...
turn_uow.ready(assistant_record, proposal.target_document)
turn_uow.commit()
```

`propose()` 必须保留来源索引与目标文档身份；`commit()` 只由 TurnUnitOfWork 的 READY_TO_COMMIT 发布路径调用。READY_PENDING 必须丢弃 proposal，不得在 provider 返回后立即修改长期记忆。

## 工具契约

- 当前工具注册表中的所有工具必须声明并经审计为 `read_only=true`；其结果只存于 working set，reject 时丢弃。
- 写工具在 rejectable turn 中必须提供可持久恢复的 `prepare/commit/rollback`，或声明并实现具有等价恢复证明的明确补偿契约。
- 未声明、声明与实现不一致、prepare 失败或恢复能力未通过合同测试的写工具必须在执行任何副作用前被拒绝，不能仅在 UI 上警告。

## 故障注入矩阵

至少在以下动作之前/之后模拟异常并重启：

- PREPARED 临时文件写入、fsync、replace；
- 每次 curation/chat/tool/retry 批准点 reject；
- READY_PENDING 与 READY_TO_COMMIT 各自的临时文件写入、fsync、replace；
- READY_PENDING 单条 USER pending 发布、默认/显式丢弃及 `--resume-pending`；
- pending 与此前完整记录位于同一 segment、单独位于 rollover 尾 segment、尾 segment 原子替换的每个 failure hook；
- raw 第一条/第二条记录发布、跨 segment rollover、manifest 更新；
- long-term replace 与 last-valid 更新；
- journal cleanup；
- 回滚期间的删除失败与进程终止。

断言：明确拒绝或未决定的 PREPARED 最终无 journal且已确认状态等于 checkpoint；READY_PENDING 最终只发布一条 USER pending，显式恢复不重复 USER，默认/显式丢弃只截尾；READY_TO_COMMIT 最终完整发布且无重复；任何不确定状态发送次数为 0 且阻止新轮。
