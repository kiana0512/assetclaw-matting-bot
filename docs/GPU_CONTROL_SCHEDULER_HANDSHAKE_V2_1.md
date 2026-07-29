# 动画管家 ↔ GPU Control 调度握手与持久化 V2.1

更新时间：2026-07-24  
状态：动画管家已实现；GPU Control 可按本文件同步可选容量接口  
基础合同：`GPU_CONTROL_MATTING_HANDOFF_V2.md`，SHA-256 `5b80a284b770a8f45eda827890b8b5ef6c2acca794afbceaddc55cf4030bdd1d`

## 1. 边界不变

GPU Control 只负责 ImageClip RGBA 抠图。动画管家继续负责飞书接收、视频抽帧、Cherry 后处理、视频/ZIP 打包、Unity/表格流程和最终回复。

一个动画管家业务父任务对应一个不可变抠图批次代次。不同父任务不得合并目录、manifest、结果或重试；服务端可以把同一父批次的帧分配给不同 GPU 节点，但返回时必须保持原 ordinal、路径和 SHA 对应关系。

## 2. 调度前握手

每次准备把新批次路由到集群时，动画管家在跨进程路由锁内执行：

1. `GET /health/ready`，要求 HTTP 200 且 `status=ready`。
2. `GET /api/v1/scheduler/capacity`。这是 V2.1 可选扩展；当前服务返回 404 时，动画管家使用 readiness + 本地持久化的在途远端批次数作为兼容兜底。
3. 单批不超过 5000 帧且达到大帧阈值时选择远端；显式 `gpu_control` 同样直接选择远端。容量快照不得把已选远端任务留在客户端等待。
4. 容量响应只用于遥测和路由建议，不预占槽位，也不承担 admission。最终是否接单、持久化排队和幂等恢复均以原 V2 `POST /api/v1/batches/imageclip-rgba` 为准。

请求头沿用 V2：

```http
X-Request-ID: assetclaw-route-capacity
X-API-Key: <可选；来源 IP 认证时不发送>
```

## 3. GPU Control 需要同步的可选接口

```http
GET /api/v1/scheduler/capacity
```

成功响应固定为：

```json
{
  "schema_version": "1.0",
  "status": "READY",
  "accepting_batches": true,
  "queue_depth": 2,
  "active_batches": 4,
  "online_nodes": 3,
  "idle_nodes": 1,
  "busy_nodes": 2,
  "suggested_max_new_batches": 2,
  "max_batch_frames": 5000,
  "observed_at": "2026-07-24T14:45:00Z"
}
```

字段语义：

- `accepting_batches`：此刻是否建议客户端创建新批次。
- `suggested_max_new_batches`：短时间内的负载建议；`0` 表示当前负载较高，但不能阻止大帧任务调用 `create_batch` 进入服务端队列。
- `idle_nodes` / `busy_nodes` / `online_nodes`：节点快照，只展示和辅助路由，不允许客户端指定节点。
- `queue_depth` / `active_batches`：服务端全局持久化队列快照。
- `max_batch_frames`：必须不小于冻结 V2 对当前服务声明的上限；客户端仍取双方限制的较小值。
- `observed_at`：服务端 UTC 时间，便于排查时钟与陈旧响应。

非 200 响应必须带稳定错误对象和 `X-Request-ID`。维护/排空时返回 HTTP 200、`accepting_batches=false`，不要用 5xx 表示正常 drain。

## 4. 动画管家路由策略

生产模式为 `hybrid`：

- 假模式永远本机。
- 单批超过远端上限时保持整个父任务在本机，禁止拆到不同批次。
- capacity 未部署、快照饱和或探针失败时只记录遥测；达到大帧阈值的任务仍直接调用 `create_batch`，由服务端决定接单与排队。
- 4070 Ti 空闲且帧数低于阈值时走本机。
- 帧数达到阈值，或本机已有活动抠图任务时走集群。
- 可选容量接口未部署时，客户端最多保留 `GPU_CONTROL_MAX_INFLIGHT_BATCHES` 个远端在途批次；当前默认 8。

服务端接单后可以在 4090、3090-A、3090-B 间动态分帧。动画管家只认父 batch ID 和逐帧 manifest，不依赖具体节点。

## 5. 双方持久化与幂等

动画管家在创建网络请求前持久化：

- 业务父任务 ID、阶段、输入目录与输出目录；
- `external_batch_id=assetclaw:<parent_id>:matting:g<generation>`；
- manifest SHA、输入 ZIP 路径、幂等键；
- 路由原因和完整 `backend_handshake` 快照。

服务端接单后，动画管家立即持久化：

- GPU Control `batch_id`、状态、counts、progress、节点分布；
- create/poll/download 的 request ID；
- artifact 元数据、响应头 SHA、下载 SHA、结果发布时间。

GPU Control 必须持久化 batch、frame ordinal、路径、输入/输出 SHA、节点、attempts 和终态。相同 `Idempotency-Key + manifest/ZIP` 重试必须返回原 batch；相同 key 但输入字节改变必须 409，不能创建第二批。

## 6. 故障恢复

- 单个 GPU 节点异常：GPU Control 在同一 batch 内重试/改派，ordinal 与路径不变。
- 状态轮询瞬断：动画管家指数退避，默认允许连续 20 次错误，最长单次等待 60 秒；期间 WebUI 保留最后可信状态，不把任务直接报失败。
- 动画管家 worker/Gateway 重启：从数据库中的 `batch_id` 和幂等键重新挂接同一远端 batch，不重传、不创建新父批、不删除服务端任务。
- 下载中断：重新下载到 `.part`，三层 SHA 全部通过后才原子发布。
- 远端终态 `FAILED/CANCELLED`：本代次停止发布；若重试必须由父任务创建新的 generation，旧批次继续保留审计。
- Cherry/发送失败：复用已通过红线校验的 matte 结果，只重跑本机后处理或重发，不重新抠图。

## 7. WebUI 同步

WebUI 从父任务持久化状态展示：

- `GPU Control 抠图`、真实 `completed/total` 和 progress；
- remote status、batch ID、节点分布；
- 当前 generation 的 Cherry 进度。

历史失败的 Cherry 子任务保留在详情审计，但不参与当前 generation 的进度分母；因此 54 帧恢复任务显示 `54/54`，不会显示 `54/108`。

## 8. 上线顺序

1. GPU Control 可先上线容量接口；未上线时动画管家兼容 404。
2. 双方验证 readiness、容量字段和 request ID。
3. 用两个不同父任务验证不同 batch ID；再用同一幂等键重复创建验证仍返回同一 batch。
4. 模拟一台节点离线，确认服务端同批改派且结果 ordinal/SHA 不变。
5. 模拟动画管家进程重启，确认重新挂接原 batch。
6. 通过后保持 `MATTING_BACKEND_MODE=hybrid`。

任何一步都不得通过 `verify=False`、跳过 SHA、合并父任务或复制结果凑数来通过验收。
