# 动画管家稳定基线与回滚说明

## 1. 部署形态

当前动画管家运行在 Windows 本地进程/服务中，不是 Docker 部署。本机没有 Docker CLI，因此本机“镜像”定义为一个经过校验的不可变回滚快照：

- Git HEAD 的 tracked source ZIP；
- Git repository bundle；
- 当前 tracked patch 与必要的 untracked 文档/脚本；
- Windows DPAPI LocalMachine 加密的 `.env`（仅同一台机器可恢复）；
- SQLite 在线一致性备份与 integrity report；
- 小型 JSON/YAML 任务身份和状态；
- Python 版本与依赖冻结；
- Gateway/WebUI/ComfyUI 健康快照；
- ImageClip Git bundle 与工作流 SHA；
- 全部 payload SHA-256 清单。

GPU Control 的 API/Scheduler/Web/节点 worker 是容器部署，其镜像 digest 必须由 GPU Control 团队通过 V4.1 回执返回；本机不能代替远端备份这些镜像。

## 2. 已签发的稳定基线（2026-07-30）

- 快照 ID：`20260730-115717_stable-pre-v4_1_1f84ceba1276`
- 快照目录：`C:\assetclaw-matting-bot\storage\rollback_snapshots\20260730-115717_stable-pre-v4_1_1f84ceba1276`
- 动画管家 Git HEAD：`1f84ceba1276a3a3df8c07a44fb813cd91bb3638`
- ImageClip Git HEAD：`691770cd6a59fd7c51391456fe900dc57a313233`
- payload：2,098 个已签名文件；总目录约 4.83 GiB
- SQLite：17,838,080 bytes；17 张业务表；`integrity_check=ok`、`quick_check=ok`
- SQLite SHA-256：`04D91FD3CD87C261E742D5A6E764C80795B929BFE8B54EBCC3745A75A58AC96F`
- `.env`：DPAPI LocalMachine 加密，并通过内存解密 SHA-256 回环校验
- 创建后 Gateway：`ok=true`；服务名 `assetclaw-win3090-animation-butler`
- 快照验证：通过全部 payload SHA-256、数据库报告及两个 Git bundle HEAD 验证

该基线解决“本次代码/配置优化出现故障时快速恢复”的问题。它保存在本机同一磁盘，不能替代异机灾备；GPU Control 容器镜像也不包含在本机快照中，必须由 GPU Control 团队返回镜像 digest 和可回滚版本。

## 3. 创建快照

该过程不停服务、不重启、不取消任务：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\create_stable_rollback_snapshot.ps1 `
  -Label stable-pre-v4_1 `
  -GitExe <git.exe> `
  -PythonExe <assetclaw-python.exe>
```

输出位于：

```text
storage/rollback_snapshots/<timestamp>_<label>_<git-short-sha>/
```

只有存在 `snapshot_complete.json` 且验证脚本通过的目录才是有效快照。存在 `SNAPSHOT_FAILED.txt` 的目录禁止用于回滚。

## 4. 验证快照

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\verify_stable_rollback_snapshot.ps1 `
  -SnapshotRoot <snapshot-directory>
```

验证内容包括所有 payload SHA-256 和 SQLite integrity report。

## 5. 默认回滚：应用代码/配置，保留运行数据

这是新版本出现异常时的默认方案。目标是恢复旧代码和配置，同时保留最新的：

- `data/assetclaw.db`；
- `storage/` 任务状态和产物；
- GPU Control batch ID、external ID、幂等键和 cancel audit；
- 日志和业务文件。

步骤：

1. 停止扩大流量，但不要取消远端批次；
2. 记录所有 RUNNING 任务及远端 batch ID；
3. 再创建一份“回滚前快照”，保留故障现场；
4. 使用 `scripts/stop_bot_local.ps1` 停止 Gateway/WS/WebUI；不要停止独立业务 worker 或 ComfyUI，除非故障处理负责人明确决定；
5. 验证目标快照；
6. 从 `code/tracked_head.zip` 恢复 tracked source，或把 Git 工作树恢复到 manifest 中的 `git_head`；
7. 使用同一 Windows 用户解密 `config/env.dpapi` 并恢复 `.env`；
8. **不要恢复旧数据库**；
9. 使用 `scripts/start_bot_local.ps1` 启动；
10. 检查 Gateway `/health`、WebUI、飞书 WS、ComfyUI 和远端 batch 重新挂接；
11. 确认没有生成第二个 generation/batch 后再恢复流量。

DPAPI 解密必须在创建快照的同一台 Windows 机器上执行：

```powershell
$encrypted = [IO.File]::ReadAllBytes('<snapshot>\config\env.dpapi')
$plain = [Security.Cryptography.ProtectedData]::Unprotect(
  $encrypted,
  $null,
  [Security.Cryptography.DataProtectionScope]::LocalMachine
)
[IO.File]::WriteAllBytes('<project>\.env', $plain)
```

## 6. 数据回滚：仅限数据库损坏

旧数据库可能缺少修改后创建的任务、远端 batch ID 和投递回执。覆盖数据库可能制造重复任务或孤儿批次，因此禁止自动执行。

只有同时满足以下条件才考虑数据回滚：

- 已确认当前数据库损坏且无法修复；
- Gateway、WS、WebUI 和所有会写数据库的 worker 已停止；
- 本机和 GPU Control 所有父批次已对账；
- 当前损坏数据库已另行在线/离线备份；
- 明确评估快照时间之后会丢失哪些任务和回执；
- 负责人书面确认恢复点。

数据回滚后必须运行 `PRAGMA integrity_check`，并逐项核对远端 batch。禁止在有状态不明的远端批次时恢复旧数据库。

## 7. 回滚成功判定

- Gateway `/health` 返回 `ok=true`；
- 飞书 WebSocket 保持连接；
- WebUI 可查询任务；
- ComfyUI 本机与 GPU Control readiness 正常；
- 修改前存在的远端 batch 仍使用原 ID；
- 没有新 generation、重复上传或错误取消；
- 新测试任务完成并有投递回执；
- 日志中无数据库 schema、import、workflow identity 错误。

## 8. 强制安全边界

- 不使用 `git reset --hard` 作为默认回滚；
- 不删除 `storage/`、`data/` 或 GPU Control 批次；
- 不在服务运行时覆盖 SQLite；
- 不把系统超时当成用户取消；
- 不用旧数据库覆盖新的 batch ID；
- 不在没有 SHA 验证时使用快照；
- 不在 GPU Control 未提供镜像 digest 时宣称远端可回滚。
