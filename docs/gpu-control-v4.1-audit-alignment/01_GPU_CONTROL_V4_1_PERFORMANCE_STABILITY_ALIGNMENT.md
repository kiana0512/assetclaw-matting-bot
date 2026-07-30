# 动画管家 ↔ GPU Control V4.1 性能与稳定性对齐合同

文档状态：`DRAFT / JOINT ALIGNMENT REQUIRED / RUNTIME UNCHANGED`

动画管家文档版本：`4.1.0-am-audit1`

基础合同：`GPU_CONTROL_MATTING_HANDOFF_V4_ASSETCLAW_ALIGNMENT.md`

基础合同 SHA-256：`93F638B40B4B009F9D637E3C4E8000F8FAACA20BF36966E465CC696BA768B52A`

审计证据：`animation-log-audit/generated/summary.json`

审计证据 SHA-256：`31DC3935E4E4F19A21EAA5CA544157E7F9E2D464D194CC04041C4D83071EA70A`

生产入口：`https://10.3.34.11`

批准工作流基线：`imageclip-rgba / 2026.07.27-721f7d6-r1`

## 1. 文档定位

V4.1 是 V4 的审计增补，不是破坏性 API 升级：

- manifest 继续使用 `schema_version=1.0`；
- external batch ID、幂等键、父状态、all-or-nothing 与结果包语义不变；
- V4.1 新增的是时间语义、性能遥测、错误分类、恢复证据、发布门禁和联合速度验收；
- 未在本合同明确修改的项目继续以 V4 为准；
- V4.1 与 V4 冲突时，安全性、取消审计、时间语义和性能验收以 V4.1 为准。

任何一方不得单方面把本文标成 `FROZEN`。GPU Control 必须按回执模板逐项返回实现状态、版本、证据和未决项。

## 2. 审计基线与问题陈述

### 2.1 当前速度

| 指标 | 本机 4070 Ti | GPU Control 纯执行 | 结论 |
|---|---:|---:|---|
| 成功视频样本 | 8 | 12 | 历史日志样本，不是正式 A/B |
| 中位吞吐 | 0.556 帧/分 | 1.532 帧/分 | 中位加速 2.75× |
| 均值吞吐 | 0.704 帧/分 | 2.063 帧/分 | 均值加速 2.93× |
| 最近两个 97 帧三节点任务 | - | 2.425 / 2.638 帧/分 | 相对本机中位 4.36× / 4.74× |

集群总体速度基本正常，但不同任务波动很大。现有样本未统一素材、分辨率、模型缓存、节点负载与同时并发，不能作为“持续三倍”的冻结证据。

### 2.2 当前稳定性与可观测性

- 150 个直发任务：125 DONE、19 FAILED、6 CANCELED，成功率 83.3%；
- 26 个动画流程：17 FAILED、9 CANCELED、0 DONE；
- 12 个可拆分集群视频的真实远端排队 P50 0.61 秒、P90 1.05 秒，排队不是当前主瓶颈；
- `VID_9D9EB9ACE6A1` 的 97 帧纯 GPU 执行 82分17秒、1.179 帧/分，是明确执行期慢点；
- 两个单图和一个 171 帧视频出现 38–42 小时非运行间隔，当前 UI 错归因为 Cherry 计算；
- 125 个 DONE 只有 20 个持久投递回执；
- 10 个成功任务的阶段时间线交叉，不能进入阶段性能基线。

## 3. 双方共同目标

### 3.1 正确性目标

1. 网络、超时、节点离线、worker 重启、单帧失败绝不伪造成用户取消；
2. 同一 generation 在任何恢复路径中只有一个远端父 batch；
3. 只有父批次 `SUCCEEDED`、身份字段匹配且结果十项验证通过才进入 Cherry；
4. DONE 必须意味着用户侧投递 API 已确认；仅产物完成使用 `OUTPUT_READY`；
5. 每个阶段拥有可审计的开始、结束、attempt、错误码和责任端；
6. 所有性能数字都可从原始事件重算，不能使用全局快照替代任务实际等待。

### 3.2 性能目标

在第 12 节固定 A/B 素材与环境下：

- 三节点 97 帧批次：纯 GPU P50 ≤40 分钟，90% 批次 ≤50 分钟；
- 三节点相对本机逐批加速：中位 ≥2.7×，至少 90% 批次 ≥2.2×；
- 服务端真实排队 P90 ≤5 秒；
- artifact ready 到动画管家下载完成 P95 ≤30 秒（局域网正常、标准 97 帧包）；
- 节点拖尾占批次 GPU wall time P95 ≤15%；
- 不允许出现无法解释的 >5 分钟无进度间隔；
- GPU 批次成功率 ≥99%，损坏输入、用户取消和故障注入样本单独统计；
- 结果完整性与 alpha 质量不能因提速下降。

这些是联合验收门槛，不是从现有历史样本直接推导出的生产承诺。任何指标不达标都必须保留证据并进入行动矩阵，禁止挑选最快样本宣布通过。

## 4. 边界与责任冻结

### 4.1 动画管家负责

- 飞书事件、附件下载、业务路由、抽帧及不可变输入冻结；
- external batch ID、manifest、ZIP、幂等创建与原 batch 恢复；
- 本机/集群路由决策，但不指定具体 GPU 节点；
- 远端状态持久化、结果下载、身份门禁、十项验证与原子发布；
- Cherry、业务打包、飞书上传、投递回执；
- 本地 queue、worker、attempt、idle gap 和 delivery 事件；
- 完整 cancel intent 与本地状态映射。

### 4.2 GPU Control 负责

- create 权威接单、持久化排队、父/子状态机和幂等恢复；
- 上传回读 size/SHA 后才提交 prompt；
- 节点健康、租约、重试、改派、动态分片与拖尾控制；
- 逐帧 ordinal/path/SHA 不变；
- workflow/model/pipeline 身份 fail closed；
- 父状态、节点性能、attempt、错误码和 artifact 时间的权威遥测；
- 节点离线或单帧失败不设置 cancel intent；
- 只有已认证取消请求或管理员审计操作才能取消父批次。

### 4.3 共同禁止

- capacity 饱和时让已选远端任务停在客户端等待；
- 状态不明时新建 generation、静默回本机或重传为第二个父批次；
- 用 handshake `queue_depth` 作为该任务实际排队时间；
- 用最早到最晚 attempt 包络冒充实际计算时间；
- 用帧数简单平均冒充加权吞吐；
- 在缺投递回执时标 DONE；
- 用减少质量、跳过 SHA/Alpha 校验或降低输出完整性换取速度。

## 5. V4.1 父状态时间合同

现有 `created_at/started_at/finished_at/updated_at` 继续保留，并冻结为：

| 字段 | 权威含义 | 责任端 |
|---|---|---|
| `created_at` | create 被持久化、batch ID 已分配 | GPU Control |
| `validated_at` | manifest/ZIP/输入集合完成服务端验证 | GPU Control |
| `queued_at` | 父批次具备调度资格，进入持久队列 | GPU Control |
| `started_at` | 第一个 child 开始 GPU/Comfy 工作流执行；不含上传完整性校验 | GPU Control |
| `last_progress_at` | succeeded/failed/running 或完成帧集合最后变化 | GPU Control |
| `execution_finished_at` | 全部 child 到达不可逆终态 | GPU Control |
| `assembling_started_at` | 开始结果汇总 | GPU Control |
| `artifact_ready_at` | result archive 持久化且元数据可下载 | GPU Control |
| `finished_at` | 父批次进入 `SUCCEEDED/FAILED/CANCELLED` | GPU Control |
| `updated_at` | 父记录最后一次持久更新 | GPU Control |

响应示例（全部为 UTC ISO-8601）：

```json
{
  "batch_id": "uuid",
  "external_batch_id": "assetclaw:VID_x:matting:g1",
  "status": "SUCCEEDED",
  "created_at": "2026-07-30T03:00:00.100Z",
  "validated_at": "2026-07-30T03:00:00.420Z",
  "queued_at": "2026-07-30T03:00:00.430Z",
  "started_at": "2026-07-30T03:00:01.020Z",
  "last_progress_at": "2026-07-30T03:36:40.000Z",
  "execution_finished_at": "2026-07-30T03:36:41.000Z",
  "assembling_started_at": "2026-07-30T03:36:41.020Z",
  "artifact_ready_at": "2026-07-30T03:36:52.000Z",
  "finished_at": "2026-07-30T03:36:52.010Z",
  "updated_at": "2026-07-30T03:36:52.010Z"
}
```

要求：

- 所有时间只写一次或单调前进，不允许轮询时倒退；
- create 稀疏响应不能清空后续 GET 已观察到的时间；
- Scheduler 重启后时间保持原值；
- 真实排队固定为 `started_at - queued_at`；
- 纯 GPU 父 wall 固定为 `execution_finished_at - started_at`；
- 汇总时间固定为 `artifact_ready_at - assembling_started_at`；
- 任一阶段无适用值时返回 `null`，不伪造为父 `updated_at`。

## 6. 节点级性能合同

GPU Control 父状态新增 `performance.nodes[]`。这是 additive response，不进入 manifest 1.0：

```json
{
  "performance": {
    "schema_version": "1.0",
    "frames_total": 97,
    "input_pixels_total": 19070976,
    "reassignments": 0,
    "scheduler_restarts_observed": 0,
    "nodes": [
      {
        "node_id": "control-4090",
        "gpu_model": "RTX 4090",
        "worker_version": "<git/image digest>",
        "frames_assigned": 42,
        "frames_succeeded": 42,
        "frames_failed": 0,
        "frame_attempts": 42,
        "upload_integrity_attempts": 42,
        "first_execution_at": "UTC",
        "last_execution_at": "UTC",
        "gpu_service_ms": 900000,
        "frame_latency_p50_ms": 21000,
        "frame_latency_p95_ms": 26000,
        "workflow_load_ms": 0,
        "max_concurrent_prompts": 1
      }
    ]
  }
}
```

冻结规则：

- `frames_assigned` 是当前/累计领取量，`frames_succeeded` 是最终成功量；
- `frame_attempts` 包括可恢复执行重试，但上传完整性尝试单独计数；
- `gpu_service_ms` 是实际工作流执行累计，不含队列、上传和结果汇总；
- 节点统计总和必须能与父 counts 对账；
- 节点重启或改派不覆盖旧节点记录；
- GPU model、worker version、workflow identity 缺失时该样本不能进入速度基线；
- 逐帧明细继续通过分页 child job 接口提供，父状态只返回聚合。

## 7. 错误码与 attempt 分层

双方不得再依赖自由文本判断故障类型。GPU Control 至少返回：

| error domain | code 示例 | 是否可恢复 | 是否允许取消父批次 |
|---|---|---|---|
| `INPUT_VALIDATION` | `MANIFEST_MISMATCH` | 否 | 否 |
| `UPLOAD_INTEGRITY` | `REMOTE_SHA_MISMATCH` | 是，预算内 | 否 |
| `WORKFLOW_IDENTITY` | `PIPELINE_SHA_MISMATCH` | 否，节点 fail closed | 否 |
| `PROMPT_EXECUTION` | `PROMPT_TIMEOUT` | 是，按 job attempt | 否 |
| `NODE_RUNTIME` | `NODE_HEARTBEAT_LOST` | 是，租约后改派 | 否 |
| `SCHEDULER` | `LEASE_RECONCILIATION_FAILED` | 视恢复结果 | 否 |
| `ARTIFACT` | `RESULT_ARCHIVE_BUILD_FAILED` | 是/否 | 否 |
| `CANCEL` | `AUTHENTICATED_CANCEL` | 不适用 | 只有该类允许 |

每个 child job 必须分开记录：`upload_integrity_attempt`、`job_attempt`、`prompt_attempt`。动画管家的 HTTP create/poll/download retry 继续作为第四层独立计数。

## 8. 取消、超时与恢复

### 8.1 动画管家必改

1. 删除执行 watchdog 到期自动调用远端 cancel；改为写 `watchdog_alert_at`、通知、持续查询原 batch；
2. cancel API 调用前原子保存 `source/operator/reason/conversation/request_id/idempotency_key/requested_at`；
3. 收到 `CANCELLING/CANCELLED` 时先核验本地 cancel intent；无意图则标 `PROTOCOL_ANOMALY`，不得显示用户取消；
4. worker 重启从已保存 batch ID 恢复，不重新上传、不新建 generation；
5. 状态不明期间保留最后可信 progress 和 `last_progress_at`。

### 8.2 GPU Control 必须确认

- timeout、node lost、prompt failure、scheduler restart 永不设置 `cancel_requested=true`；
- cancel audit 保存认证主体、request ID、幂等键、时间和来源；
- 同一 cancel key 重放返回同一取消操作；
- 没有合法 cancel audit 的 `CANCELLED` 属于服务端协议错误；
- 节点离线时在原 batch/job 身份下租约对账和改派。

## 9. 工作流身份与发布硬门禁

批准基线：

```text
workflow_key:     imageclip-rgba
workflow_version: 2026.07.27-721f7d6-r1
imageclip_commit: 721f7d68635ee36d45f545ce2c82037046147442
pipeline_sha256:  00e7104762f0a1fdf3a4c20e043bec2b9f088132452d5a5ce4302ba268edac0b
only output node: SaveImage #25
```

GPU Control 在 create、每次父 GET、child 领取和 artifact manifest 中返回同一身份。动画管家在 generation 创建时冻结批准身份，并在以下时间校验：

1. create 响应；
2. 每次状态 GET；
3. artifact 元数据；
4. 下载包内 manifest；
5. 进入 Cherry 前。

任一字段缺失、变化或不匹配：停止发布，任务进入 `BLOCKED_IDENTITY_MISMATCH`，保留结果与证据，不自动重跑、不自动取消、不进入 Cherry。

## 10. 路由与“速度拉满”策略

capacity 继续只是 advisory；已选择远端后立即幂等 create，由服务端排队。V4.1 建议把固定帧数阈值升级为“预测完成时间”路由：

```text
predicted_local_finish
  = local_queue_gpu_seconds + input_pixels / local_ewma_pixels_per_second

predicted_cluster_finish
  = client_prepare_upload
  + server_estimated_queue
  + input_pixels / compatible_online_nodes_ewma_pixels_per_second
  + artifact_return
```

规则：

- 路由模型只使用同 workflow version、分辨率桶和近 7 天高可信成功样本；
- 数据不足时使用保守静态策略：本机空闲的小任务走本机，视频/大 ZIP 或本机繁忙走集群；
- 一旦 create 被服务端接受，不因 capacity 后续变化改走本机；
- 预测只决定 backend，不允许客户端指定 4090/3090 节点；
- 每次路由保存两个预测值、选择原因和最终实际值，用于误差回归；
- 路由优化不能绕过身份、完整性和投递门禁。

GPU Control 应在 capacity 或 create 响应提供 advisory `estimated_queue_ms`、`compatible_online_nodes`、`observed_at`；真正排队仍由实际时间计算，预测值不得出现在事后性能真值中。

## 11. 动画管家本地全链路事件

GPU Control 只负责抠图内部事件。动画管家必须补齐：

```text
feishu.event_received
attachment.download_started / finished
task.created
extract.started / finished
matte.queue_entered / batch_created / result_published
cherry.attempt_started / finished
package.started / finished
delivery.upload_started / finished
delivery.send_ack
worker.heartbeat / lost / resumed
```

一个业务请求使用一个 `trace_id`，并将其放入 GPU Control `X-Request-ID` 派生信息或 W3C `traceparent`。双方日志至少可以用 `trace_id + external_batch_id + batch_id + request_id` 对账。

Cherry 必须同时计算 attempt 包络、实际 attempt sum 和 idle gap。DONE 只有在 `delivery.send_ack` 存在时成立；否则为 `OUTPUT_READY` 或 `DELIVERY_FAILED`。

## 12. 联合测试与冻结

测试细节以 `03_JOINT_ACCEPTANCE_AND_BENCHMARK.md` 为准。最低矩阵包含：

- 1、30、64、97、300 帧正常批次；
- 97 帧同素材本机/1节点/2节点/3节点热启动重复基准；
- 三个并发 97 帧公平调度；
- 0 字节上传、SHA 不一致、prompt timeout、节点离线、Scheduler 重启；
- create 响应丢失、poll 断线、动画管家 worker 重启；
- 有/无合法 intent 的取消；
- workflow 漂移、artifact 篡改、结果缺帧；
- Cherry worker 重启与投递 API 重试。

所有测试必须保存原始 JSON、request IDs、trace、节点数据、产物 SHA、质量校验和最终投递回执。只给截图或汇总数字不算完成。

## 13. 发布与回滚

1. 等活动批次进入终态；
2. GPU Control 发布 additive telemetry 和错误码，先保持客户端忽略新字段也可运行；
3. GPU Control 回归 V4 正确性与 V4.1 时间单调性；
4. 动画管家增加新字段持久化、取消安全修复和身份硬门禁；
5. 用隔离 tenant/generation 联合验收；
6. 先 10% 远端批次观察，再 50%，最后恢复 hybrid 全量；
7. 每个阶段至少观察 24 小时且无 P0/P1；
8. 回滚只回应用版本，不删除 batch、event、cancel audit 或 artifact；
9. 任一身份、幂等或投递问题立即停止扩大流量。

## 14. 冻结条件

以下全部满足才能签署：

- [ ] 动画管家移除 watchdog 自动取消；
- [ ] 完整 cancel intent 与取消对账上线；
- [ ] workflow/commit/pipeline SHA 发布硬门禁上线；
- [ ] GPU Control 父状态时间字段与节点 performance 合同上线；
- [ ] GPU Control 错误码和 attempt 分层上线；
- [ ] 动画管家本地 stage/trace/idle gap/delivery ack 上线；
- [ ] 联合故障注入全部通过；
- [ ] 同素材速度和质量门槛全部通过；
- [ ] 生产专用 API Key 与 TLS 证据完成；
- [ ] 7 天观察窗无 P0/P1；
- [ ] 双方回执包含 commit、image digests、测试 IDs 和未决项；
- [ ] 主合同状态由双方共同改为 `FROZEN / PRODUCTION ACCEPTED`。

在此之前，只能写“候选实现”或“等待联合验收”，不能写“问题已全部解决”或“速度已拉满”。
