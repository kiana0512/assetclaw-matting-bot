# GPU Control 抠图交接 V2（动画管家实现记录）

更新时间：2026-07-24

## 冻结依据

- 上游合同：`GPU_CONTROL_MATTING_HANDOFF_V2.md`
- 合同 SHA-256：`5b80a284b770a8f45eda827890b8b5ef6c2acca794afbceaddc55cf4030bdd1d`
- 服务入口：`https://10.3.34.11`
- 协议：GPU Control `1.2.0`，manifest `1.0`

旧版 [GPU_CONTROL_MATTING_HANDOFF_V1.md](GPU_CONTROL_MATTING_HANDOFF_V1.md) 仅保留为历史设计记录，不再作为联调依据。

## 动画管家职责边界

GPU Control 只负责 ImageClip RGBA 抠图。飞书图片直发、视频直发、序列帧 ZIP 与一键动画流程仍由动画管家完成接收、拆帧、后处理、编码、归档和回复；动画管家只把每个业务父任务的不可变图片集合交给远端。

每个父任务拥有独立的 `external_batch_id`、输入 ZIP、manifest、远端 `batch_id`、结果 ZIP、staging 与最终发布目录。不同父任务不得共享工作目录或合并结果。

## 已实现的 V2 红线

- `manifest` 使用 multipart 普通字符串字段，`archive` 使用文件字段。
- 输入为 `ZIP_STORED`，1～5000 帧；仅 JPEG、PNG、WebP；逐帧校验大小、像素、解码、路径和 SHA-256。
- `parameters` 固定 `{}`；不发送 V1 草案字段。
- 上传超时 86400 秒；状态默认每 3 秒轮询；活动状态不会重建批次。
- 终态只接受 `SUCCEEDED`、`FAILED`、`CANCELLED`。
- 下载时同时核对 artifact 元数据、`X-Artifact-SHA256` 与下载字节 SHA。
- 结果严格核对 batch/external ID、字段、帧数、0..N-1 顺序、输入映射、输出路径、逐帧 SHA、PNG Alpha 与 ZIP 精确文件集。
- 全批验证通过后才进行 staging 原子发布；任一错误不会覆盖正式目录。
- 默认 `MATTING_BACKEND_MODE=local`，所以代码更新不会自动迁移正在运行的后端。后续重启时才可按运维窗口切换 `hybrid` 或 `gpu_control`。

## 不重启后端的真实验收

独立脚本不会访问动画管家数据库、不会修改 `.env`、不会调用本机 ComfyUI，也不会停止或重启现有服务：

```powershell
python scripts/run_gpu_control_v2_live_acceptance.py --frames-per-task 6
```

只构造并验证四套交接包、不发网络请求时使用 `--prepare-only`。

它并发创建四个隔离批次：`direct-image`、`direct-video`、`sequence-zip`、`one-click-animation`。报告和结果写入：

```text
storage/gpu_control_live_tests/<session>/report.json
```

若生产使用内部 CA，传入 `--ca-bundle <lan-ca.crt>`；若使用 API 客户，传入 `--api-key <key>`。两者都只覆盖当前验收进程，不写入配置文件。

报告保存 external/batch ID、创建请求 ID、状态历史、节点分布、artifact 三层 SHA、逐帧路径与节点、发布目录。测试中断不会擅自取消已接单批次，避免破坏服务端排障现场。

## 当前 LAN CA 的 Python 3.13 兼容说明

2026-07-24 收到的 `GPU_CONTROL_LAN_CA.crt` SHA-256 为：

```text
ad4a4dbd95bb789be03451ff0c25b2bc65dfe170428bd675789c2ebba1e6dc2b
```

该 CA 能验证 `10.3.34.11`，但证书没有 `keyUsage` 扩展。Python 3.13/OpenSSL 3 的 `VERIFY_X509_STRICT` 会报 `CA cert does not include key usage extension`。动画管家提供临时兼容开关：

```text
GPU_CONTROL_ALLOW_CA_WITHOUT_KEY_USAGE=true
```

该开关只移除 OpenSSL 对 CA 扩展完整性的严格检查；证书链、签名、有效期和主机名/IP 校验仍然开启，绝不等同于 `verify=False`。集群重新签发包含 `critical keyUsage = keyCertSign, cRLSign` 与 `basicConstraints = CA:TRUE` 的 CA 后，应把开关恢复为 `false`。

## 2026-07-24 真实验收与生产切流

独立验收会话 `v2-live-20260724-1435` 已通过 4/4 个父批次、24/24 帧。报告位于 `storage/gpu_control_live_tests/v2-live-20260724-1435/report.json`。图片直发、视频直发、序列帧 ZIP 与一键动画分别使用独立 external/batch ID；全部通过路径、顺序、PNG Alpha、逐帧 SHA、结果 ZIP SHA 和原子发布校验。

随后将 4 个仍处于 `waiting_pipeline_queue`、尚未抽帧或创建本地 ComfyUI 子任务的视频父任务平滑迁移到 GPU Control。迁移只终止等待锁的旧 worker，不触碰正在本机处理的任务：

| 父任务 | 帧数 | GPU Control batch |
| --- | ---: | --- |
| `VID_AF9BE191FC9A` | 118 | `71329961-7ed4-4d2d-b88a-dc8be8b2d0a0` |
| `VID_58BFF279115F` | 128 | `0123925d-121c-4e9a-8797-7430303b67e7` |
| `VID_0E6A798184FD` | 118 | `9683dce9-8802-49bd-810b-49280f2b289a` |
| `VID_B284DA228D08` | 55 | `3df4c180-9676-4df1-84f7-096cfc94a3f3` |

迁移脚本为 `scripts/migrate_waiting_video_queue_to_gpu_control.ps1`。它只接受 `RUNNING + waiting_pipeline_queue` 的父任务，验证 PID 属于 Python 后才迁移，并为每个父任务启动独立进程、独立 external ID 和独立远端批次。

后续生产使用 `MATTING_BACKEND_MODE=hybrid`：小任务且 4070 Ti 空闲时留在本机；达到阈值或本机已有抠图任务时交给集群。每次远端路由前先做 readiness 与可选容量握手。详细的服务端同步约定见 [GPU_CONTROL_SCHEDULER_HANDSHAKE_V2_1.md](GPU_CONTROL_SCHEDULER_HANDSHAKE_V2_1.md)。

滚动热更新验证中，`VID_B284DA228D08` 的动画管家父 worker 退出时，GPU Control 批次仍继续运行。飞书接收器恢复扫描通过持久化的 `remote_batch_id=3df4c180-9676-4df1-84f7-096cfc94a3f3` 重新挂接原批次，未重复创建、未重传输入、未改变 55 帧的 ordinal/路径。Windows 存活检测使用 `psutil`，避免 `os.kill(pid, 0)` 对高 PID 的误判导致重复恢复。

## 热更新与验收记录

- `scripts/reload_gpu_control_routing.ps1` 只滚动重启 Gateway 与飞书 WebSocket 接收器；ComfyUI、WebUI 和独立视频 worker 不在重启范围内。
- Gateway `/health` 返回 `ok=true`；飞书接收器启动日志包含恢复扫描结果并保持 WebSocket 监听。
- WebUI 生产构建通过；浏览器实测后端在线、4 个 GPU Control 批次均显示真实 batch ID、节点分布和递增的 `completed/total`。
- 本次 GPU Control/Cherry 相关回归为 `21 passed`。完整旧测试集中另有 3 个 `agent.current_work` 展示格式断言仍要求历史表格文案，以及一个 Unity timeout 用例会结束 pytest 进程；它们不在本次抠图路由改动文件中，未为凑测试结果修改现行交互或生产逻辑。
- 所有测试夹具和误入任务中心的 `IMG_ORPHANED` 测试记录均已清理；生产父任务与远端批次未删除、未取消。
