# 动画管家 ↔ GPU Control 抠图交接协议 v1（已由 V2 取代）

> 历史设计记录。当前实现与联调以 [GPU_CONTROL_MATTING_HANDOFF_V2_IMPLEMENTATION.md](GPU_CONTROL_MATTING_HANDOFF_V2_IMPLEMENTATION.md) 为准。

状态：调用方实现完成，等待双方联调验收后启用 `hybrid`。

本文是动画管家对 `36_2026-07-24_ANIMATION_BATCH_MATTING_DESIGN.md` 和
`37_ANIMATION_MANAGER_BATCH_API_CONTRACT_DRAFT.md` 的正式回应，并固定动画管家侧的实现选择。

## 1. 职责边界

- GPU Control **只执行 ImageClip RGBA 抠图**，不读取飞书、不抽帧、不跑 Cherry、不编码视频、不打业务 ZIP、不发送结果。
- 动画管家继续负责表格读取、飞书图片/视频/ZIP 接收、下载、抽帧、Cherry、视频编码、序列帧 ZIP、结果发送、任务状态和失败通知。
- 本机 4070Ti 保留完整的本地抠图能力。`hybrid` 模式下，本机空闲且任务较小时走本机；本机已有抠图任务或任务达到阈值时，整个抠图调用交给 GPU Control。
- v1 不把一个 `COMFY_*` 抠图调用拆到两种后端。一个调用从开始到结束只属于 `local` 或 `gpu_control`。

## 2. 不可违反的隔离红线

1. 每个 `COMFY_*` 调用生成唯一 `external_batch_id`，独占一个工作目录、一个输入 ZIP、一个 manifest 和一个输出目录。
   同一父任务执行红线修复或重新抠图时递增 `g1/g2/...` generation，绝不复用旧内容的幂等键。
2. 不同父任务的帧不得进入同一个 ZIP 或远端 `batch_id`。
3. ZIP 内路径使用该调用输入根目录下的 POSIX 相对路径；禁止绝对路径、`..`、反斜杠和规范化重名。
4. `ordinal` 必须从 0 连续到 `total-1`；输入文件名、相对目录、大小和 SHA-256 是业务真值。
5. 结果只有在整包、逐帧映射、逐帧 SHA-256、PNG/Alpha 和可选帧身份校验全部通过后，才一次性替换该调用的 matte 目录；`skip_existing` 场景会把已完成输出原样并入原子发布目录。
6. `PARTIAL`、`FAILED`、缺帧、多帧、错序、错名、错 SHA、无透明 Alpha 均视为整批失败，不进入 Cherry 或发送阶段。

## 3. 创建请求（固定）

```http
POST /api/v1/batches/imageclip-rgba
Content-Type: multipart/form-data
Idempotency-Key: <external_batch_id>
X-Request-ID: <comfy_run_id>-create
X-API-Key: <configured secret, when enabled>
```

multipart 字段固定为：

- `archive`：ZIP_STORED 输入包；包内只有输入图片。
- `manifest`：UTF-8 JSON，`schema_version=1.0`、`failure_policy=all_or_nothing`、
  `output_naming=preserve_stem_png`。

manifest 示例：

```json
{
  "schema_version": "1.0",
  "external_batch_id": "assetclaw:IMG_ABC123:matting:g1",
  "failure_policy": "all_or_nothing",
  "output_naming": "preserve_stem_png",
  "parameters": {},
  "frames": [
    {
      "ordinal": 0,
      "relative_path": "image_01/01_frame_0001.png",
      "size_bytes": 123456,
      "sha256": "<64 lowercase hex>"
    }
  ]
}
```

同一个 `Idempotency-Key` 的重试必须复用磁盘上已经固化的同一 ZIP 和 manifest。若源文件发生变化，动画管家拒绝重试并要求创建新任务。

## 4. 状态、恢复和终态（固定）

- 创建成功接受 HTTP `200` 或 `202`，动画管家持久化返回的 `batch_id`。
- 状态查询固定为 `GET /api/v1/batches/{batch_id}`。
- 进度只用于展示；成功依据固定为：`status=SUCCEEDED`、`counts.total=本地总数`、
  `counts.succeeded=本地总数`、`counts.failed=0`。
- 动画管家进程或轮询恢复后，使用持久化的 `batch_id` 继续查询，不创建新业务批次。
- 创建请求发生超时且是否已受理不明确时，仍使用原 `Idempotency-Key` 重试。
- v1 不做“远端失败后静默改走本机”，避免同一业务调用出现双写和结果竞争。
- 远端不支持暂停；用户可取消并新建任务。

取消请求：

```http
POST /api/v1/batches/{batch_id}/cancel
Idempotency-Key: <external_batch_id>:cancel
```

## 5. 结果返回（固定）

动画管家只接受 `SUCCEEDED` 状态中的 `kind=result_archive` artifact，并通过其 `download_url` 下载。

结果 ZIP 固定包含：

```text
manifest.json
results/<input-relative-path-with-.png-suffix>
```

内部 `manifest.json` 固定包含 `total` 和按 ordinal 对应的 `items`（兼容字段名 `frames`）。每项至少包含：

```json
{
  "ordinal": 0,
  "input_relative_path": "image_01/01_frame_0001.png",
  "input_sha256": "<original input sha256>",
  "output_relative_path": "image_01/01_frame_0001.png",
  "output_sha256": "<rgba png sha256>",
  "status": "SUCCEEDED",
  "job_id": "...",
  "node_id": "worker-3090-a",
  "attempts": 1
}
```

发布顺序固定为：整包 SHA → ZIP 路径/重名 → manifest 总数/ordinal → 输入映射和 SHA →
输出映射和 SHA → PNG/Alpha → 可选帧身份 → 同盘 staging 原子替换。任何一步失败，旧输出目录保持不变。

## 6. 路由与并发

`MATTING_BACKEND_MODE`：

- `local`：全部抠图由本机 4070Ti 执行（当前安全默认值）。
- `hybrid`：小任务且本机无活动抠图时走本机；达到阈值或本机忙时整批走 GPU Control。
- `gpu_control`：全部真实抠图走 GPU Control，用于联调和压测。

混合路由的“检查本机是否空闲 + 创建任务记录”由跨进程文件锁保护，防止两个任务同时误判本机空闲。
视频抽帧、飞书收发、Cherry、编码和打包不占用该抠图路由锁，可并发执行。

## 7. 配置与启用步骤

```dotenv
MATTING_BACKEND_MODE=local
GPU_CONTROL_BASE_URL=https://10.3.34.11
GPU_CONTROL_API_KEY=
GPU_CONTROL_VERIFY_TLS=true
GPU_CONTROL_CA_BUNDLE=
GPU_CONTROL_LARGE_BATCH_THRESHOLD=64
GPU_CONTROL_MAX_BATCH_FRAMES=5000
GPU_CONTROL_POLL_INTERVAL_SECONDS=3
GPU_CONTROL_EXECUTION_TIMEOUT_SECONDS=86400
```

联调顺序：

1. 保持 `local`，跑现有本机回归。
2. 临时设为 `gpu_control`，依次测试 1 帧、中文/嵌套路径、多帧、重复提交、取消、失败和结果篡改。
3. 验证任务隔离与原子发布后切换为 `hybrid`。
4. 联调期间不得关闭 TLS 校验；自签证书通过 `GPU_CONTROL_CA_BUNDLE` 配置企业 CA。

## 8. 双方验收清单

- GPU Control 确认创建、查询、取消、artifact 下载路径和字段名与本文一致。
- GPU Control 确认同一 idempotency key + 同一内容返回同一批次；同 key 不同内容返回冲突。
- GPU Control 确认 `SUCCEEDED` 前不会暴露不完整结果包。
- 动画管家确认四类入口（一键动画、图片直发、视频直发、序列帧 ZIP）只有抠图阶段可能出站到集群。
- 并发提交至少两个父任务，验证 ZIP、`external_batch_id`、`batch_id`、输出目录和返回文件完全不串任务。
- 人工制造缺帧、错序、重复规范化路径、错 SHA、无 Alpha 和 `PARTIAL`，确认动画管家全部拒绝发布。
