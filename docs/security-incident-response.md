# 凭据泄露事件响应

> [2026-07-19] 本流程适用于 DeepSeek 及未来 Provider/工具凭据；任何疑似泄露在处置证据齐全前阻塞提交和交付。

1. 立即停止传播：暂停 Agent、构建、发布和相关日志转发，不复制疑似值。
2. 在凭据提供方撤销或轮换受影响凭据，并记录不含秘密的 rotation reference。
3. 扫描全部 tracked files、工作区/暂存差异、全部可达 Git 历史，以及 `build/`、`dist/` 等生成制品。
4. 扫描原始记忆、长期 YAML、运行日志和提示追踪；报告只保存逻辑制品 ID、不可逆指纹和范围。
5. 记录 repository scan revision、runtime/log scan revision 与处置记录编号。
6. 分次或一次确认处置证据：

```bash
python -m bai_agent security incident acknowledge --rotation-reference REF --repository-scan-revision REV --runtime-scan-revision REV --disposition-record RECORD
python -m bai_agent security incident check
```

`security incident check` 返回非零时不得提交或发布。只有四项证据齐全且全范围复查未发现可用凭据时，才能解除阻塞；若秘密进入了 Git 历史，必须按仓库托管策略完成历史清理与协作者协调后再记录扫描修订。
