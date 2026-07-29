# 动画管家 ↔ GPU Control 批量抠图 V3 对齐回执与联合验收合同

文档状态：`ASSETCLAW ALIGNED / ADVISORY QUEUE POLICY HOT-LOADED / JOINT ACCEPTANCE PENDING`

动画管家侧文档版本：`3.0.1-am2`

生成时间：`2026-07-27 19:06 +08:00`

上游交接文档：`40_GPU_CONTROL_MATTING_HANDOFF_V3.md`

上游文档 SHA-256：`f449f349c411ec358803ffdc002e206c7852f4ad95ec43bd7c7c09cbabaf05e1`

GPU Control 候选版本：`1.3.3`

manifest 协议：`1.0`

生产入口：`https://10.3.34.11`

本文件是动画管家读取 GPU Control V3 交接文档后给出的实现对齐、差异修正、热更新边界和联合验收回执。GPU Control 读取本文件后，应按第 19 节格式返回确认。双方完成真实联调前，状态保持 `JOINT ACCEPTANCE PENDING`；禁止单方面写成 `FROZEN`。

## 1. 结论先行

动画管家接受 V3 的核心合同：

- manifest 固定为 `schema_version=1.0`；
- 输入固定为只有图片文件的 `ZIP_STORED`；
- 一个业务抠图 generation 对应一个不可变 `external_batch_id`、一个幂等键和一个远端父批次；
- `all_or_nothing`，不接受部分成功；
- 远端状态不明确时只恢复同一 `batch_id`，不创建第二份任务；
- 远端失败、断线或超时不得静默切到本机重复执行；
- 只有父批次 `SUCCEEDED` 且结果十项校验全部通过后才原子发布 matte；
- Cherry、编码、业务打包和飞书返回始终由动画管家负责；
- 顶层只展示父任务，帧 job 只进入父详情；
- 每次请求严格验证 TLS，不使用 `verify=False` 或 `curl -k`；
- 生产与压测客户必须隔离。
- capacity/accepting/suggested 字段只作为遥测和路由建议；大帧任务一旦选定 GPU Control，客户端立即执行幂等 `POST create_batch`，排队由服务端负责。

当前协议代码已完成 V3 差异修正并通过本地回归。由于生成本文时存在多个正式 GPU Control/本机抠图批次，动画管家没有重启生产服务；代码热加载必须等活动任务安全收敛后执行，详见第 15 节。

## 2. 双方版本基线

### 2.1 GPU Control / ImageClip 基线

动画管家接受以下生产候选基线：

| 项目 | 固定值 |
|---|---|
| GPU Control | `1.3.3` |
| ImageClip repository | `/opt/imageclip` |
| ImageClip branch | `main` |
| ImageClip commit | `721f7d68635ee36d45f545ce2c82037046147442` |
| pipeline SHA-256 | `00e7104762f0a1fdf3a4c20e043bec2b9f088132452d5a5ce4302ba268edac0b` |
| workflow version | `2026.07.27-721f7d6-r1` |
| GPU 执行 API 图节点数 | `44` |
| generator graph digest | `797f423ca1808790162f8402bcec67f99420db9864b9409f60c317740e002eca` |
| registry template SHA | `63e56d99bc125156016c544f26679406c84b3640123a8cec0ae762eb598c485c` |
| 唯一输出节点 | `SaveImage #25` |
| 最终上游节点 | `CodexLazyShadowBypassV43 #57` |

说明：动画管家本机保存的是 51 节点 UI 工作流；GPU Control 的 44 节点是从唯一最终 SaveImage 反向裁剪得到的生产 API 祖先图。两者节点数不同不是版本冲突。真正的版本身份以完整 Git commit、确定性 pipeline SHA、workflow version、generator digest 和 registry template SHA 共同确认。

### 2.2 三节点身份

| node_id | MAC | 当前地址 | 角色 |
|---|---|---|---|
| `control-4090` | `58:11:22:c1:66:63` | `10.3.34.11` | 控制面、归档、可调度 GPU |
| `worker-3090-a` | `18:c0:4d:9f:13:13` | `10.3.34.12` | 主算力 |
| `worker-3090-b` | `2c:f0:5d:76:7b:70` | 动态地址，当前文档记录 `10.3.34.4` | 主算力 |

动画管家只消费稳定 `node_id`，不把 IP 当节点身份。GPU Control 必须继续用 node_id、MAC、GPU UUID、签名和来源 IP 共同校验节点代理。

## 3. 动画管家当前生产配置事实

以下为 2026-07-27 只读核验结果，不包含密钥值：

| 配置 | 当前值 | 结论 |
|---|---|---|
| `MATTING_BACKEND_MODE` | `hybrid` | 接受；路由后单批后端不可改变 |
| `GPU_CONTROL_BASE_URL` | `https://10.3.34.11` | 对齐 |
| `GPU_CONTROL_VERIFY_TLS` | `true` | 对齐 |
| `GPU_CONTROL_CA_BUNDLE` | `C:\Users\zhangqichao\Downloads\GPU_CONTROL_LAN_CA.crt` | 已存在 |
| CA SHA-256 | `ad4a4dbd95bb789be03451ff0c25b2bc65dfe170428bd675789c2ebba1e6dc2b` | 与 V3 完全一致 |
| `GPU_CONTROL_ALLOW_CA_WITHOUT_KEY_USAGE` | `true` | 当前 LAN CA 兼容模式；仍执行证书链与主机验证 |
| `GPU_CONTROL_API_KEY` | 未配置 | 当前使用来源 IP 身份；见未决项 |
| 大批阈值 | `64` 帧 | 小批且本机空闲走本机，大批/本机忙走集群 |
| 单批上限 | `5000` 帧 | 对齐 |
| 客户端在途父批次上限 | `8` | 仅用于告警与观测，不阻止服务端持久化排队 |
| 普通轮询 | `3` 秒 | 对齐 |
| 状态断线退避 | `1,2,4,8,15,30` 秒 | V3 对齐 |

正式冻结前建议 GPU Control 向动画管家分别签发生产 API Key 和隔离 test API Key。当前来源 IP 模式能工作，但出口 IP 改变后会成为不同租户，不能作为长期生产身份方案。

## 4. 只读生产握手证据

2026-07-27 14:xx +08:00，动画管家使用指定 LAN CA 对生产入口执行只读请求：

| 请求 | 结果 | X-Request-ID |
|---|---|---|
| `GET /health/live` | HTTP 200，`status=live` | `am-v3-align-live-01` |
| `GET /health/ready` | HTTP 200，`status=ready`、`database=ok`、`redis=ok` | `am-v3-align-ready-01` |
| `GET /api/v1/scheduler/capacity` | HTTP 200，V3 `advisory=true`、`accepting=true` | 2026-07-27 18:51+08 实测 |

`scheduler/capacity` 已上线，当前 V3 返回字段为顶层 `advisory`、`accepting`，并在 `cluster/client` 下返回队列、节点和额度快照。动画管家兼容旧字段 `accepting_batches`，但不会再用 `suggested_max_new_batches=0` 或容量快照阻塞正式创建。最终接单、幂等和服务端排队以 `POST /api/v1/batches/imageclip-rgba` 的响应为准；capacity 请求失败也不得把已路由远端的大帧任务留在 `WAITING_CAPACITY`。

2026-07-27 18:57+08 联调证据：84 帧任务 `VID_49BB45CC93DB` 在 `suggested_max_new_batches=0` 时仍成功创建远端 batch `9cba0b51-0fb7-450e-9cb1-22f44ce184c3`，服务端返回 `RUNNING` 并持久化 `pending/queued/running` 计数。

## 5. 当前真实活动任务与热更新保护

2026-07-27 14:20 +08:00 的最新快照检测到四个正式父批次正在运行，其中三个使用 GPU Control、一个使用本机 ComfyUI：

| 动画管家 run | GPU Control batch | 帧数 | workflow version | 状态快照 |
|---|---|---:|---|---|
| `COMFY_C927D4693763` | `21793981-de3f-453b-b5fe-71f603e69122` | 171 | `2026.07.27-721f7d6-r1` | `RUNNING` |
| `COMFY_5D4195988D0F` | `507c9f7e-9d98-49ba-90c8-3839eb31f583` | 118 | `2026.07.27-721f7d6-r1` | `RUNNING` |
| `COMFY_CD212493CC39` | `26bc3c94-c757-495d-b12a-45dcdad39c5f` | 128 | `2026.07.27-721f7d6-r1` | `RUNNING` |
| `COMFY_8815D4352E77` | 本机，无远端 batch | 27 | 本机任务启动时固定工作流 | `RUNNING` |

因此本次处理遵守以下规则：

1. 不重启 ComfyUI、Gateway、飞书接收器或独立业务 worker；
2. 不取消、迁移或重新创建上述 batch；
3. 不修改它们的 external ID、幂等键、manifest、ZIP 或 generation；
4. 现有远端和本机 worker 均按启动时已加载的代码完成；
5. V3 修正代码先落盘，待活动任务归零后再安全 reload；
6. reload 后从持久化 batch ID 恢复，禁止重新上传或双写。

## 6. 创建批次合同

### 6.1 请求

```http
POST /api/v1/batches/imageclip-rgba
X-API-Key: <production-key；当前暂用来源 IP>
Idempotency-Key: <external_batch_id>
X-Request-ID: am-<业务ID>-create-<序号>
Content-Type: multipart/form-data

archive=<ZIP_STORED zip>
manifest=<UTF-8 JSON 字符串>
```

字段名固定为 `archive` 和 `manifest`。客户端上传超时为 86400 秒；相同网络重试复用同一磁盘 ZIP、同一 manifest、同一 external ID 和同一幂等键。

### 6.2 manifest

```json
{
  "schema_version": "1.0",
  "external_batch_id": "assetclaw:VID_ABC:matting:g1",
  "failure_policy": "all_or_nothing",
  "output_naming": "preserve_stem_png",
  "parameters": {},
  "frames": [
    {
      "ordinal": 0,
      "relative_path": "视频一/镜头_010/000001.png",
      "size_bytes": 4839201,
      "sha256": "64位小写十六进制"
    }
  ]
}
```

动画管家已实现：

- 1～5000 帧；
- ordinal 严格为 `0..N-1`；
- UTF-8 NFC、POSIX `/`、无空段、无 `.`/`..`、无反斜杠、无绝对路径；
- 大小写折叠后输入路径与输出路径均唯一；
- 单帧 1～64 MiB，最大 40000000 像素；
- 只接受可解码 JPEG、PNG、WebP；
- manifest 不发送任何额外字段；
- `parameters` 当前必须精确为 `{}`。

### 6.3 输入 ZIP

动画管家生成的输入包：

- 每个图片条目都是 `ZIP_STORED`；
- 不包含目录项、manifest、README、隐藏文件或其他条目；
- 条目集合与 manifest 完全一致；
- 重试前重新核对 ZIP 条目 size 和 SHA，输入变化立即拒绝复用旧幂等键；
- ZIP 总量上限 100 GiB。

## 7. 幂等与未知状态恢复

| 场景 | 唯一允许动作 |
|---|---|
| 创建 202 | 持久化 `batch_id`，开始查询 |
| 同 key、同内容重放 200 | 接受原 `batch_id`，不得新增父任务 |
| 创建超时，不知道是否受理 | 用原 ZIP、manifest、external ID、key 重试 |
| 同 key、内容变化 | 本地先拒绝；若到服务端则期望 409 `IDEMPOTENCY_CONFLICT` |
| external ID 被另一 key 占用 | 期望 409 `EXTERNAL_BATCH_CONFLICT` |
| 输入确实变化 | 创建新 generation，同时更换 external ID 和 key |
| 已有 batch ID 后动画管家重启 | 只恢复 GET 同一 batch，不重新 POST |

本地持久化目录：

```text
storage/gpu_control_batches/<COMFY_RUN_ID>/
  input.zip
  input_manifest.json
  result.zip          # 只有下载成功后出现
```

## 8. 状态、进度和 UI 映射

### 8.1 状态映射

| GPU Control | 动画管家内部 | 用户显示/动作 |
|---|---|---|
| `VALIDATING` | `RUNNING` | 正在校验输入 |
| `QUEUED` | `RUNNING` | 已进入 GPU 集群队列 |
| `RUNNING` | `RUNNING` | 显示真实 counts 和 progress |
| `ASSEMBLING` | `RUNNING` | 正在汇总并校验结果包 |
| `CANCELLING` | `CANCELING` | 取消请求已提交，继续轮询 |
| `CANCELLED` | `CANCELED` | 终态，无 artifact、无 Cherry |
| `FAILED` | `FAILED` | 终态，无本机回退、无 Cherry |
| `SUCCEEDED` | 仍保持处理中 | 下载并执行十项校验 |
| 十项校验及原子发布完成 | `DONE` | 才允许父流程进入 Cherry |

### 8.2 进度规则

动画管家直接使用父状态 `progress`，并在客户端执行单调保护：

```text
display_progress = max(previous_progress, clamp(server_progress, 0, 100))
```

同时持久化并展示：

```text
counts.total
counts.pending
counts.queued
counts.running
counts.succeeded
counts.failed
counts.cancelled
node_distribution
workflow_key
workflow_version
created_at / started_at / finished_at
```

不得根据本机 ComfyUI 队列推算远端进度，不得把 progress 当 ETA。SSE 未来只作低延迟提示，GET 始终是最终真相。

### 8.3 轮询断线

- 正常轮询间隔：3 秒；
- 连续错误退避：1、2、4、8、15、30 秒，之后保持 30 秒；
- 达到告警阈值只通知“状态不明确，继续恢复”，不把业务批次直接判失败；
- 始终查询原 batch ID；
- 直到得到终态或达到明确的业务执行超时；
- 执行超时触发取消时，幂等键仍必须精确为 `<external_batch_id>:cancel`。

## 9. 请求 ID 和持久化字段

动画管家请求 ID 使用以下模式：

```text
<run_id>-create-01
<run_id>-poll-000001
<run_id>-poll-000002
<run_id>-download-01
<run_id>-cancel-01
```

请求 ID 只使用 `[A-Za-z0-9._:-]` 且不超过 64 字符。服务端可能采用或重写请求 ID，因此动画管家以响应 `X-Request-ID` 为实际值，并和 HTTP 状态一起写入 remote state。

### 9.1 本地字段映射

| V3 事实 | 动画管家持久化位置 |
|---|---|
| `external_batch_id` | `comfyui_runs.options_json.external_batch_id` |
| `Idempotency-Key` | `options_json.gpu_control.idempotency_key` |
| 输入 manifest SHA | `options_json.gpu_control.manifest_sha256` |
| `batch_id` | `options_json.gpu_control.batch_id` |
| 父状态 | `options_json.gpu_control.status` |
| progress / counts | `options_json.gpu_control.progress/counts` |
| 节点分布 | `options_json.gpu_control.node_distribution` |
| workflow key/version | `options_json.gpu_control.workflow_key/workflow_version` |
| pipeline commit/SHA | 服务端返回时写入 `pipeline_commit/pipeline_sha256` |
| 响应 request ID / HTTP | `response_meta`、`create_response_meta`、`cancel_response_meta`、`result_download` |
| 轮询序号 | `options_json.gpu_control.poll_sequence` |
| artifact SHA/路径 | `result_archive_sha256/result_archive_path` |
| 帧级证据 | 发布后 `prompt_map`，含 ordinal、输入/输出 SHA、job/node/attempts |

GPU Control 当前父状态已返回 `workflow_version`，但未返回 `pipeline_commit` 和 `pipeline_sha256`。这不阻塞 V3 核心执行，因为服务器已在创建和领取时硬门禁；为了动画管家和用户侧审计，建议在父状态增加这两个只读字段。

## 10. 取消合同

```http
POST /api/v1/batches/{batch_id}/cancel
Idempotency-Key: <external_batch_id>:cancel
```

动画管家不会在收到取消 HTTP 响应后立即伪造 `CANCELLED`：

1. 有 batch ID：发送固定 cancel key；本地进入 `CANCELING`；保存响应 request ID；继续轮询；
2. GPU Control 返回父 `CANCELLED` 后，本地才进入 `CANCELED`；
3. 无 batch ID 但 create 正在进行：先标记本地取消；create 一旦得到 batch ID，立即用同一固定 cancel key 提交；
4. 取消请求失败：不把本地状态伪装成取消成功；
5. 终态批次重复取消必须返回当前终态。

## 11. SUCCEEDED 门禁和 artifact 合同

父状态为 `SUCCEEDED` 时，动画管家还要求：

- `counts.total == 输入帧数`；
- `counts.succeeded == 输入帧数`；
- pending、queued、running、failed、cancelled 全部为 0；
- 恰好一个 `kind=result_archive` artifact；
- artifact 有非空 id；
- filename 精确为 `<batch_id>-rgba.zip`；
- content type 精确为 `application/zip`；
- size_bytes 为正数；
- sha256 为 64 位；
- download_url 与配置服务同源。

下载时三重 SHA 和大小都必须一致：

```text
artifact.sha256
== HTTP X-Artifact-SHA256
== 下载字节重算 SHA-256

下载实际字节数 == artifact.size_bytes
```

任一失败都保留 result ZIP/元数据/错误证据，不覆盖旧 matte，不进入 Cherry。

## 12. 结果 ZIP 十项校验

动画管家在同盘 staging 中执行以下全部校验：

1. 整包 SHA 与 artifact 元数据、响应头、下载重算值一致；
2. result manifest 的 batch ID / external ID 与本地一致；
3. schema、total、items 数量与输入一致，manifest 不允许额外字段；
4. items 数组 ordinal 精确为 `0..N-1`，顺序不可打乱；
5. 每项输入相对路径和输入 SHA 与本地冻结 manifest 一致；
6. 输出路径等于 `preserve_stem_png` 规则，大小写折叠后唯一；
7. ZIP 集合精确为 `manifest.json + results/...`，拒绝额外文件、显式目录、符号链接；
8. 每个输出 payload SHA 等于 output SHA；
9. 每个输出必须为可解码 PNG、包含有效 Alpha，不能是预览/蒙版/黑底中间图；
10. 全部通过后才用 `os.replace` 原子提升 staging；提升失败时恢复旧目录。

`job_id`、`node_id`、`attempts` 同时保存用于对账。严格视频帧模式还会执行源帧与 matte 尺寸/身份一致性检查。

## 13. 父任务与帧详情边界

- 一个 3000 帧业务批次在动画管家和 GPU Control 顶层都只能出现一行父任务；
- 动画管家 `comfyui_runs` 保存一个父 run，不创建 3000 条顶层 DB 任务；
- 帧 job 仅从 `/manifest?offset=&limit=&status=` 获取并放到父详情；
- Web 顶层进度直接来自父 counts/progress；
- 子帧完成乱序不影响最终 ordinal 和路径；
- 任何帧失败都使父批次失败且不发布 artifact。

## 14. hybrid 路由边界

`hybrid` 只在任务创建前选择一次后端：

1. 显式指定 `local`：整批本机；
2. 显式指定 `gpu_control`：只要基础配置有效就整批提交；capacity 是 advisory，不在客户端等待；
3. 帧数达到 64：整批 GPU Control，并立即 `create_batch` 进入服务端持久化队列；
4. 本机已有活动抠图：新整批 GPU Control；
5. 小批且本机空闲：整批本机；
6. 大于远端 5000 帧：保持完整任务在本机或由业务层显式分 generation，客户端不暗拆；
7. 一旦远端父批次已创建，任何失败都不得回退本机。

readiness/capacity 用于健康检查、展示、告警和小任务路由建议。大帧任务已选择远端后，capacity 不拥有本地 admission 权；`create_batch` 才是权威接单操作。服务端忙时由服务端返回 batch ID 并排队，动画管家保存 batch ID 后持续轮询，禁止显示 `WAITING_CAPACITY`、禁止回退本机、禁止创建第二个 generation。

## 15. 热更新与任务保护合同

### 15.1 动画管家代码热更新

存在活动父任务时：

- 允许编辑源代码和文档；
- 禁止执行全量 `start_bot_local.ps1`；
- 禁止重启 ComfyUI；
- 禁止结束独立 direct-video/direct-image worker；
- 禁止删除 `storage/gpu_control_batches`；
- 禁止修改活动 run 的 options、manifest 或 archive；
- 新协议只对 reload 后新启动的 worker 生效；旧 worker 按其启动快照收敛。

安全 reload 顺序：

1. 查询所有非终态父任务；
2. 若仍有本机 ComfyUI/Cherry 阶段，等待自然完成；
3. 远端批次必须已保存 batch ID 和 immutable handoff；
4. 优先使用只滚动 Gateway/飞书接收器的路由 reload，不结束独立 worker；
5. reload 后检查 run worker 是否仍存活；缺失时只用 `run_resume` 挂接原 batch；
6. 验证进度不回退、父任务不重复、GPU Control 不新增 batch；
7. 新任务再使用 V3 修正代码。

### 15.2 GPU Control 管线热更新

GPU Control 必须继续执行 V3 第 11 节事务顺序：drain/暂停新 ImageClip、fast-forward、三节点同步、commit+pipeline SHA、object_info、唯一最终祖先图、兼容表、签名心跳、单帧/三帧 smoke、再恢复生产。活动 batch 固定创建时 workflow version，不得中途换版本。

## 16. 本次动画管家侧修正清单

| 修正 | V3 风险 | 当前结果 |
|---|---|---|
| 远端取消改为 `CANCELING → CANCELLED` | 本地提前显示取消成功 | 已修正 |
| 所有取消统一 `<external_id>:cancel` | timeout 使用不同 key | 已修正 |
| 轮询断线不再达到固定次数即失败 | 远端仍运行、本地误判失败 | 已修正 |
| 错误退避改为 1/2/4/8/15/30 | 与 V3 恢复节奏不一致 | 已修正 |
| poll request ID 加持久化序号 | 多次请求难以对账 | 已修正 |
| progress 客户端单调保护 | 用户看到进度回退 | 已修正 |
| 持久化 workflow/version/timestamps | 父任务缺版本证据 | 已修正 |
| artifact 恰好一个且字段严格 | 接受歧义 artifact | 已修正 |
| 校验 artifact size | 只校验 SHA、遗漏大小 | 已修正 |
| 结果 ZIP 拒绝目录/符号链接 | 精确集合验证不完整 | 已修正 |
| 路径拒绝 `//` 空段 | PurePath 可能静默归一化 | 已修正 |
| SUCCEEDED counts 全部收敛 | 尚有 pending/running 仍发布 | 已修正 |
| HTTP 500 加入同请求重试 | 5xx 状态不明确恢复不完整 | 已修正 |

实现位置：

- `src/assetclaw_matting/services/gpu_control_batch.py`
- `src/assetclaw_matting/skills/comfyui_skills.py`
- `tests/test_gpu_control_batch.py`

GPU Control V3 相关回归：`16 passed`。本次未向生产集群提交测试任务。

## 17. 未决项与阻塞级别

### 17.1 FROZEN 前必须完成

1. 为动画管家提供专用生产 API Key；
2. 提供隔离 test API Key，并确认其 `client_kind=test`；
3. 完成第 18 节真实联合验收；
4. 当前全部活动批次完成后，执行动画管家安全 reload 并验证恢复；
5. 双方保存同一份证据记录，再把状态改为 `FROZEN / PRODUCTION ACCEPTED`。

### 17.2 建议但不阻塞核心合同

1. 父状态增加 `pipeline_commit` 和 `pipeline_sha256`，便于动画管家侧审计；
2. 明确 `/api/v1/scheduler/capacity` 的规划；
3. 对所有接口确认响应 `X-Request-ID` 是回显客户端值还是服务端重写后的实际值；
4. 提供三节点当前签名心跳摘要的只读运维接口或验收导出。

## 18. 第二轮联合验收计划

必须使用隔离 test 客户，禁止混入真实任务列表：

| 顺序 | 用例 | 通过标准 |
|---:|---|---|
| 1 | TLS/身份 | CA 严格校验；同一 Key 可创建/查询/下载 |
| 2 | 1 帧真实 v4.3 | SaveImage 25、Alpha、SHA、node ID 全对 |
| 3 | 30 帧中文 NFC + 两级目录 | ordinal/目录/文件名/输入输出 SHA 全对 |
| 4 | 两个并发 30 帧父任务 | 顶层 2 行，不是 60 行；三节点参与 |
| 5 | 创建超时幂等重放 | 同 key 返回同 batch ID，无新增父行 |
| 6 | 错误输入 SHA | HTTP 422，无可执行父任务 |
| 7 | 运行中取消 | `CANCELLING → CANCELLED`，无 artifact |
| 8 | 动画管家重启恢复 | 原 batch ID、进度不回退、不重传 |
| 9 | artifact/单帧篡改 | SHA/缺帧/错序/错名/无 Alpha 均被拒绝 |
| 10 | 单节点 pipeline SHA 漂移 | 漂移节点停止领取，另两台继续 |
| 11 | 7:3 ImageClip:ModelView 压力 | production 优先，test 不饿死生产 |

每个用例必须记录：时间、client kind、external ID、batch ID、全部 request ID、帧数、输入字节、workflow version、commit、pipeline SHA、创建/排队/执行/总耗时、节点分布、attempts、artifact SHA、动画管家 staging/最终目录和最终业务状态。

## 19. GPU Control 读取后的固定回执格式

GPU Control 不要只回复“已对齐”。请复制以下结构并逐项填写：

```yaml
alignment_response: GPU_CONTROL_V3
gpu_control_version: "1.3.3"
document_read:
  upstream_sha256: "f449f349c411ec358803ffdc002e206c7852f4ad95ec43bd7c7c09cbabaf05e1"
  assetclaw_alignment_version: "3.0.0-am1"
contract:
  manifest_1_0: ACCEPTED|REJECTED
  zip_stored: ACCEPTED|REJECTED
  all_or_nothing: ACCEPTED|REJECTED
  immutable_external_id: ACCEPTED|REJECTED
  idempotent_create: ACCEPTED|REJECTED
  exact_cancel_key: ACCEPTED|REJECTED
  parent_only_top_level: ACCEPTED|REJECTED
  succeeded_only_artifact: ACCEPTED|REJECTED
  no_client_gpu_selection: ACCEPTED|REJECTED
  no_silent_local_fallback: ACCEPTED|REJECTED
pipeline:
  workflow_version: "2026.07.27-721f7d6-r1"
  commit: "721f7d68635ee36d45f545ce2c82037046147442"
  pipeline_sha256: "00e7104762f0a1fdf3a4c20e043bec2b9f088132452d5a5ce4302ba268edac0b"
  generator_graph_digest: "797f423ca1808790162f8402bcec67f99420db9864b9409f60c317740e002eca"
  registry_template_sha256: "63e56d99bc125156016c544f26679406c84b3640123a8cec0ae762eb598c485c"
  final_save_node: "25"
  final_upstream_node: "57"
identity:
  production_api_key_ready: true|false
  isolated_test_api_key_ready: true|false
  source_ip_mode_end_date: "YYYY-MM-DD or N/A"
observability:
  parent_returns_workflow_version: true|false
  parent_returns_pipeline_commit: true|false
  parent_returns_pipeline_sha256: true|false
  response_request_id_policy: "echo|rewrite|other"
  scheduler_capacity: "supported|optional-404|planned"
hot_update:
  active_batch_version_pinned: true|false
  drain_before_enable: true|false
  signed_heartbeat_gate: true|false
  object_info_refresh_seconds: 60
deviations: []
blocking_questions: []
ready_for_joint_acceptance: true|false
```

任何 `REJECTED`、`false` 或 deviation 都要给出字段级原因和计划版本，不能静默改变合同。

## 20. 双方最终签署条件

以下全部满足后才能冻结：

- [x] 动画管家接受 manifest 1.0、ZIP_STORED、all-or-nothing；
- [x] 动画管家实现 immutable ID/key、全量结果校验和原子发布；
- [x] 动画管家不在远端失败后静默本机接管；
- [x] 动画管家父任务进度使用远端真实 progress/counts；
- [x] CA SHA 与 V3 一致，TLS 只读握手通过；
- [x] 当前生产 workflow version 与 V3 一致；
- [ ] GPU Control 按第 19 节返回结构化确认；
- [ ] 生产 API Key 与隔离 test API Key 就绪；
- [ ] 活动批次完成后动画管家安全 reload；
- [ ] 第 18 节真实联合验收全部通过；
- [ ] 双方追加 batch ID、artifact SHA、节点分配和验收时间；
- [ ] 文档状态共同改为 `FROZEN / PRODUCTION ACCEPTED`。

在此之前，本文件是详细的联调候选合同，不是“已经完成全部生产验收”的声明。
