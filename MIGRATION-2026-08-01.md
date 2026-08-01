# Bai 迁移记录（2026-08-01）

本文记录 Bai 从 `D:\SchoolWork\Self\Bai` 迁移到 `D:\_Dev\Bai` 的操作重点、完整性证据、迁移期间发现的问题和后续排查方法。时间基准为 Asia/Shanghai。

## 1. 迁移结论

| 项目 | 结果 |
| --- | --- |
| 原目录 | `D:\SchoolWork\Self\Bai`，迁移后不存在 |
| 新目录 | `D:\_Dev\Bai` |
| 迁移方式 | D 盘内原子目录移动，完整保留工作树、`.git` 和忽略文件 |
| Git 分支 | `main` |
| 迁移时 HEAD | `37283c2df3b7065f49bd2135d2b3b314bf4150b3` |
| HEAD 说明 | `feat: 增强 TUI 折叠模式` |
| 提交数量 | 41 |
| Python | 3.13.7 |
| 最终验证 | 356 项通过，2 项按平台条件跳过 |

旧目录已经消失，新目录可正常导入 `bai_agent`、加载 CLI、校验配置和记忆数据。迁移和验证过程没有向真实 DeepSeek API 发起请求。

## 2. 数据与 Git 完整性

### 2.1 真实记忆数据

迁移前后均对 `data\memory` 下的相对路径、文件长度和单文件 SHA-256 生成聚合 SHA-256：

- 文件数：3
- 总字节数：28,078
- 聚合 SHA-256：`8C818702E51AEA336FC6C495F2F3DF88B445FD9952123C18686EE32499B2B978`
- 迁移前后结果：完全一致

最终应用级校验结果：

- `raw_records`：34
- `long_term_items`：5
- `coverage_spans`：2
- `coverage_gaps`：0
- `dangling_sources`：0
- `curated_through_sequence`：20
- `direct_range`：21–34

### 2.2 Git 历史

`.git` 随项目整体迁移，并非重新初始化。迁移后确认：

- `git rev-parse --is-inside-work-tree` 返回 `true`
- 仓库根目录为 `D:/_Dev/Bai`
- HEAD、分支、41 个提交和最近提交历史均与迁移前一致
- `git fsck --no-progress` 退出码为 0

`git fsck` 同时列出了若干既有 dangling commit/blob。这些对象不是仓库损坏，迁移时未执行 `git gc` 或删除操作，因此原样保留。

迁移前已有的未提交修改也得到保留：

- `config/agent.toml`：`short_term.max_records` 从 48 改为 24

## 3. 迁移期间实施的变更

### 3.1 路径和文档

- 将 `specs/002-prompt-trace-debugger/quickstart.md` 中的 Windows 路径从 `D:\SchoolWork\Self\Bai` 更新为 `D:\_Dev\Bai`。
- 对活动代码、配置、文档和新虚拟环境元数据执行旧路径扫描，结果为 0。
- 清除了 `src/` 和 `tests/` 中迁移前生成的 `__pycache__`，避免异常回溯继续显示旧目录。

### 3.2 Python 虚拟环境

旧 `.venv` 内存在两类旧路径：

- `.venv\pyvenv.cfg` 的创建命令
- editable install 的 `_editable_impl_bai_agent.pth`

因此没有直接复用旧环境，而是在新目录使用 Python 3.13.7 重建 `.venv`，执行：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

新环境验证通过后，旧虚拟环境回退副本已删除。迁移时实际安装的关键版本：

```text
bai-agent==0.1.0
editables==0.6
filelock==3.32.2
hatchling==1.31.0
hypothesis==6.164.0
openai==2.52.0
pydantic==2.13.4
pytest==9.1.1
pytest-asyncio==1.4.0
respx==0.22.0
ruamel.yaml==0.18.17
textual==8.2.8
tzdata==2026.3
```

项目没有依赖锁文件；以上是迁移时按 `pyproject.toml` 约束解析出的现场版本，不代表永久锁定。

### 3.3 配置热重载修复

完整测试发现一个迁移前已存在的恢复边界问题：

1. 配置被改成非法值，热重载正确失败且不产生业务副作用。
2. 配置随后恢复成与旧快照完全相同的内容。
3. 因修订号再次与旧快照相同，原实现提前返回，没有按测试契约重建控制器。

`src/bai_agent/application.py` 增加了内部 `_config_reload_required` 状态：失败后保持待重载，只有完整构造并原子发布新控制器后才清除。目标测试和最终全量测试均已通过。

## 4. 验证记录

最终验证使用新 `.venv`、禁用 pytest 缓存插件，并使用新的独立 `--basetemp`：

```text
356 passed, 2 skipped in 220.80s
```

两项跳过均为项目预期的平台门禁：

- TUI 500 ms 强制性能门禁仅在 Ubuntu 24.04 / Python 3.13 运行
- Windows 3 秒启动门槛仅在显式启用的参考环境运行

另外确认：

- `config validate`：通过
- `memory validate`：通过
- `doctor`：通过，`network_probe=false`
- `python -m bai_agent --help`：通过
- 打包相关测试：包含在全量测试中并通过
- `git diff --check`：通过
- 活动文件旧路径扫描：无结果

离线配置校验需要凭据变量存在。验证时仅在单个 PowerShell 进程内注入明确无效的占位值，并在 `finally` 中删除；未持久化或使用真实凭据。

## 5. 已知测试残留与 ACL 注意事项

项目迁移前已经存在由 Windows 私有权限测试留下的受限目录：

- `.pytest_cache`
- `data\doctor-test`
- `data\permission-test`

其中旧 `.pytest_cache` 的 ACL 拒绝当前非提升令牌访问，无法在本次迁移中删除。它是 Git 忽略的测试缓存，不参与 Bai 运行；最终测试使用 `-p no:cacheprovider`，不会读取它。该缓存内部即使存在旧绝对路径，也不代表活动代码或环境仍引用旧目录。

`data\doctor-test` 和 `data\permission-test` 是固定路径测试夹具，不是真实记忆数据。各轮测试产生或发现的实例均已从活动 `data` 目录移到 Git 忽略的 `.tmp` 隔离区。迁移完成时：

- `data\memory` 保持完整
- `data\doctor-test` 不存在
- `data\permission-test` 不存在
- `.venv.migration-backup-20260801` 不存在

`.tmp` 中保留了迁移验证的 basetemp 和 ACL 隔离目录。部分子目录可能需要提升权限才能删除；它们不参与运行，也不会出现在普通 `git status` 中。

## 6. 后续排查

### 6.1 基础运行检查

```powershell
Set-Location D:\_Dev\Bai
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m bai_agent --help
.\start.ps1
```

`start.ps1` 会安全读取 DeepSeek API Key，并从脚本所在目录启动新 `.venv`。

### 6.2 离线健康检查

不要把真实密钥写进命令历史。以下占位值只用于通过“变量必须存在”的配置门禁，相关命令不会探测网络：

```powershell
$env:DEEPSEEK_API_KEY = 'invalid-local-validation-only'
try {
    .\.venv\Scripts\python.exe -m bai_agent --config-dir config --data-dir data config validate
    .\.venv\Scripts\python.exe -m bai_agent --config-dir config --data-dir data memory validate
    .\.venv\Scripts\python.exe -m bai_agent --config-dir config --data-dir data doctor
} finally {
    Remove-Item Env:\DEEPSEEK_API_KEY -ErrorAction SilentlyContinue
}
```

### 6.3 全量测试

由于旧 `.pytest_cache` 的 ACL 状态，建议继续禁用缓存并为每次运行指定一个全新临时根：

```powershell
$testTemp = ".tmp\pytest-local-$(Get-Date -Format yyyyMMdd-HHmmss)"
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp $testTemp
```

如果重复运行固定路径的 doctor/permission 测试，先确认 `data\doctor-test` 和 `data\permission-test` 不存在。它们不是 `data\memory`，不要对真实记忆目录执行清理。

### 6.4 检查旧路径

```powershell
rg -n --hidden `
  --glob '!.git/**' `
  --glob '!.venv/**' `
  --glob '!.tmp/**' `
  --glob '!.pytest_cache/**' `
  --glob '!*.pyc' `
  'D:\\SchoolWork\\Self\\Bai|D:/SchoolWork/Self/Bai' .
```

新虚拟环境还应单独检查：

```powershell
Get-Content .venv\pyvenv.cfg
.\.venv\Scripts\python.exe -m pip show bai-agent
```

两处均应指向 `D:\_Dev\Bai`。

## 7. 本次提交范围与说明

本文档与迁移相关改动使用同一个原子提交，提交主题为：

```text
chore: 完成 Bai 项目迁移并记录验证结果
```

提交范围：

- `config/agent.toml`：纳入迁移前用户已有的 `short_term.max_records = 24` 修改
- `specs/002-prompt-trace-debugger/quickstart.md`：更新 Windows 项目路径
- `src/bai_agent/application.py`：修复非法配置恢复后的强制热重载
- `MIGRATION-2026-08-01.md`：保存完整迁移证据、环境快照、ACL 注意事项和排查命令

commit message 保留路径迁移、数据摘要、Git 历史、热重载修复和最终测试结果等便于从日志检索的摘要；本文档保存不适合塞入 commit message 的完整哈希、依赖版本、验证过程与后续操作说明。

本次提交不执行推送、历史重写、`git gc` 或忽略目录清理。
