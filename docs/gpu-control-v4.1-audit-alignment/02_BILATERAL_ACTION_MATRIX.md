# V4.1 双方行动矩阵

状态：`IMPLEMENTING / LOCAL DEPLOYED_NOT_ACCEPTED / JOINT ACCEPTANCE PENDING`

本矩阵把 2026-07-30 审计问题映射为动画管家、GPU Control 和联合验收的明确责任。`完成` 只能由代码、版本和测试证据证明。

## 1. P0：正确性与可靠性

| ID | 问题/风险 | 动画管家动作 | GPU Control 动作 | 完成证据 |
|---|---|---|---|---|
| P0-01 | 26 个动画流程 0 成功 | 修复飞书表格权限预检、worker 生命周期、stage 时间；连续完成最小流程 | 无直接代码责任；保证远端子批次可追踪 | 连续 10 个流程 ≥90% 成功；每阶段有时间与 trace |
| P0-02 | watchdog 会自动 cancel 远端批次 | 删除 timeout cancel；告警并持续查询原 batch | 确认 timeout/node loss 不会设置 cancel_requested | 故障注入后 batch ID 不变、无 cancel API、最终可恢复 |
| P0-03 | cancel intent 不完整 | 原子保存来源、操作者、原因、会话、request ID、key、最终状态 | 保存认证主体和服务端 cancel audit | 用户/管理员取消可双向对账；重复请求幂等 |
| P0-04 | 远端 CANCELLED 可被误报用户取消 | 无本地 intent 时映射为协议异常，不映射 CANCELED | 无合法 audit 不得进入 CANCELLED | 注入非法 CANCELLED，UI 显示协议异常 |
| P0-05 | DONE 缺投递回执 | 引入 OUTPUT_READY；send_ack 后才 DONE | 无 | 新成功任务 delivery receipt 覆盖 100% |
| P0-06 | workflow 身份只保存不强校验 | create/GET/artifact/Cherry 前强校验 | 每次父 GET 与 artifact 稳定返回完整身份 | 缺失/漂移注入均 fail closed，不进入 Cherry |
| P0-07 | GPU 上传中断可留下损坏输入 | 客户端保持输入 ZIP/manifest 不变 | overwrite=true，回读 size/SHA 后才 prompt | 0 字节故障测试：第二次覆盖成功，损坏输入无 prompt |

## 2. P1：可恢复执行与真实时间

| ID | 问题/风险 | 动画管家动作 | GPU Control 动作 | 完成证据 |
|---|---|---|---|---|
| P1-01 | 单 prompt 600 秒卡死击穿整批 | 本机 checkpoint、失败帧重排、健康检查、熔断切集群 | child job attempt/租约/改派；父批次继续收敛 | 已完成帧不重做；单帧永久失败时父 FAILED、无 artifact |
| P1-02 | 抽帧与本地 queue 混合 | 写 extract start/end、queue entered、execution start | 无 | 新任务 95% 以上阶段互斥且时间单调 |
| P1-03 | 全局 queue depth 被当任务排队 | UI 改用 queued_at→started_at | 返回冻结时间语义 | 页面排队值能从父状态重算 |
| P1-04 | 集群内部阶段混合 | 保存 validated/queued/started/execution_finished/artifact_ready | 返回全部权威时间 | 上传、排队、GPU、汇总、回传可独立显示 |
| P1-05 | Cherry 38–42 小时空转被算成执行 | 保存 attempt 与 worker heartbeat；计算 active/idle | 无 | idle gap >5 分钟告警；三条历史型异常不再误归因 |
| P1-06 | 状态快照不能解释重启 | append-only task_events + trace | 父/子审计事件、scheduler restart 和 reassignments | 任一恢复可追到 worker/node/request ID |

## 3. P2：速度、调度与长尾

| ID | 问题/风险 | 动画管家动作 | GPU Control 动作 | 完成证据 |
|---|---|---|---|---|
| P2-01 | 只有任务级 node_distribution | 持久化并展示 node performance | 返回每节点帧数、GPU 秒、P50/P95、attempt、版本 | 父 counts 与节点总和完全对账 |
| P2-02 | `VID_9D9...` 97 帧仅 1.179 帧/分 | 用同素材/版本关联诊断 | 查节点并发、显存、工作流加载、慢分片 | 能定位执行期慢点；回归样本达到门槛 |
| P2-03 | 异构节点分片可能拖尾 | 不指定节点，只展示 straggler | 按历史 Mpixel/s 加权，动态 work stealing | 节点拖尾 P95 ≤15% |
| P2-04 | 固定帧数路由不能总是最快 | 记录本机/集群预测与实际误差 | capacity/create 提供 advisory queue estimate 与兼容节点数 | 路由预测 MAPE ≤25%，不破坏 create 权威性 |
| P2-05 | 小任务远端固定开销 | 本机空闲且预测更快时留本机 | 缓存工作流、减少调度/上传固定开销 | 1/6/30 帧分桶的 P90 下降，成功率不降 |
| P2-06 | 大任务最后少量慢帧拖尾 | 无 | 动态领取或推测性重算，保持 ordinal 幂等 | 300 帧批次末尾拖尾显著下降，无重复发布 |

## 4. P3：统计、面板与容量规划

| ID | 问题/风险 | 动画管家动作 | GPU Control 动作 | 完成证据 |
|---|---|---|---|---|
| P3-01 | 当前页面只取 20+20+流程 | 后端分页/按时间聚合；显示 cohort 与截断 | 无 | 页面与审计脚本同条件数量/P50/P90 一致 |
| P3-02 | 简单平均吞吐偏置 | 同时报告任务中位数、总帧/总 GPU 秒、Mpixel/s | 提供 pixels 和 gpu_service_ms | 大小任务不会因等权平均扭曲容量 |
| P3-03 | 无版本/分辨率维度 | 持久化并筛选 version/resolution/model | 稳定返回版本、模型和节点身份 | 发布前后性能可比较，漂移可告警 |
| P3-04 | 无异常基线 | median/MAD + 确定性规则；样本不足不自动建模 | 稳定 error code 与性能字段 | 慢点自动标注且可解释 |
| P3-05 | 无真实端到端 | 补 feishu ingress、下载、投递 ack | 通过 trace/request ID 关联抠图 span | true_e2e 可完整重算 |

## 5. 代码落点（动画管家）

以下是 2026-07-30 第一批动画管家实现落点：

| 位置 | 当前行为 | V4.1 改造 |
|---|---|---|
| `src/assetclaw_matting/skills/comfyui_skills.py::_run_gpu_control_worker` | 已删除 timeout cancel；非法 CANCELLED 失败闭锁 | 本地 `DEPLOYED_NOT_ACCEPTED`，待故障注入 |
| `src/assetclaw_matting/skills/comfyui_skills.py::run_cancel` | 已在远端调用前保存 actor/source/reason/request ID/key | 本地 `DEPLOYED_NOT_ACCEPTED`，待服务端 operation/audit |
| `src/assetclaw_matting/services/gpu_control_batch.py::compact_remote_state` | 已 additive 保存完整阶段字段和 performance | 本地 `DEPLOYED_NOT_ACCEPTED`，等待服务端返回数据 |
| `src/assetclaw_matting/services/gpu_control_batch.py::download_artifact` | 已支持完整文件复用与 `.part` Range 续传 | 本地 `DEPLOYED_NOT_ACCEPTED`，待中断下载联合测试 |
| `src/assetclaw_matting/services/hybrid_matting_router.py` | 4070 Ti 忙时把小任务送入服务端持久队列 | 本地 `DEPLOYED_NOT_ACCEPTED`，待并发吞吐压测 |
| `src/assetclaw_matting/services/gpu_control_batch.py::verify_and_publish_result` | 十项验证与原子发布 | 发布前增加冻结身份强校验 |
| `external_webui/src/domain/task-performance.js` | 集群阶段“含排队”；Cherry 使用包络 | 读取真实时间；active/idle 分离 |
| `external_webui/src/components/PerformanceDashboard.vue` | 最大 handshake queue depth 作为洞察 | 改为任务实际 queue P50/P90、节点拖尾与数据可信度 |
| 动画流程/飞书/投递 worker | 阶段时间和 ack 不完整 | 统一 task_events 与 trace helper |

当前本地状态：P0-02、P0-03 客户端部分、P0-04、P0-06 观察值强校验、P1-04 客户端阶段、P1-05 idle gap、P3-05 GPU span 已于 2026-07-30 完成 Gateway 最小重载，状态为 `DEPLOYED_NOT_ACCEPTED`。P0-01、P0-05、P1-01/02/06 的其余非 GPU 流程部分仍需下一批实现；所有项目在生产观察和联合原始证据完成前都不是 `ACCEPTED`。

## 6. GPU Control 回执必须给出的实现落点

GPU Control 需要在回执中填写其真实仓库位置，至少包括：

- create/admission 与父状态 serializer；
- Scheduler queue/lease/recovery；
- child upload integrity gate；
- Comfy prompt submit/history reconciliation；
- node heartbeat 与 compatibility gate；
- artifact assembly；
- cancel authentication/audit；
- parent/child performance aggregation；
- fault injection 与 A/B runner。

未给 source commit、镜像 digest、测试 ID 的项目保持 `UNVERIFIED`。

## 7. 状态值

每个 ID 只能使用：

- `NOT_STARTED`
- `IN_PROGRESS`
- `IMPLEMENTED_NOT_DEPLOYED`
- `DEPLOYED_NOT_ACCEPTED`
- `ACCEPTED`
- `BLOCKED`（必须写阻塞者和下一动作）

“代码存在”“本地测过”“应该没问题”都不能等同于 `ACCEPTED`。
