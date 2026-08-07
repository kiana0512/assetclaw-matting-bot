# 动画管家性能优化后 GPU Control 对齐与回执清单

状态：`LOCAL DEPLOYED_NOT_ACCEPTED / GPU P0 PENDING / JOINT ACCEPTANCE PENDING`

生成日期：2026-07-30（Asia/Shanghai）

接收方：GPU Control / GPU 集群负责人

本文是 GPU Control V4.1 的第二轮对齐文档。它只描述本次动画管家已经实现的客户端行为、GPU 集群必须返回的字段，以及双方上线前要完成的验证。它不授权修改或重启 GPU Control 生产环境，也不代表联合验收已通过。

## 1. 结论先行

动画管家本地已经完成以下稳定性与速度改造：

1. 超过本地观察阈值不再自动取消远端批次，不会创建第二份相同任务；始终使用原 `batch_id` 恢复查询。
2. 用户取消先持久化取消意图，再调用父批次 cancel；没有本地取消意图的远端 `CANCELLED` 会被判为协议异常。
3. 工作流身份的任一非空字段与批准基线不一致时立即 fail closed；服务端暂缺字段会标记 `UNVERIFIED_MISSING`，但联合冻结前必须补齐。
4. 结果 ZIP 支持 `.part` + HTTP Range 断点续传，并在发布前继续执行 size/SHA、manifest、帧数、ordinal、路径和文件哈希验证。
5. 本机 4070 Ti 已忙时，小任务可进入 GPU Control 持久队列，使本机和集群并行工作；本机空闲且集群饱和时仍优先本机，避免小任务排队反而变慢。
6. GPU 路径和一键动画流程均新增 trace、阶段时间、attempt/状态变化和原子状态写入。
7. P4 旧版 `ai_art_comfyui` 高权限能力没有恢复；当前只保留 Spark UI `shelve-only` 安全合同，不影响抠图性能主链路。

GPU Control 仍需先解决父子取消、父成功前 child artifact 泄漏、取消审计、版本证据链、身份快照、兼容节点调度和 prompt 重复执行窗口。完成这些 P0 后，双方再做固定素材 A/B 和灰度。

## 2. 冻结的 ImageClip 身份

新生产任务的批准身份如下：

```yaml
workflow_key: imageclip-rgba
workflow_version: 2026.07.30-691770c-r1
imageclip_commit: 691770cd6a59fd7c51391456fe900dc57a313233
pipeline_sha256: 00e7104762f0a1fdf3a4c20e043bec2b9f088132452d5a5ce4302ba268edac0b
output_node: "SaveImage #25"
```

动画管家当前兼容策略：

- 返回字段非空且不匹配：立即失败，不下载、不进入 Cherry、不发布结果；
- 返回字段缺失：记录为 `UNVERIFIED_MISSING`，仅作为过渡兼容；
- 联合验收和正式冻结：create、每次父 GET、artifact manifest 必须完整返回全部身份字段，缺失即验收失败；
- 历史任务保留原身份，不回写或伪造成新版本。

## 3. 动画管家发送给 GPU Control 的内容

### 3.1 通用请求头

```http
X-API-Key: <configured secret>
X-Request-ID: assetclaw-<run-or-operation-id>
Idempotency-Key: assetclaw:<COMFY_RUN_ID>:matting:g1
```

约束：

- `X-Request-ID` 为 1–64 个 `[A-Za-z0-9._:-]` 字符；
- create/cancel 使用稳定幂等键，HTTP 重试不能产生新父批次或重复取消操作；
- poll request ID 形如 `<run_id>-poll-000001`，用于双方逐次对账；
- cancel request ID 和 idempotency key 独立于 create，但同一取消意图重试时保持不变。

### 3.2 创建父批次

```http
POST /api/v1/batches/imageclip-rgba
Content-Type: multipart/form-data
```

表单：

- `archive`: ZIP_STORED 输入 ZIP；
- `manifest`: UTF-8 JSON 字符串。

manifest 1.0：

```json
{
  "schema_version": "1.0",
  "external_batch_id": "assetclaw:COMFY_xxxxxxxxxxxx:matting:g1",
  "failure_policy": "all_or_nothing",
  "output_naming": "preserve_stem_png",
  "parameters": {},
  "frames": [
    {
      "ordinal": 0,
      "relative_path": "character/0001.png",
      "size_bytes": 123456,
      "sha256": "<64 hex>"
    }
  ]
}
```

客户端保证：ordinal 连续、相对路径规范化后不冲突、输入文件 size/SHA 与 ZIP 一致；同一幂等键下输入变化会在本地拒绝。

### 3.3 查询、取消和下载

```http
GET  /api/v1/batches/{batch_id}
GET  /api/v1/batches/{batch_id}/manifest?offset=0&limit=500
POST /api/v1/batches/{batch_id}/cancel
GET  <artifact.download_url>
```

下载中断后动画管家发送：

```http
Range: bytes=<existing_part_size>-
```

GPU Control 必须返回合法 `206` 与 `Content-Range: bytes <offset>-<end>/<total>`；若不支持续传，应返回完整 `200`，客户端会清空旧 partial 后从 0 重下。最终文件仍必须与 artifact size/SHA 完全一致。

## 4. GPU Control 必须返回给动画管家的内容

### 4.1 create 与每次父 GET 的最低公共字段

```json
{
  "batch_id": "batch-uuid",
  "external_batch_id": "assetclaw:COMFY_xxxxxxxxxxxx:matting:g1",
  "status": "QUEUED",
  "progress": 0.0,
  "counts": {
    "total": 97,
    "pending": 97,
    "queued": 0,
    "running": 0,
    "succeeded": 0,
    "failed": 0,
    "cancelled": 0
  },
  "workflow_key": "imageclip-rgba",
  "workflow_version": "2026.07.30-691770c-r1",
  "pipeline_commit": "691770cd6a59fd7c51391456fe900dc57a313233",
  "pipeline_sha256": "00e7104762f0a1fdf3a4c20e043bec2b9f088132452d5a5ce4302ba268edac0b",
  "output_node": "SaveImage #25",
  "created_at": "RFC3339 UTC",
  "validated_at": "RFC3339 UTC or null",
  "queued_at": "RFC3339 UTC or null",
  "started_at": "RFC3339 UTC or null",
  "execution_finished_at": "RFC3339 UTC or null",
  "assembling_at": "RFC3339 UTC or null",
  "artifact_ready_at": "RFC3339 UTC or null",
  "finished_at": "RFC3339 UTC or null",
  "updated_at": "RFC3339 UTC"
}
```

规则：

- 时间只能首次写入，不得随轮询刷新；
- 状态和时间必须单调；重启/租约改派不能倒退父状态；
- `counts.total` 固定，所有状态计数总和始终等于 total；
- `SUCCEEDED` 时 succeeded=total，其他计数为 0；
- `FAILED` 必须有稳定 error code 和失败帧/attempt 证据；
- `CANCELLED` 必须能关联合法父取消 audit，不能由 child 通用 cancel 直接制造。

### 4.2 成功 artifact

父状态只有在全部帧成功、结果装配并校验完成后才可进入 `SUCCEEDED`：

```json
{
  "status": "SUCCEEDED",
  "artifact": {
    "download_url": "https://...",
    "size_bytes": 987654321,
    "sha256": "<64 hex>",
    "content_type": "application/zip"
  }
}
```

artifact HTTP 响应同时返回：

```http
X-Request-ID: <server request id>
X-Artifact-SHA256: <same sha256>
Content-Length: <full or ranged body length>
Accept-Ranges: bytes
```

父 `SUCCEEDED` 之前，父 artifact 和任何 child artifact 均不可下载；建议返回 `409 ARTIFACT_NOT_READY` 或 `404`，不可返回半成品。

### 4.3 取消回执与审计

父取消接口至少返回：

```json
{
  "batch_id": "batch-uuid",
  "status": "CANCEL_REQUESTED",
  "cancel_operation_id": "cancel-op-uuid",
  "cancel_requested_at": "RFC3339 UTC",
  "cancel_requested_by": "authenticated principal",
  "cancel_request_id": "assetclaw-...-cancel-01",
  "cancel_idempotency_key": "assetclaw:...:cancel"
}
```

最终 GET 的 `CANCELLED` 还需返回 accepted/finished 时间、被取消/已完成/未开始帧数和审计引用。重复 cancel 必须返回同一 operation 或等价终态，不得创建多个取消事务。

### 4.4 性能与节点数据

`performance` 必须由服务端权威生成，不能由动画管家猜测：

```json
{
  "performance": {
    "input_pixels_total": 201093120,
    "gpu_service_ms_total": 125000,
    "queue_ms": 3200,
    "execution_ms": 128500,
    "assembly_ms": 4800,
    "artifact_publish_ms": 900,
    "frames_per_gpu_minute": 46.56,
    "megapixels_per_gpu_second": 1.61,
    "scheduler_restarts": 0,
    "reassignments": 1,
    "straggler_ratio": 1.08,
    "nodes": [
      {
        "node_id": "gpu-node-01",
        "gpu_model": "...",
        "worker_version": "...",
        "workflow_version": "2026.07.30-691770c-r1",
        "frames_succeeded": 33,
        "frames_failed": 0,
        "attempts_total": 33,
        "gpu_service_ms": 41800,
        "frame_ms_p50": 1240,
        "frame_ms_p95": 1410,
        "max_concurrent_prompts": 1,
        "input_pixels": 68428800
      }
    ]
  }
}
```

对账条件：节点 succeeded/failed、attempt 和像素量之和必须能与父批次对上；纯 GPU service 不包含上传、父排队、装配和下载。异构分辨率比较同时使用 frames/min 与 MPixel/s。

### 4.5 capacity/admission 建议

```http
GET /api/v1/scheduler/capacity
```

建议返回：

```json
{
  "accepting_batches": true,
  "suggested_max_new_batches": 2,
  "queue_depth": 1,
  "compatible_nodes": 3,
  "estimated_queue_ms_p50": 2500,
  "estimated_queue_ms_p90": 9000,
  "capacity_generated_at": "RFC3339 UTC"
}
```

这是 advisory，不是 create 的权威替代。动画管家可能在本机忙时仍将任务提交到服务端持久队列；GPU Control 必须由 create/admission 返回最终接受或稳定错误码。

## 5. 本次动画管家实现落点

| 文件 | 已实现行为 | GPU Control 依赖 |
|---|---|---|
| `services/gpu_control_batch.py` | 身份漂移闭锁、状态字段保留、Range 续传、最终哈希与原子发布 | 完整身份、阶段时间、Range、artifact hash |
| `skills/comfyui_skills.py` | trace、阶段计时、取消意图先落盘、watchdog 不取消、非法 CANCELLED 闭锁 | 父取消 operation/audit、稳定父状态 |
| `services/hybrid_matting_router.py` | 本机忙时允许小任务进入集群队列并行 | capacity 准确、create 权威、队列可恢复 |
| `skills/animation_flow_skills.py` | 原子 JSON、阶段 start/end/duration/attempt、last transition | 远端 trace/request ID 可关联 |
| `scripts/pre_rollout_health.py` | 发布前只读任务/Gateway/GPU 健康检查 | health 与 capacity 可访问 |

本地验证已覆盖：身份匹配/漂移、断点续传、watchdog 不取消、取消意图持久化、非法 CANCELLED、忙本机远端排队、动画阶段计时和原子保存。动画管家 Gateway 已完成最小重载，状态为 `DEPLOYED_NOT_ACCEPTED`；尚未完成真实媒体灰度、故障注入或联合性能验收。

## 6. GPU Control 必须先修的 P0

| ID | 必修项 | 验收证据 |
|---|---|---|
| G-P0-01 | child cancel 不得绕过父批次合同 | 自动测试 + audit event；child API 对 batch-owned job 返回拒绝 |
| G-P0-02 | 父成功前 child/parent artifact 均不可访问 | 故障注入在 QUEUED/RUNNING/ASSEMBLING 均返回 not ready |
| G-P0-03 | 公共父取消持久、认证、幂等 | operation ID、principal、request/key、重复请求测试 |
| G-P0-04 | 发布标签、包版本、Web 版本一致 | source commit、OCI image digest、registry manifest digest、SBOM/版本输出 |
| G-P0-05 | 父批次不可变身份快照 | create/GET/artifact manifest 三处相同字段的自动测试 |
| G-P0-06 | 兼容节点不被不兼容节点阻塞 | 混合节点调度故障测试 |
| G-P0-07 | prompt submit→prompt ID 持久化窗口不重复执行 | 崩溃点注入；恢复后同 ordinal 至多一个有效结果 |

## 7. 联合性能验收

固定同一输入、同一 ImageClip 身份、同一输出质量设置，执行：

- B1、B6、B30、B64、B97、B300；
- 本机 4070 Ti 热跑；
- 集群 1 节点、2 节点、3 节点热跑；
- 并发 `3 × B97`；
- 每组至少 5 次，第一次冷跑单列，不混入热跑分位数。

双方同时保存：create/poll/cancel/download 的原始 JSON、request ID、父/子事件、节点 performance、客户端阶段时间、输入像素、artifact SHA 和质量抽检结果。

建议门槛：

- 成功率 100%，无重复发布、无孤儿批次、无非法 CANCELLED；
- 3 节点对 1 节点的纯 GPU service 加速比目标 ≥2.4×；
- 3 节点对本机 4070 Ti 的端到端加速需按 B30/B97/B300 分桶报告，不接受只报总体平均；
- 节点拖尾 `straggler_ratio P95 ≤ 1.15`；
- 路由预测 MAPE ≤25%；
- output count、ordinal、路径、size、SHA 全部通过；
- 中断下载恢复后 artifact SHA 与一次下载完全一致。

## 8. GPU Control 下一份回执必须返回

请按下面清单逐项回复，不接受只回复“已对齐”：

1. source repository + commit；
2. OCI image digest 与 registry manifest digest；
3. API/worker/Web 的统一版本号；
4. G-P0-01 至 G-P0-07 的状态、代码位置、测试 ID 和原始测试结果；
5. create、父 GET、cancel、artifact、capacity 的脱敏示例 JSON/headers；
6. `performance.nodes[]` 的真实样例和父子对账结果；
7. Range 下载与父成功前 artifact 阻断证据；
8. B1/B6/B30/B64/B97/B300 及 `3×B97` 原始 benchmark report；
9. 新版本 rollback image digest、回滚命令和预计恢复时间；
10. 可供动画管家联合验证的隔离 tenant/API key 和安全时间窗。

## 9. 发布顺序

1. GPU Control 在非生产环境完成 G-P0-01～07 和自测；
2. GPU Control 提交上述回执和不可变镜像 digest；
3. 双方完成故障注入与固定素材 A/B；
4. 动画管家先执行只读 pre-rollout health；
5. 无活动任务时部署客户端，按 10% → 50% → 100% 灰度；
6. 连续 7 天观察成功率、P90、重试、非法状态、artifact 与 delivery receipt；
7. 达标后才将双方状态改为 `PRODUCTION ACCEPTED`。

动画管家回滚基线：`storage/rollback_snapshots/20260730-115717_stable-pre-v4_1_1f84ceba1276`。GPU Control 必须在回执里提供其对应的不可变回滚镜像，不能只给可变 tag。

## 10. 动画管家本地部署回执

2026-07-30 本地部署证据：

- 完整测试：`455 passed, 8 warnings`；
- 候选 Gateway 隔离端口烟测：health 成功，191 个技能可加载，旧 P4 能力正确标记为未实现；
- 部署前本地活动任务：0；GPU Control 活动批次：0；
- GPU Control readiness：database/redis 均 `ok`；
- capacity：3/3 eligible nodes、3 available slots、queue 0、running 0；
- Gateway 最小重载：旧 PID `64136`，新监听 PID `71348`；
- 重载后 Gateway `/health`：HTTP 200、`ok=true`；
- 未重启 WebUI、Unity MCP 或飞书 WS；未创建、取消或修改任何 GPU 批次；
- 7 条测试遗留的僵死动画流程记录在逐文件备份后由内置恢复函数闭合为 FAILED，当前活动动画流程为 0；备份位于 `storage/recovery_backups/20260730-orphan-flow-pre-recovery`。

以上只证明动画管家客户端已部署且健康，不代表 GPU Control P0 或联合基准已通过。
