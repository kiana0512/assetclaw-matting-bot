# C 盘清理执行记录（2026-08-12）

## 1. 结果

- 清理前 C: 空闲：约 `282.16 GiB`。
- 清理后 C: 空闲：约 `360.64 GiB`。
- 实际释放：约 `78.48 GiB`。
- AssetClaw 健康检查：通过。
- 秋叶 ComfyUI：未修改。
- 当前数据库：保留，`data/assetclaw.db`，约 29 MiB。
- 稳定回滚基线：保留 `storage/rollback_snapshots/20260730-115717_stable-pre-v4_1_1f84ceba1276`。

## 2. 已删除的项目数据

共删除 `1,316` 个精确目标、`47,046` 个文件，估算 `71.649 GiB`：

| 类别 | 删除量 | 处理 |
|---|---:|---|
| 视频任务可重建中间产物 | 25.296 GiB | 删除 `frames/matte/smooth/repairs/smooth_v379_backup` |
| 图片任务可重建中间产物 | 3.056 GiB | 删除 `matte/comparison/cherry_sequences/smooth` |
| GPU Control 批次 ZIP | 20.872 GiB | 删除 141 个终态批次的 `input.zip/result.zip` |
| 重复回滚快照 | 9.675 GiB | 删除 2026-08-05 与 2026-08-07 两份快照 |
| 旧审计数据库副本 | 8.353 GiB | 删除 `assetclaw.pre_audit_cleanup.20260723.db` |
| 旧飞书附件副本 | 2.098 GiB | 删除 2026-08-10 及以前 inbox 日期目录 |
| 旧图片解包副本 | 1.659 GiB | 删除 2026-08-11 以前导入目录 |
| 测试、验收和调试缓存 | 0.641 GiB | 删除 pytest、GPU 验收和 debug 缓存 |

执行前已确认：

- 104 个视频任务全部为 `DONE/CANCELED/FAILED` 终态。
- 170 个图片目录无运行中任务；有状态任务全部为终态，9 个无状态目录均为早期小残留。
- AssetClaw 服务健康且没有正在执行的图片/视频父任务。
- 所有递归删除目标解析后均位于 `C:\assetclaw-matting-bot\` 内。

## 3. 已删除的普通缓存

扫描 `46,449` 个缓存文件、计划 7.831 GiB；实际释放 `6.959 GiB`。删除范围：

- 当前用户 Temp 中未锁定内容。
- pip cache。
- uv cache。
- npm cache。
- VS Code `Cache/CachedData/CachedExtensionVSIXs/Code Cache/GPUCache/Service Worker CacheStorage/logs` 等明确缓存。

有 27 个正在使用的临时目标被系统锁定，未强制终止程序、未强删。

未删除 VS Code `User` 配置、未保存文件备份、扩展目录、飞书账号或聊天数据。

## 4. 明确保留的数据

| 数据 | 清理后核验 |
|---|---:|
| 视频最终 ZIP | 122 个 |
| 视频原始文件 | 104 个 |
| 视频 status.json | 104 个 |
| 图片最终 ZIP | 87 个 |
| 图片原始文件 | 1766 个 |
| 图片 status.json | 161 个 |
| GPU Control input manifest | 141 个 |
| GPU Control 大 ZIP | 0 个 |
| 当前数据库 | 1 个，约 29 MiB |
| 稳定回滚快照 | 1 个，约 4.831 GiB |

清理后主要业务目录：

| 目录 | 体积 |
|---|---:|
| `storage/direct_video_runs` | 27.005 GiB |
| `storage/direct_image_runs` | 5.620 GiB |
| `storage/rollback_snapshots` | 4.831 GiB |
| `storage/combined_deliveries` | 0.623 GiB |
| `storage/feishu_inbox` | 0.203 GiB |
| `storage/direct_image_imports` | 0.129 GiB |
| `storage/gpu_control_batches` | 0.002 GiB |

## 5. CodexSandboxOffline 结论

SpaceSniffer 把 `C:\Users\CodexSandboxOffline\.codex\.sandbox` 显示为约 111 GiB，但它是对真实工作区的虚拟/硬链接视图，并非等量的独立物理副本。

证据：真实项目内一个 1.162 GiB ZIP 与沙箱视图下对应文件具有完全相同的 NTFS File ID：

```text
0x00000000000000000006000000173bb1
```

项目清理 71.649 GiB 后，沙箱视图统计也从约 111 GiB 同步降至约 39.7 GiB，进一步证明 SpaceSniffer 在目录树中重复展示同一批文件。剩余沙箱视图对应本次明确保留的交付包、原素材和源码。

由于当前有多个 `codex-command-runner` 使用该视图，强制删除不会释放对应的 39.7 GiB 物理空间，反而会中断 Codex 工具。因此未强杀进程、未删除活动镜像；不应把这部分重复计入磁盘清理收益。

## 6. 后续保留策略

- 父任务进入终态且最终 ZIP 校验/交付完成后，定期删除 `frames/matte/smooth/repair/comparison`。
- GPU Control 父任务完成并由 AssetClaw 原子发布后，只保留 manifest 和紧凑审计字段，删除本地 input/result ZIP。
- 飞书附件和图片解包副本保留 48 小时；近期任务需要完整重跑时保留原素材副本。
- 至少保留一个经过验证的稳定回滚快照，不重复保存外部版本三份。
- C: 空闲低于 180 GiB 告警，低于 150 GiB 时禁止 4070 Worker 接新任务。
