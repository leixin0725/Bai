# Contract: 配置分组与热重载（阶段 1）

> 2026-08-08。本契约补充既有 `specs/001-persistent-memory-agent/contracts/configuration.md`；既有加载、资产、来源与凭据语义不变。

## 1. 配置分组

每个 TOML 清单文件与人格/提示文件构成一个分组（见 [data-model.md](../data-model.md) 第 5 节）。分组用于：

- 启动与运行中校验的错误定位：错误信息必须指出 `分组 ID` 与字段/文件。
- `config validate` 的分组状态输出。

分组不是独立生效单元：**生效始终以整份 `ConfigSnapshot` 原子切换**。

## 2. 重载语义

- 触发点：每个新对话动作开始前执行一次重载（沿用现有 `AgentApplication._reload_config()`）。
- 成功：整份新快照校验通过后，原子替换控制器、人格、组装器、provider、整理服务与工具；进行中的轮次继续使用开始时的快照（BR-003）。
- 失败：整份重载失败，系统保持最后有效快照与控制器继续运行；**在下一对话动作开始前 MUST 向 stderr 输出明确警告**，内容包含分组、字段、失败原因与继续使用的配置 revision，**禁止静默回退**；`RuntimeStatus.last_reload` 同步记录失败与错误；修复文件后下一动作重试并恢复，无需重启、无需迁移数据。
- 凭据门禁：重载使用与启动相同的 `require_credentials` 策略；缺失环境变量属于重载失败，不混用旧 provider 与新配置。

## 3. 错误合同

`config validate` 成功输出新增分组状态：

```json
{
  "ok": true,
  "config_revision": "sha256:...",
  "groups": {
    "agent": "ok",
    "providers": "ok",
    "states": "ok",
    "tools": "ok",
    "history_timestamps": "ok",
    "personas": "ok",
    "prompts": "ok"
  }
}
```

失败输出保持既有 `{"ok": false, "error": {...}}` 结构，并在错误消息中包含分组 ID 与具体字段；不输出配置正文或凭据值。

运行时重载失败警告（stderr）示例：

```text
[警告] 配置重载失败，继续使用 config_revision=sha256:abcd1234...
  分组: history_timestamps
  字段: long_gap_minutes
  原因: 必须是 1..1440 的整数
```

该警告 MUST 在动作开始前打印；`RuntimeStatus.last_reload` 必须与警告一致：`ok=false`、`error` 含同一分组/字段/原因、`revision` 为继续使用的旧 revision。修复并成功重载后，警告消失且 `last_reload.ok=true`。

## 4. 不变式

- 数据不迁移：既有 `data/`、原始记录与长期记忆继续兼容，重载不触碰存储。
- 不引入配置监听线程或守护进程；修改在下一个对话动作边界生效。
- `:status` 的 `last_reload` 与真实重载结果一致（BR-007）。
