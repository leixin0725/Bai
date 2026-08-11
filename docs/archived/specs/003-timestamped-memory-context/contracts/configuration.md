# Contract: 历史时间标注配置

**File**: `config/history_timestamps.toml`

**Schema version**: 1

**Asset identity**: `config:history_timestamps`

## Canonical default

```toml
schema_version = 1
display_timezone = "Asia/Shanghai"
long_gap_minutes = 30
continuous_segment_refresh_minutes = 120
split_on_local_date_change = true
```

固定 marker 模板不属于配置，任何字段都不能覆盖中文标签或输出结构。

## Fields

| Field | Type | Required | Bounds/relationship | Meaning |
|---|---|---:|---|---|
| `schema_version` | integer | yes | exactly `1`; bool forbidden | 配置合同版本 |
| `display_timezone` | string | yes | non-empty, resolvable IANA zone | 仅用于 marker 显示和本地日期比较 |
| `long_gap_minutes` | integer | yes | `1..1440`; bool forbidden | 相邻有效间隔达到此值时分段 |
| `continuous_segment_refresh_minutes` | integer | yes | `1..10080`, `>= long_gap_minutes`; bool forbidden | 自最近 marker 承载项起达到此值时刷新 |
| `split_on_local_date_change` | boolean | yes | strict bool | 本地自然日变化时是否分段 |

未知字段、重复语义别名、字符串数字、浮点数字和额外 table 全部拒绝。配置文件缺失、不可读、TOML 语法错误或 schema version 不支持也全部拒绝；不使用静默默认值继续运行。

## Loading and validation

1. `config.loader.MANIFESTS` 把本文件作为与 `agent.toml` 等相同级别的必需 manifest 读取。
2. 原文、project-relative path、SHA-256 与其他资产共同形成 `ConfigSnapshot.revision`。
3. 专用 validator 先校验精确字段/类型/范围/关系，再用 `ZoneInfo(display_timezone)` 解析。
4. `build_application()` 从该 snapshot 构造一个 immutable policy/annotator，注入 assembler、curation 和 controller。
5. 重载先完整加载并构造所有新依赖；成功后在轮次边界整体替换。任何失败都不得局部替换消费者。

## Reload behavior

- 有效修改在下一次既有配置 reload/turn boundary 整体生效。
- 已经开始构建的 prompt、curation 或 tool continuation 保持原 snapshot；同一请求不得混入新值。
- 无效修改在受影响请求、raw mutation 和工具副作用前返回可操作错误；错误至少指出 path 与字段/关系，不回显其他配置正文或凭据。
- 修复文件后的下一次边界可正常加载；不需要重写存档或清除记忆。

## Source and trust

`ConfigAsset` 必须包含：

```text
asset_id: config:history_timestamps
kind: history_timestamp_policy
project_relative_path: history_timestamps.toml
content_sha256: <real SHA-256>
revision: <same ConfigSnapshot revision>
```

每个 marker 的 provenance 引用这个真实资产以及触发日志项来源。配置影响数据展示，不因此成为 trusted prompt instruction；marker part 仍是 `UNTRUSTED_DATA`。

## Cross-platform timezone data

- Python 使用标准库 `zoneinfo`；项目声明 `tzdata>=2026.3` 作为 Ubuntu/WSL 环境下 IANA 时区数据的确定性后备。
- 各 Ubuntu/WSL 环境对同一 IANA zone/instant 必须输出相同本地日期、时间和 offset。
- 不接受本机 locale 名称、`UTC+8` 自定义字符串或仅固定 offset 代替 IANA zone。
- 原始 persisted datetime 不随 `display_timezone` 变更；只有新构建的 marker 变化。

## Error cases

| Input | Result |
|---|---|
| file missing | configuration error before request |
| `long_gap_minutes = true` | type error, not integer 1 |
| gap `0` or `1441` | bounds error |
| refresh `29`, gap `30` | relationship error |
| unknown `marker_format` | unknown-field error |
| `display_timezone = "Local"` | invalid IANA timezone error |
| valid gap 60 replacing 30 | next full snapshot uses 60 for every consumer |

## Automated evidence

- 单元/合同：必需字段、unknown、bool/int、range、relationship、IANA、DST、asset identity/revision。
- 集成：有效修改在下一轮让 45 分钟 gap 从“分段”变为“不分段”；聊天、curation、tool 使用同一 snapshot。
- 失败原子性：无效 reload 后 provider 调用数、raw 写入数和工具执行数均为 0，无消费者保留部分新策略。
- 打包：wheel/sdist 包含默认配置安装路径所需资产，Ubuntu/WSL 都能解析 `Asia/Shanghai`。
