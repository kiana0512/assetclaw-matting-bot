# 07 可观测性与异常检测技术规格

## 1. 推荐技术组合

### 追加式事件表作为事实源

保留当前状态 JSON 供恢复，但新增 append-only `task_events`。状态快照回答“现在是什么”，事件回答“为什么、何时变成这样”。

建议字段：

```text
event_id, trace_id, task_id, parent_task_id, task_type,
stage, event_name, attempt, worker_id, backend, node_id,
event_at_utc, monotonic_ns, status,
frames, pixels, input_bytes, output_bytes,
workflow_version, pipeline_commit, model_hash,
queue_depth, concurrent_jobs, error_code, error_detail,
attributes_json
```

`event_at_utc` 用于跨进程关联，`monotonic_ns` 用于同一进程精确计时；所有事件必须有唯一 ID，重试写新 attempt，不覆盖旧记录。

### OpenTelemetry trace

一个飞书请求一个 `trace_id`，父任务和 Comfy/Cherry/上传子任务作为 span：

```text
feishu.receive
  attachment.download
  task.route
  frame.extract
  matte.queue
  matte.execute
    matte.node.<id>
  postprocess.cherry
  package.zip
  delivery.upload
  delivery.send_ack
```

日志中统一注入 `trace_id/task_id/run_id/attempt/node_id`。这样可以从页面一键跳到同一任务的全部日志，而不是按文件猜测。

### Metrics 与日志

- Prometheus 风格指标：成功率、queue seconds、GPU seconds、seconds/frame、Mpixel/s、idle gap、retry、delivery ack missing。
- 结构化 JSON 日志：错误堆栈和调试细节；禁止只有自由文本错误。
- GPU 节点采集：利用率、显存、功耗、温度、时钟、OOM/ECC、worker heartbeat、当前 batch。

如果暂时不引入完整平台，先落 SQLite/Postgres 事件表并由现有 Vue 页面查询，也能获得大部分价值。

## 2. 必需事件

| 阶段 | 开始事件 | 结束事件 | 关键属性 |
|---|---|---|---|
| 飞书入口 | `feishu.event_received` | `route_finished` | event id、消息类型 |
| 附件 | `download_started` | `download_finished` | bytes、HTTP code、重试 |
| 抽帧 | `extract_started` | `extract_finished` | fps、输入/输出帧数 |
| 抠图队列 | `matte_queued` | `worker_assigned` | backend、queue position |
| GPU 执行 | `matte_started` | `matte_finished` | node、frames、pixels、workflow |
| 后处理 | `cherry_started` | `cherry_finished` | attempt、browser instance |
| 打包 | `package_started` | `package_finished` | 文件数、bytes |
| 投递 | `upload_started` | `send_ack` | file/message id、API code |
| 等待用户 | `user_wait_started` | `user_wait_resolved/expired` | question id、reminder count |
| 恢复 | `worker_lost` | `task_resumed` | heartbeat、exit code、resume source |

所有终态都要带 `reason_code`；取消至少记录 `USER_CANCEL/SYSTEM_CANCEL/ADMIN_CANCEL/DEPENDENCY_CANCEL`。

## 3. 核心派生指标

1. `true_e2e = send_ack - feishu.event_received`
2. `queue_wait = worker_assigned - matte_queued`
3. `gpu_service = matte_finished - matte_started`
4. `active_post = Σ(cherry_finished - cherry_started)`
5. `post_idle_gap = post_envelope - active_post`
6. `weighted_throughput = Σframes / Σgpu_service_minutes`
7. `pixel_throughput = Σpixels / Σgpu_service_seconds`
8. `straggler_ratio = (last_node_finish - median_node_finish) / batch_gpu_wall`
9. `delivery_confirmation_rate = acked_success / output_ready`
10. `stage_coverage = 有完整开始结束事件的阶段数 / 应有阶段数`

平均值、中位数、P90/P95/P99都保留；页面默认展示中位数与 P90，并显示样本数。

## 4. 异常检测

### 第一层：确定性规则

- worker heartbeat >90 秒；
- prompt 超过同配置历史 P99 与绝对上限；
- active attempt 间 gap >5 分钟；
- 小于 10 MB 投递 >60 秒；
- 终态无 reason/ack；
- 阶段时间倒序或阶段和超过生命周期。

### 第二层：稳健统计

按 `task_type + backend + workflow_version + resolution_bucket + node_count` 建基线。对 `seconds/frame`、Mpixel/s、queue、idle gap 使用 median/MAD；稳健 Z >3.5 标异常。样本不足 20 时只做规则告警，不做自动基线判断。

### 第三层：趋势与变更点

- EWMA/CUSUM 检测版本发布后的速度漂移；
- 版本对比用 bootstrap 置信区间；
- 失败原因用 Pareto，监控新 error_code 的首次出现；
- 容量趋势用队列等待与 GPU 利用率共同判断，避免只看 backlog。

无需一开始就上复杂机器学习。当前最有价值的是事件完整、维度正确、规则可解释；样本积累后再做 Isolation Forest 或季节性模型。

## 5. 数据质量门禁

只有满足以下条件的记录才进入速度基线：

- 状态成功且有明确投递/产物终态；
- 阶段时间单调；
- 帧数、分辨率/像素、workflow version、backend 已知；
- GPU execution 与 queue 分开；
- 没有人工等待混入执行时间；
- 若发生 retry，单独报告首次成功与总资源成本。

不满足门禁的任务仍显示在运营和异常页面，但打上 `low_confidence`，不参与容量规划。

## 6. 面板接口建议

服务端提供聚合接口，不让浏览器只拉 20 条后自己算总体：

```text
GET /api/performance/summary?from=&to=&task_type=&backend=&version=
GET /api/performance/tasks?...&cursor=&limit=
GET /api/performance/task/{id}/timeline
GET /api/performance/failures/pareto?...
GET /api/performance/nodes?...
GET /api/performance/data-quality?...
```

响应必须返回 `cohort_start/cohort_end/sample_count/truncated/query/data_quality`。分位数由后端针对完整 cohort 计算，不能从当前页的 20 条记录估算。

## 7. 落地顺序

1. 先加事件表、trace id、stage helper 和 delivery ack。
2. Comfy/Cherry/flow worker 接入同一 helper，补 heartbeat/restart。
3. 后端实现全量聚合与分页；用本审计脚本做对账测试。
4. 前端替换固定 20 条统计，增加 cohort 与可信度。
5. 数据稳定一周后启用 MAD 与趋势告警。
6. 累积统一 A/B 数据后再做自动调度与容量预测。
