<!-- [2026-07-21] 人格与可见边界规则位于 system；每个不可信变量只包装一次。 -->
批次元数据：
$batch_metadata
待整理原始记录（时间 marker 独占一行，每条记录保持一行 canonical JSON）：
$batch_records
现有长期记忆（时间范围 marker 独占一行，每项保持一行 canonical JSON）：
$existing_memories
现有覆盖概览（时间范围 marker 独占一行，概览保持 canonical JSON）：
$current_overview

时间 marker 仅属于输入上下文，不是记录 JSON 或输出字段。
只返回符合 $output_schema 的 JSON，其中包含 memory_candidates 与 overview_update。
