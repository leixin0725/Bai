<!-- [2026-07-21] 模型只接收整理判断需要的紧凑语义视图。 -->
待整理原始记录（按事件时间排列；时间 marker 独占一行）：
$batch_records
现有长期记忆（按来源事件时间排列；时间范围 marker 独占一行）：
$existing_memories
现有覆盖概览（时间范围 marker 独占一行）：
$current_overview

时间 marker 仅用于理解上下文，不是输出字段。只整理上述批次；短来源别名只用于候选引用。
一次响应同时给出 memory_candidates 和 overview。
输出契约：
$output_schema
