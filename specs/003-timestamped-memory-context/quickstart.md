# Quickstart: 验证历史上下文时间段标注

**Status**: Phase 1 validation guide

**Platforms**: Ubuntu 24.04/Python 3.13–3.14（主要）；Windows 11/PowerShell（次要）

本指南中的测试文件是本功能计划目标；实现阶段完成后命令必须可直接运行。命令不需要真实 provider 凭据，夹具只使用无效占位值。

## 1. 安装与默认配置

```bash
python -m pip install -e ".[dev]"
python -m bai_agent --config-dir config --data-dir data config validate
```

确认 `config/history_timestamps.toml` 为：

```toml
schema_version = 1
display_timezone = "Asia/Shanghai"
long_gap_minutes = 30
continuous_segment_refresh_minutes = 120
split_on_local_date_change = true
```

预期：配置校验成功；`history_timestamps.toml` 参与 config revision，并显示/记录为 `config:history_timestamps` 资产。原始 JSONL/YAML 不发生写入。

## 2. 运行核心时间算法验收

```bash
pytest tests/unit/test_temporal_annotation.py tests/unit/test_temporal_annotation_properties.py -q
```

必须覆盖：

- 空区块无 marker，非空区块首项恰有一个 marker；
- gap 30 分钟恰好分段，少 1 微秒不因 gap 分段；
- 自最近 marker 120 分钟恰好刷新并重新计时；
- 前一范围 end 与当前 start 的重叠 gap 为零；
- 跨显示时区自然日、时间倒退、同刻、多原因命中；
- EVENT/SOURCE_RANGE/RECORDED 三种固定中文格式；
- DST 跳跃/重复时刻仍带明确 offset；
- 输入顺序/body 不变，marker/body spans 可从最终文本逐字回读；
- 相同输入重复运行产生相同文本和边界。

### Representative expected output

密集消息只显示段首：

```text
[时间：2026-07-20 09:00 +08:00]
user: 早上好
assistant: 早上好，需要我做什么？
user: 帮我检查计划
```

09:20 后间隔恰好 30 分钟：

```text
[时间：2026-07-20 09:20 +08:00]
user: 第一段结束
[时间：2026-07-20 09:50 +08:00]
user: 这是新的时间段
```

连续对话在最近 marker 后首次达到 120 分钟才刷新；如果没有下一条消息，不生成尾 marker。

## 3. 验证配置边界与原子重载

```bash
pytest tests/contract/test_history_timestamp_config.py tests/integration/test_temporal_config_reload.py -q
```

重点场景：

1. 用默认 gap 30 分钟构建相隔 45 分钟的固定历史，应有第二个 marker。
2. 只把 gap 改为 60 分钟并通过 reload，下一轮同一历史不应因 gap 增加 marker。
3. 把 refresh 设为 59 分钟、gap 保持 60 分钟，应在任何 raw 写入、工具执行和 provider 请求前失败。
4. 依次验证文件缺失、unknown field、`true` 冒充整数、越界和非法 IANA 时区；都不得混用旧/新策略。
5. 还原 canonical default 并重新运行 `config validate`。

Windows 必须额外验证安装 wheel 后无需系统 IANA 数据也能解析 `Asia/Shanghai`；Ubuntu 和 Windows 对固定 UTC instant 输出相同的本地日期、分钟和 `+08:00`。

## 4. 验证聊天与长期记忆

```bash
pytest tests/unit/test_memory_temporal_projection.py tests/unit/test_temporal_prompt_budget.py -q
pytest tests/contract/test_prompt_temporal_context.py tests/integration/test_temporal_chat_context.py -q
```

预期：

- `memory_overview`、`long_term_memories`、`recent_records` 三个非空区块各有自己的首 marker；区块间不共享 previous/last-marker 状态。
- recent 使用每条 raw `created_at` 点时间；body 仍为既有 `role: content`。
- 长期记忆和 overview 使用全部已验证 raw 来源的最早—最晚范围，而不是 YAML item 的整理时间。
- 来源引用乱序、重复、范围重叠或端点相等时仍保持既有选择顺序；相等端点仍显示 `[时间范围：… 至 …]`。
- 来源缺失、不可读、hash 不匹配或时间非法时 provider 请求为 0，不使用 `created_at` 降级。
- 通用 RECORDED formatter 合同可测试，但当前版本没有持久化 RECORDED 入口；现行 schema v1 和未识别格式都不得走该路径。
- overview/recent/long-term 预算包含 marker；超限不会静默删除 marker 或正文。
- 构建一次 raw index 并复用，没有按长期记忆项重复 `archive.read_all()`。

## 5. 验证记忆整理提示

```bash
pytest tests/integration/test_temporal_curation_context.py tests/integration/test_curation_workflow.py -q
```

`batch_records`、`existing_memories`、`current_overview` 各自独立标注，batch metadata 和 output schema 不标注。模型可见表示应类似：

```text
[时间：2026-07-20 09:00 +08:00]
{"content":"待整理消息","record_id":"r-1","role":"user"}
```

逐项 canonical JSON 的字段、转义和 curation proposal 输出 schema 必须与实现前一致；marker 不进入 JSON。重复 body/JSON 夹具仍能通过构建时记录的绝对 span 精确关联来源，不允许反向 `find()` 猜测。

## 6. 验证工具续接协议与事件时间

```bash
pytest tests/contract/test_temporal_tool_protocol.py tests/contract/test_deepseek_tool_calls.py -q
pytest tests/integration/test_temporal_tool_continuation.py tests/integration/test_autonomous_loop.py -q
```

用可注入 deterministic clock 证明：

- assistant tool-call batch 使用成功模型结果被 gateway 接受的时刻；失败 retry 不产生事件，审批等待不重采样；
- 同一 assistant response 的多个 calls 只有一条 assistant 事件和一个 accepted_at；
- 每个 tool result 在安全/大小/事务处理完成并形成可发送结果后独立采样 completed_at；
- 短执行与 call 同段时 result 前无逐条 marker；达到 gap、跨日或倒序条件时 result 前有自己的 marker；
- 多个 continuation round 从未标注事件整体重建，旧 marker 不重复、整个工具 block 只有一个默认首 marker。

wire payload 必须仍满足：

```text
assistant(content="[时间：...]\n<原正文>" 或仅 marker, tool_calls=[原结构])
tool(content="<可选 marker>\n<原 canonical JSON>", tool_call_id="原 id")
```

没有新增 role/message；call id/name/arguments/order、tool_call_id 与原 JSON body 逐字段/逐字不变。内部逻辑读取渲染前 `ToolResult`，不把带 marker content 当整串 JSON 解析。

## 7. 验证来源、预算与调试等价

```bash
pytest tests/unit/test_prompt_provenance.py tests/unit/test_context_estimation.py tests/unit/test_context_estimation_properties.py -q
pytest tests/integration/test_prompt_debug_equivalence.py tests/integration/test_prompt_trace_actual_usage.py -q
```

检查每个 marker part：

- 来源同时包含 `config:history_timestamps` 的真实 revision/hash 和触发日志项来源；
- trust 为 `UNTRUSTED_DATA`；
- marker/body 在同一 payload pointer 下 spans 互不重叠且逐字回读；
- DeepSeek 不再额外产生覆盖整个 content 的重复 part；part token + protocol overhead 守恒；
- marker 在物化前已进入最终 payload，因此 estimator、TUI 和 sender 看到相同字符；
- 固定 clock/input 下 debug on/off 的最终请求逐字一致，等待批准不会改变 marker。

## 8. 验证来源追溯工具保持不变

```bash
pytest tests/contract/test_memory_source_tool.py -q
```

直接调用 `memory source`/`MemorySourceQueryTool` 时，输入、分页、权限、错误码、字段和 canonical JSON 必须与现有 golden contract 完全一致，不出现 marker。仅当该结果作为普通工具历史回放给模型时，controller 才可在外层 tool message content 前加完成时间 marker；去掉前缀后 body 必须逐字等于工具返回值。

不要修改：

```text
src/bai_agent/tools/memory_source.py
```

## 9. 性能、兼容性与完整回归

```bash
pytest tests/performance/test_temporal_annotation_scale.py -q -s
pytest -m "not performance" -q
python -m pytest tests/integration/test_repository_secret_safety.py -q
```

主要性能验收使用 10,000 个预构造日志项，不把 fixture 创建或文件 I/O 计入标注窗口；Ubuntu 24.04/Python 3.13 单次标注必须 `<1.0s`，输出位置/数量同时验真，不能只测耗时。另用 10,000 raw + 1,000 long-term sources 证明单次索引和线性引用解析。

完整回归必须特别包含：

- 既有 raw/long-term 文件无需迁移或正文/UTC 改写；
- prompt debug、context estimation、DeepSeek tool calls 和 curation 测试全部通过；
- `memory_source_query` 黄金合同不变；
- 启动、TUI 与既有性能门禁无实质回退；
- 仓库及测试夹具无凭据泄漏。

## 10. 文档一致性检查

实现阶段应同步检查：

```bash
rg -n "history_timestamps|Asia/Shanghai|30|120|时间范围|memory_source" README.md specs/001-persistent-memory-agent specs/002-prompt-trace-debugger specs/003-timestamped-memory-context
```

README、001 配置/模型工具/存储合同、001/002 quickstart、002 model-call contract 和本功能文档必须对默认值、七个逻辑区块、预算/来源、UTC 不迁移及来源工具排除给出一致说明。文档修改与对应重大实现处于同一原子提交；若现有 compatibility workflow 已执行所有新增非性能测试，可记录 workflow 修改为 N/A，否则同步新增命令。
