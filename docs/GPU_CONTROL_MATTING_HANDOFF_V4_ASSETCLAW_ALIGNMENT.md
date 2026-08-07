# 动画管家 ↔ 统一调度中心批量抠图 V4 对齐回执与联合验收合同

文档状态：`ASSETCLAW REVIEWED / RUNTIME UNCHANGED / JOINT ACCEPTANCE PENDING`

动画管家侧文档版本：`4.0.0-am1`

生成时间：`2026-07-28 17:20 +08:00`

上游交接文档：`56_GPU_CONTROL_MATTING_HANDOFF_V4.md`

上游文档 SHA-256：`d49dda5bd0fd2c0bc0d9f1e2da067991a4b58cb8abda26aac9f2b91ff47c10e`

上游文档状态：`IMPLEMENTATION CANDIDATE / 等待双方真实联调冻结`

manifest 协议：`1.0`，V4 未改变请求 manifest 字段

生产入口：`https://10.3.34.11`

当前批准工作流：`imageclip-rgba / 2026.07.30-691770c-r1`

本文是动画管家完整阅读统一调度中心 V4 交接文档后给出的差异审计、双方边界、待实施项和联合验收回执。本文不代表双方已经冻结生产协议，也不代表动画管家已在活动任务期间修改或重载后端。统一调度中心读取后，应按第 17 节逐项回复；真实联调与证据未完成前，双方状态必须保持 `JOINT ACCEPTANCE PENDING`。

---

## 1. 结论先行

动画管家接受 V4 的核心目标和以下生产语义：

1. 一帧传输、执行或节点失败不等于用户取消；
2. `all_or_nothing` 批次中，单帧最终失败后其他帧继续收敛，父任务最终为 `FAILED`，不发布部分结果；
3. 只有明确、已认证、可审计的取消请求才能使父任务进入 `CANCELLING → CANCELLED`；
4. 远端状态不明确时复用原 ZIP、manifest、幂等键和 `batch_id`，不能创建第二个业务批次；
5. 只有父任务 `SUCCEEDED` 且结果包十项校验全部通过，动画管家才原子发布 matte 并进入 Cherry；
6. ComfyUI 上传后回读 size/SHA 再提交 prompt 属于统一调度中心的强制完整性门禁；
7. 顶层一个业务父批次只显示一行，逐帧 job 只在父详情分页中出现；
8. 工作流版本、Git commit、pipeline SHA 和节点 class inventory 必须 fail closed；
9. 3090-B 离线、恢复或重新加入只改变可用算力，不改变 V4 API 合同；
10. capacity 只作遥测和路由建议，`POST create_batch` 仍是权威接单与服务端排队操作。

但当前动画管家不能无条件声明“V4 已完全落地”。只读代码审计发现三个必须在安全窗口修正的客户端差异：

- 远端执行超过本地 watchdog 时，当前代码会主动调用 cancel；V4 要求超时只能告警并继续查询原 batch，不能伪造成用户取消；
- 当前取消记录只有时间和响应元数据，尚缺操作者、来源、原因、业务 request ID 和最终对账状态；
- 当前收到远端 `CANCELLED` 会直接映射为本地 `CANCELED`，尚未先核验本地是否存在合法取消意图。

另有一个建议升级为发布硬门禁的审计增强：动画管家当前保存服务端返回的 `workflow_version/pipeline_commit/pipeline_sha256`，但尚未在最终发布前与批准基线做强制相等校验。

这些差异只记录在本文。本次对齐没有修改动画管家后端代码、数据库、环境变量、进程、工作流或活动任务。

---

## 2. 本次操作边界与活动任务保护

用户明确要求“别动动画管家的后端”。本次处理严格遵守：

- 未重启 Gateway、飞书接收器、WebUI、ComfyUI 或独立业务 worker；
- 未停止、暂停、取消、迁移或重建任何本地或远端任务；
- 未调用 GPU Control 创建、取消、重试、下载或管理接口；
- 未修改数据库、任务状态、batch ID、external ID、幂等键、manifest、输入 ZIP 或结果目录；
- 未修改动画管家源代码、配置或工作流；
- 只读取 V4 文档、现有实现和本地持久化状态，并新增本文档。

2026-07-28 17:16 +08:00 的只读本地快照：

| 动画管家抠图 run | 后端 | 帧数 | 远端 batch | 状态快照 |
|---|---|---:|---|---|
| `COMFY_0C1D9F553994` | `gpu_control` | 97 | `d8ab774b-a895-4983-a92b-60e456b8140e` | `RUNNING`；68 succeeded、2 running、10 queued、17 pending、0 failed、0 cancelled |
| `COMFY_56AC3DFF399F` | `local` | 54 | 无 | `RUNNING` |

第一个任务对应 V4 事件中的 `external_batch_id=assetclaw:VID_9D9EB9ACE6A1:matting:g1`。快照表明原 batch ID 已继续运行，动画管家没有建立第二份远端父任务。本文不对活动任务的最终结果作提前声明。

---

## 3. V4 事件归因确认

动画管家接受上游文档对本次事件的证据链：

- 动画管家冻结输入 ordinal 34 的原文件有效，大小 560646 字节，SHA-256 为 `0f8f9d005b9a13772840010aeca9d44021bdd6192d67351f25168932b1b27c28`；
- 3090-A 的 ComfyUI 输入目录出现同名 0 字节文件；
- 旧上传重试使用 `overwrite=false`，连接中断遗留的空文件没有被第二次成功响应覆盖；
- prompt 在未验证远端最终字节的情况下被提交，最终由 `LoadImage` 报输入无效；
- 动画管家没有发起取消，服务端 `cancel_requested=false`；
- 旧服务端把单帧失败收尾错误映射成 `CANCELLING`，造成 Web 误显示“取消中”。

因此当前事件的根因归属为统一调度中心的“上传完整性门禁缺失 + 失败/取消状态机混淆”，不是动画管家源 PNG、ImageClip 工作流、模型参数或用户取消导致。

GPU Control 对原批次的定点恢复必须继续满足：原 `batch_id`、external ID、ordinal、child job ID 和审计历史不变；修复 ordinal 34 不能创建第二个业务父批次。

---

## 4. 双方职责冻结

### 4.1 动画管家负责

1. 接收业务输入、抽帧并冻结不可变文件集合；
2. 生成一个 generation 唯一的 `external_batch_id` 和创建幂等键；
3. 生成严格 manifest 1.0、逐帧 size/SHA 和只有图片条目的 `ZIP_STORED`；
4. 创建超时或网络状态不明确时，复用原 ZIP、manifest 和 key 重放；
5. 创建成功后立即持久化服务端 `batch_id`，后续只查询该 ID；
6. 只把父任务公开状态作为业务进度真相，不把本机 ComfyUI 队列或 child 数量冒充远端进度；
7. 用户或管理员明确取消时，持久化完整取消意图后才调用取消接口；
8. 等待父任务进入公开终态；`RUNNING + failed>0` 仍继续等待；
9. `FAILED/CANCELLED` 均禁止进入 Cherry；
10. `SUCCEEDED` 后执行 artifact 和结果 ZIP 十项验证，全部通过后原子发布；
11. 负责 Cherry、序列完整性校验、业务 ZIP、飞书投递和送达回执。

### 4.2 统一调度中心负责

1. 严格验证 manifest、ZIP 文件集合、大小、SHA、路径和图片可解码性；
2. 固定创建时启用的工作流版本和完整管线身份；
3. 按节点健康、兼容性、槽位、租户公平性和热工作流分发 child job；
4. 每次向 ComfyUI 上传均使用 `overwrite=true`，并回读最终字节核对 size/SHA；
5. 只有上传完整性验证通过后才能提交 prompt；
6. 维护 child job 租约、attempt、心跳、节点失联恢复和 Comfy queue/history 对账；
7. 单帧最终失败后隔离该帧，其余帧继续执行；全部收敛后父任务变为 `FAILED`；
8. 只有全部帧成功才生成唯一 `result_archive`；失败批次不暴露部分结果；
9. 只有收到已认证取消 API 或管理员等价审计操作，才能设置 `cancel_requested=true`；
10. Web 顶层只显示父批次，逐帧详情只通过分页接口展示。

### 4.3 双方禁止事项

- 不得把系统失败、网络断开、watchdog 超时、进程重启、节点离线或租约过期记为用户取消；
- 不得在远端状态不明确时创建新 generation 或静默改走本机；
- 不得在父任务未 `SUCCEEDED` 时发布部分 matte、启动 Cherry 或返回业务 ZIP；
- GPU Control 不得修改动画管家的外部工作流、参数、模型、提示词或最终输出语义；
- 动画管家不得指定具体 GPU 节点，也不得操作服务端内部 child job；
- 任一方不得在活动批次期间暗改协议字段、状态语义或固定工作流版本。

---

## 5. 请求、身份、TLS、幂等与追踪

动画管家接受 V4 的接口与请求头：

```http
POST /api/v1/batches/imageclip-rgba
Idempotency-Key: <external_batch_id>
X-Request-ID: <每次动作的稳定审计 ID>
X-API-Key: <生产专用身份；正式冻结前必须配置>
```

双方固定以下规则：

- TLS 必须使用批准的 LAN CA，禁止 `verify=False` 和 `curl -k`；
- 生产与测试使用不同 API Key 或完全隔离的租户身份；
- 同一 generation 的创建重试必须复用原 ZIP、原规范化 manifest、原 external ID 和原 key；
- 同 key、同内容重放返回原 batch ID；同 key 不同内容返回 `409 IDEMPOTENCY_CONFLICT`；
- 不同 key、相同 external ID 返回 `409 EXTERNAL_BATCH_CONFLICT`；
- 每次 create/poll/cancel/download 都记录本地 request ID 和服务端响应 request ID；
- 创建响应中的 batch ID/external ID 必须与本地 generation 一致，否则 fail closed；
- 创建成功后 batch ID 必须在任何业务后续操作之前持久化。

当前动画管家已经实现冻结输入、创建幂等重放、batch ID 持久化、状态查询 request 序号和结果下载 request ID。生产专用 API Key 仍是双方正式冻结前的待办；来源 IP 认证只能作为短期联调兼容。

---

## 6. manifest 1.0 与输入包确认

动画管家接受且当前实现已按以下字段生成 manifest：

```json
{
  "schema_version": "1.0",
  "external_batch_id": "assetclaw:<parent_id>:matting:g<generation>",
  "failure_policy": "all_or_nothing",
  "output_naming": "preserve_stem_png",
  "parameters": {},
  "frames": [
    {
      "ordinal": 0,
      "relative_path": "video_01/0000.png",
      "size_bytes": 123456,
      "sha256": "64位小写十六进制"
    }
  ]
}
```

当前动画管家输入门禁包括：

- 1～5000 帧；
- 单帧不超过 64 MiB、单图不超过 40000000 像素；
- JPEG、PNG、WebP 可解码；
- ordinal 严格为 `0..N-1`；
- NFC、POSIX 安全相对路径，拒绝空段、绝对路径、反斜杠、`.`、`..` 和大小写折叠冲突；
- 输出路径固定保留 stem 并改为 `.png`，提前拒绝输出名冲突；
- ZIP 只包含 manifest 声明的图片，不包含显式目录、README、隐藏文件或额外条目；
- 图片条目使用 `ZIP_STORED`；
- 输入 ZIP 与 manifest 在第一次创建前持久化，重试前重新校验文件集合、大小和 SHA；
- 同一幂等键对应的输入发生变化时拒绝重放。

V4 没有改变 manifest 字段，因此该部分无需动画管家运行时迁移。

---

## 7. V4 父状态机与本地映射

### 7.1 公开父状态

```text
VALIDATING → QUEUED → RUNNING → ASSEMBLING → SUCCEEDED
                 │        │
                 │        ├─ 单帧最终失败：父任务仍 RUNNING，其他帧继续
                 │        │                    └─ 全部收敛后 → FAILED
                 │        │
                 └────────┴─ 仅明确取消 → CANCELLING → CANCELLED
父级不可恢复系统错误 ─────────────────────────────→ FAILED
```

终态只有 `SUCCEEDED`、`FAILED`、`CANCELLED`，没有 `PARTIAL`。

### 7.2 动画管家映射合同

| GPU Control 状态/事实 | 动画管家内部状态 | 用户展示 | 动作 |
|---|---|---|---|
| `VALIDATING/QUEUED` | `RUNNING` | 已进入 GPU 集群队列 | 保存 counts，继续查原 batch |
| `RUNNING, failed=0` | `RUNNING` | GPU 抠图中 | 继续轮询 |
| `RUNNING, failed>0` | `RUNNING` | 部分帧失败，其他帧继续收敛 | 不取消、不发布、不建新任务 |
| `ASSEMBLING` | `RUNNING` | 结果汇总校验中 | 继续轮询 |
| `SUCCEEDED` | 验证中 | GPU 完成，正在验证结果 | 十项验证通过后才 `DONE` |
| `FAILED` | `FAILED` | GPU 批次失败 | 保存父 error 和失败帧，不进入 Cherry |
| `CANCELLING` 且本地有合法 cancel intent | `CANCELING` | 取消收尾中 | 继续轮询 |
| `CANCELLED` 且本地有合法 cancel intent | `CANCELED` | 已取消 | 保存最终取消审计，不发布 |
| `CANCELLING/CANCELLED` 且本地无 cancel intent | `RUNNING`→`FAILED` | 异常收尾中/协议异常 | 立即告警并对账，绝不能显示“用户取消” |

当前动画管家已经正确等待 `RUNNING + failed>0`，只有父状态进入公开终态才收敛；`FAILED` 不发布 artifact，`SUCCEEDED` 还必须通过完整结果校验。

当前缺口是最后一行的本地取消意图核验尚未实施。安全改造后，远端取消状态不得单独证明“用户取消”。

---

## 8. 取消意图与审计合同

唯一合法取消接口：

```http
POST /api/v1/batches/{batch_id}/cancel
Idempotency-Key: <external_batch_id>:cancel
X-Request-ID: <业务取消 request ID>
```

动画管家在发出 HTTP 请求前必须原子持久化：

```json
{
  "cancel_intent": {
    "requested": true,
    "source": "user|administrator",
    "operator_id": "飞书 open_id 或管理员稳定 ID",
    "operator_name": "展示名，可选",
    "conversation_id": "业务会话 ID",
    "reason": "用户原话或管理员原因",
    "requested_at": "UTC ISO-8601",
    "request_id": "am-...-cancel-01",
    "idempotency_key": "<external_batch_id>:cancel",
    "http_status": null,
    "service_request_id": "",
    "remote_final_status": ""
  }
}
```

请求完成后补充 HTTP 结果和服务端 request ID；远端进入终态后补充 `remote_final_status`。审计记录不能被稀疏 cancel 响应覆盖。

以下情况不得调用 cancel API：

- 本地执行 watchdog 超时；
- 状态查询连续失败；
- 动画管家或 Scheduler 重启；
- 单帧失败或 `counts.failed>0`；
- 节点离线、心跳抖动、租约过期；
- 服务端返回错误但没有用户/管理员取消意图。

当前动画管家已使用固定 `<external_id>:cancel` key、保存取消时间和响应元数据，并等待远端 `CANCELLED` 后再显示终态；但尚未持久化上述完整 cancel intent，且 watchdog 超时仍会自动调用 cancel。两项均列为 V4 客户端必改项，但本次不修改运行后端。

---

## 9. 超时、断线、重启与恢复

V4 冻结以下行为：

1. 创建请求超时：复用原 ZIP、manifest、external ID 和 key 重放，获取原 batch ID；
2. batch ID 已知后的查询超时：按 1、2、4、8、15、30 秒退避，继续查原 batch；
3. 长时间无进度：记录 watchdog 告警、最后状态、counts 和 request ID，继续查询；
4. 动画管家重启：从本地持久化 batch ID 恢复，不重新上传、不新建 generation；
5. Scheduler 重启：服务端从 PostgreSQL、租约和 Comfy queue/history 恢复原 child job；
6. 节点离线：服务端按租约和审计重试 child job，不取消父批次；
7. 只有明确 cancel intent 才能中止远端父任务。

动画管家已经实现网络查询错误无限恢复原 batch ID、固定退避、worker 重启后挂接原 batch、禁止远端失败后静默回退本机。V4 安全改造必须删除“执行 watchdog 到期后自动 cancel”的副作用，改为“告警 + 持续恢复查询 + 等待明确人工动作”。

---

## 10. ComfyUI 上传完整性门禁

该门禁由统一调度中心在每个 child job 内实施：

```text
冻结输入读取并计算 size/SHA
  → overwrite=true 上传到 job 独立目录
  → GET /view 回读远端最终字节
  → 重算远端 size/SHA
  → 本地/远端完全一致
  → 才允许提交 prompt
```

双方确认：

- 每个 child job 默认最多 3 次上传完整性尝试；
- 重试必须使用同一 job 目录和文件名并强制覆盖；
- 0 字节输入不上传；
- 回读超时、连接断开和 SHA 不一致在内部预算内重试；
- 3 次仍失败时 child job 失败，由 Scheduler 的 job retry 层处理；
- 上传重试、job attempt 和动画管家 HTTP create retry 是三套独立计数；
- prompt 未提交前的传输失败不能记录为 Comfy 工作流执行失败；
- 任何传输/执行失败都不能设置父 `cancel_requested=true`。

GPU Control 在最终回执中必须提供自动测试证据：首次上传制造 0 字节文件、第二次覆盖成功、远端回读 size/SHA 与主控原字节一致、未向损坏输入提交 prompt。

---

## 11. 工作流版本与节点门禁

当前批准基线：

```text
workflow_key:     imageclip-rgba
workflow_version: 2026.07.30-691770c-r1
imageclip_commit: 691770cd6a59fd7c51391456fe900dc57a313233
pipeline_sha256:  00e7104762f0a1fdf3a4c20e043bec2b9f088132452d5a5ce4302ba268edac0b
only output node: SaveImage #25
```

GPU Control 必须在创建时固定版本，在节点领取前再次核对 Git commit、确定性 pipeline SHA、节点标签、模型身份和实时 `/object_info` class inventory。不匹配节点 fail closed，不领取新帧。

动画管家当前会保存服务端父状态返回的 `workflow_version`、`pipeline_commit` 和 `pipeline_sha256`，但尚未在结果发布前与批准基线做强制相等校验。V4 客户端安全改造建议增加发布硬门禁：

- 三个字段必须存在；
- 必须等于本 generation 创建时冻结的批准基线；
- 轮询过程中字段不得变化；
- 任一缺失、漂移或冲突均停止发布，保留结果包和审计信息，不进入 Cherry。

为支持双方独立审计，GPU Control 应在每次父状态 `GET` 和最终 artifact manifest 中稳定返回完整 workflow version、commit 和 pipeline SHA，而不只在节点内部日志保存。

---

## 12. capacity、排队和进度合同补充

V4 声明取代旧文档，但正文没有重新冻结 `/api/v1/scheduler/capacity`。为避免再次出现客户端 `WAITING_CAPACITY` 阻塞，双方必须在 V4 回执中明确继承以下语义：

1. capacity 是 advisory/telemetry，不预占槽位；
2. `accepting`、`accepting_batches`、`suggested_max_new_batches` 和 available slots 不能替代正式 admission；
3. 大帧任务一旦选择 GPU Control，动画管家立即调用幂等 `POST create_batch`；
4. 是否接单、排队和限流以 create 的 HTTP 响应为准；
5. 服务端接受后必须立即返回并持久化 batch ID，再由服务端管理 pending/queued/running；
6. `429` 使用同一请求内容和 key 退避重试；
7. capacity 探针失败不能导致同一任务静默改走本机；
8. 用户进度必须使用父状态的 `counts/progress`，不能显示客户端虚构的 0 或本机队列状态。

SSE 只作低延迟提示，断线不代表失败或取消；`GET /api/v1/batches/{batch_id}` 始终是最终状态真相。动画管家当前以 3 秒 GET 轮询保证正确性，未依赖 SSE 才能完成任务。

---

## 13. 结果包十项验证与原子发布

父任务只有 `SUCCEEDED` 才能出现唯一 `result_archive`。动画管家当前实现以下发布门禁：

1. artifact 元数据 SHA、`X-Artifact-SHA256` 和下载字节重算 SHA 一致；
2. 下载字节数等于 artifact `size_bytes`；
3. 包内 batch ID/external ID 等于本地持久化记录；
4. total/items 等于原输入帧数；
5. ordinal 恰好是 `0..N-1`，无缺失、重复或乱序；
6. 每项输入路径和输入 SHA 等于原 manifest；
7. 输出路径满足 `preserve_stem_png`，且 ZIP 条目集合恰好为 `manifest.json + results/...`；
8. 每个输出实际 SHA 等于 manifest output SHA；
9. 每个输出是可解码、带 Alpha 的最终 PNG；
10. 全部验证在 staging 中完成，随后同盘原子替换正式目录，失败时不污染旧结果。

此外动画业务层还会验证抽帧、matte、Cherry smooth 的逐帧同名和数量关系，最终业务 ZIP 还会执行 ZIP 完整性检查。任何门禁失败都禁止进入后续发布并保留可审计错误。

---

## 14. 动画管家 V4 差异矩阵

| V4 项目 | 当前状态 | 结论/待办 |
|---|---|---|
| manifest 1.0 严格字段 | 已实现 | 接受 |
| `ZIP_STORED`、路径、size/SHA | 已实现 | 接受 |
| external ID + create 幂等 | 已实现 | 接受 |
| batch ID 持久化与原 ID 恢复 | 已实现 | 接受 |
| `RUNNING + failed>0` 继续等待 | 已实现 | 接受 |
| 父 `FAILED` 不发布 artifact | 已实现 | 接受 |
| `SUCCEEDED` 后十项验证 | 已实现 | 接受 |
| 结果 staging 原子发布 | 已实现 | 接受 |
| 查询断线继续原 batch | 已实现 | 接受 |
| capacity 只 advisory、create 权威 | 已实现 | 要求 V4 服务端回执明确继承 |
| 取消 key 固定为 `<external_id>:cancel` | 已实现 | 接受 |
| cancel 响应后继续等远端终态 | 已实现 | 接受 |
| 完整 cancel intent 审计 | 未完整实现 | 安全窗口客户端必改 |
| watchdog 超时不自动取消 | 未实现 | 安全窗口客户端必改 |
| 无本地 cancel intent 时拒绝映射用户取消 | 未实现 | 安全窗口客户端必改 |
| 发布前强校验 workflow/commit/pipeline SHA | 只保存、未强门禁 | 建议升级为客户端必改 |
| SSE | 未作为正确性依赖 | 可选增强；GET 已满足最终真相 |
| 生产专用 API Key | 当前仍可能使用来源 IP | 双方冻结前必办 |
| GPU 上传回读 size/SHA | 服务端职责 | 等待 GPU Control 镜像发布与真实证据 |
| 单帧失败继续、最终父 FAILED | 服务端候选实现 | 等待真实注入验收 |

---

## 15. 安全实施顺序

### 15.1 本次不执行

当前存在正式 GPU Control 和本机抠图任务，因此本次只交付文档，不执行任何后端变更。

### 15.2 GPU Control 发布顺序

1. 等待生产父任务进入终态；
2. 确认在线节点数据库槽位为 0；
3. 确认 4090、3090-A Comfy queue 无 running/pending；
4. 构建固定版本号的新 API/Scheduler/Web 镜像并记录 image digest；
5. 先滚动 Web，再滚动 API/Scheduler；不重启 ComfyUI；
6. 核验容器健康、Scheduler 心跳、节点心跳、Redis subscriber、DB 租约；
7. 执行上传完整性、失败状态机、取消状态机和原 batch 恢复自动回归；
8. 与动画管家执行真实联合验收；
9. 证据完整后才标记 `FROZEN / PRODUCTION ACCEPTED`。

### 15.3 动画管家后续安全改造顺序

只有在用户另行明确授权、活动任务安全收敛后才执行：

1. 增加持久化 cancel intent/audit schema；
2. 删除 watchdog timeout 自动远端 cancel，改成告警和继续查询；
3. 远端 `CANCELLING/CANCELLED` 先核验本地 cancel intent，无审计则标协议异常；
4. 增加工作流 version/commit/pipeline SHA 发布硬门禁；
5. 增加对应单元测试、故障注入和恢复测试；
6. 只滚动动画管家必要服务，不结束独立活动 worker；
7. 使用新隔离 generation 做 1、6、64 帧真实验收；
8. 验收通过后更新本文证据，不回写历史活动任务。

---

## 16. 联合验收清单

每项必须保存测试时间、client/tenant、external ID、batch ID、全部 request ID、帧数、输入字节、工作流版本、commit、pipeline SHA、节点分布、attempts、父终态、artifact SHA、动画管家发布目录和最终业务状态。

### 16.1 正常与幂等

- [ ] 1 帧正常：创建、查询、下载、十项校验、Cherry 和业务投递完整通过；
- [ ] 30 帧嵌套中文 NFC 路径：ordinal、路径、文件名和 SHA 完全保持；
- [ ] 64 帧大任务：客户端直接 create，服务端排队，不出现客户端 `WAITING_CAPACITY`；
- [ ] 三个并发视频：在线节点公平参与，不饿死后创建父任务；
- [ ] 创建响应丢失：同 key 重放返回同 batch ID，双方顶层都只有一个父任务；
- [ ] 动画管家重启：从原 batch ID 恢复，无二次上传和新 generation；
- [ ] Scheduler 重启：原 batch/job ID、ordinal、attempts 和进度连续。

### 16.2 上传完整性与节点故障

- [ ] 首次上传注入 0 字节：第二次 `overwrite=true` 覆盖并回读 size/SHA 成功；
- [ ] 三次上传均截断：child job 按传输错误失败，不提交损坏 prompt；
- [ ] Comfy prompt 真执行错误：与上传完整性错误使用不同 code；
- [ ] 3090-B 离线：父任务不取消，兼容在线节点继续；
- [ ] 节点心跳抖动：在租约与 queue/history 对账前不重试、不取消；
- [ ] 管线漂移：不匹配节点停止领新帧，兼容节点继续。

### 16.3 失败与取消分离

- [ ] 单帧永久失败：其余帧继续，父任务最终 `FAILED`，无 artifact；
- [ ] 上一项在动画管家 Web/飞书中从未显示“用户取消”；
- [ ] 运行 watchdog 到期：动画管家只告警并继续查原 batch，GPU Control 未收到 cancel；
- [ ] 查询连续失败：恢复后仍查原 batch，无新任务、无 cancel；
- [ ] 用户真实取消：先有完整 cancel intent，再发送固定 cancel key；
- [ ] 管理员取消：有稳定管理员 ID、原因、request ID 和最终状态；
- [ ] 服务端注入无本地意图的 `CANCELLING`：动画管家显示协议异常而非用户取消；
- [ ] `CANCELLING → CANCELLED`：有合法取消审计时才映射本地已取消，无 artifact。

### 16.4 结果与版本门禁

- [ ] 结果缺帧、错序、错名、错误 SHA、额外文件或无 Alpha 均不能发布；
- [ ] artifact header SHA、元数据 SHA 和下载 SHA 不一致时拒绝发布；
- [ ] workflow version、commit 或 pipeline SHA 缺失/漂移时拒绝进入 Cherry；
- [ ] 全部通过时 staging 原子提升，原目录无半成品；
- [ ] 飞书最终文件具有真实消息回执，不能只显示“ZIP 已生成”。

---

## 17. GPU Control 回执模板

GPU Control 请复制本节，在新文档中逐项回复，不要只回复“已对齐”。

```yaml
gpu_control_v4_receipt:
  status: IMPLEMENTATION_CANDIDATE|ROLLED_OUT|PRODUCTION_ACCEPTED
  reviewed_assetclaw_document: GPU_CONTROL_MATTING_HANDOFF_V4_ASSETCLAW_ALIGNMENT.md
  reviewed_assetclaw_document_sha256: <sha256>

  source:
    git_commit: <full sha>
    api_image_digest: <sha256:...>
    scheduler_image_digest: <sha256:...>
    web_image_digest: <sha256:...>

  workflow:
    key: imageclip-rgba
    version: 2026.07.30-691770c-r1
    imageclip_commit: 691770cd6a59fd7c51391456fe900dc57a313233
    pipeline_sha256: 00e7104762f0a1fdf3a4c20e043bec2b9f088132452d5a5ce4302ba268edac0b
    parent_status_returns_all_identity_fields: true|false

  capacity_contract:
    endpoint: /api/v1/scheduler/capacity
    advisory_only: true|false
    create_batch_is_authoritative_admission: true|false
    server_persists_queue_after_accept: true|false

  upload_integrity:
    overwrite_true: true|false
    readback_size_check: true|false
    readback_sha256_check: true|false
    max_integrity_attempts: 3
    prompt_blocked_until_verified: true|false
    zero_byte_fault_test_id: <id>

  failure_state_machine:
    child_failure_keeps_parent_running_until_all_settle: true|false
    child_failure_never_sets_cancel_requested: true|false
    failed_batch_has_no_artifact: true|false
    legacy_failure_cancelling_ui_label: 失败收尾中

  cancel_contract:
    only_authenticated_cancel_or_admin_audit_can_cancel: true|false
    required_idempotency_key: <external_batch_id>:cancel
    cancellation_never_triggered_by_timeout_or_node_failure: true|false
    cancel_audit_retained: true|false

  recovery:
    postgres_is_source_of_truth: true|false
    scheduler_restart_reuses_batch_and_job_ids: true|false
    comfy_queue_history_reconciled_before_retry: true|false
    node_offline_does_not_cancel_parent: true|false

  rollout:
    active_batches_drained_before_rollout: true|false
    comfy_queues_empty_before_scheduler_rollout: true|false
    comfyui_not_restarted: true|false
    production_rollout_time: <ISO-8601|null>

  joint_acceptance:
    one_frame_batch_id: <uuid|null>
    six_frame_batch_id: <uuid|null>
    sixty_four_frame_batch_id: <uuid|null>
    zero_byte_fault_batch_id: <uuid|null>
    permanent_frame_failure_batch_id: <uuid|null>
    explicit_cancel_batch_id: <uuid|null>
    scheduler_restart_batch_id: <uuid|null>
    result_tamper_test_id: <id|null>
    final_artifact_sha256: <sha256|null>
```

---

## 18. 动画管家确认

- [x] 已完整读取 V4 上游文档并核对 SHA-256；
- [x] 接受 manifest 1.0、`ZIP_STORED`、`all_or_nothing` 和不发布部分结果；
- [x] 接受“单帧失败继续收敛，父任务最终 FAILED”的状态机；
- [x] 接受“失败不等于取消，只有明确审计取消才能进入 CANCELLED”；
- [x] 接受上传后回读 size/SHA 再提交 prompt 的服务端门禁；
- [x] 接受状态不明确时只重放原 key 或查询原 batch ID；
- [x] 接受 `SUCCEEDED` 后十项验证和原子发布；
- [x] 接受固定工作流身份和节点 fail-closed 边界；
- [x] 确认本次未修改或重载动画管家后端；
- [ ] 动画管家取消审计、timeout 行为和取消对账差异尚未在安全窗口实施；
- [ ] GPU Control V4 正式镜像尚未提供 rollout digest；
- [ ] 双方真实故障注入与联合验收尚未完成；
- [ ] 生产专用 API Key 尚待双方确认；
- [ ] 只有上述待办全部完成，本文状态才能改为 `FROZEN / PRODUCTION ACCEPTED`。
