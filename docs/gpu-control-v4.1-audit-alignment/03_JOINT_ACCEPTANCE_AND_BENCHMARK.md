# V4.1 联合验收与同素材速度基准

状态：`DRAFT / DO NOT RUN AGAINST ACTIVE PRODUCTION BATCHES`

## 1. 验收原则

1. 使用隔离 tenant、API key、generation、输出目录和可追踪 test ID；
2. 发布和测试前确认活动生产批次已收敛，不删除或重写历史批次；
3. 每项同时保存动画管家日志、GPU Control 父/子状态、request ID、trace、节点数据和产物；
4. 正确性先于速度，正确性失败的样本不能进入性能统计；
5. 失败和取消是两种不同测试，任何系统故障不得通过 cancel 收尾；
6. 结果必须包含所有运行，不得只选择最快样本；
7. 所有时间使用 UTC ISO-8601，持续时间优先使用 monotonic clock 计算。

## 2. 环境冻结清单

开始前双方共同记录：

```yaml
acceptance_session_id: v4_1-<date>-<sequence>
assetclaw:
  git_commit: <sha>
  config_digest: <sha256>
  host: <id>
  local_gpu: RTX 4070 Ti
  local_driver: <version>
gpu_control:
  source_commit: <sha>
  api_image_digest: <sha256:...>
  scheduler_image_digest: <sha256:...>
  web_image_digest: <sha256:...>
  node_images:
    control-4090: <digest>
    worker-3090-a: <digest>
    worker-3090-b: <digest>
workflow:
  key: imageclip-rgba
  version: 2026.07.27-721f7d6-r1
  imageclip_commit: 721f7d68635ee36d45f545ce2c82037046147442
  pipeline_sha256: 00e7104762f0a1fdf3a4c20e043bec2b9f088132452d5a5ce4302ba268edac0b
model_hashes: {}
network:
  client_to_api_rtt_ms: <value>
  link_speed: <value>
```

任一节点身份或 workflow 基线不匹配时不得开始速度验收。

## 3. 固定素材集

从真实素材中建立只读 benchmark bundle，并记录 bundle SHA：

| Bundle | 数量 | 用途 |
|---|---:|---|
| B1 | 1 帧 | 固定开销、完整链路 |
| B6 | 6 帧 | 小任务路由与浏览器/投递 |
| B30 | 30 帧 | 中文嵌套路径、普通批次 |
| B64 | 64 帧 | 远端阈值与服务端排队 |
| B97 | 97 帧 | 与现有生产样本对照的主速度基准 |
| B300 | 300 帧 | 动态分片和尾部拖慢 |

每帧 manifest 保存 ordinal、相对路径、宽高、像素、字节和 SHA。所有 backend 使用相同输入字节、工作流、模型和输出参数。

## 4. 正常与幂等验收

| Test ID | 场景 | 预期 |
|---|---|---|
| N-01 | B1 完整链路 | create→GPU→artifact→Cherry→投递 ack 全通过 |
| N-02 | B30 中文/NFC/嵌套路径 | ordinal/path/SHA/输出名完全保持 |
| N-03 | B64 capacity saturated | 客户端仍立即幂等 create；服务端排队 |
| N-04 | create 响应丢失 | 同 key 重放返回同 batch ID |
| N-05 | 动画管家 worker 重启 | 恢复原 batch，无二次上传/新 generation |
| N-06 | Scheduler 重启 | 原 batch/job/attempt/时间线连续 |
| N-07 | 三个并发 B97 | 公平推进，无后建父任务长期饿死 |
| N-08 | 下载中断 | `.part` 重下，SHA 全通过后原子发布 |

## 5. 故障注入验收

| Test ID | 注入 | 必须证明 |
|---|---|---|
| F-01 | 首次上传生成 0 字节远端文件 | overwrite=true 覆盖；回读 SHA；损坏输入从未 prompt |
| F-02 | 三次上传完整性均失败 | child 使用 UPLOAD_INTEGRITY 错误；父不取消 |
| F-03 | prompt timeout | 同 child attempt 重试/改派；不设置 cancel_requested |
| F-04 | 单帧永久失败 | 其他帧继续；父最终 FAILED；无 result archive |
| F-05 | 3090-B 离线 | 原 batch 继续；租约后改派；无用户取消 |
| F-06 | 节点心跳抖动 | queue/history 对账前不重复 prompt |
| F-07 | poll 断网 10 分钟 | 动画管家告警并持续查原 batch；无 cancel/新 batch |
| F-08 | execution watchdog 到期 | 无 cancel API；恢复后仍使用原 batch |
| F-09 | 非法远端 CANCELLED | 动画管家标协议异常，不显示用户取消 |
| F-10 | 合法用户取消 | 先有完整 intent；固定 key；最终双向 audit 一致 |
| F-11 | workflow version/commit/SHA 漂移 | 节点不领取或动画管家阻止发布/Cherry |
| F-12 | artifact SHA/缺帧/无 Alpha | 十项验证拒绝；正式目录不污染 |
| F-13 | Cherry worker 重启 | 复用 matte；active/idle 分离；不重复抠图 |
| F-14 | 投递 API 失败后恢复 | OUTPUT_READY/DELIVERY_FAILED→重发→有 ack 后 DONE |

## 6. 速度 A/B 设计

### 6.1 预热与重复

- 每种 backend 先冷启动 1 次，冷启动单独报告；
- 随后热启动 5 次，不删除任何失败样本；
- 本机、1 节点、2 节点、3 节点依次轮换，避免时间趋势偏置；
- 节点模式由 GPU Control 测试租户/调度约束实现，生产客户端仍不指定节点；
- 测试期间记录其他租户并发和节点利用率。

### 6.2 主矩阵

| Bundle | 本机 | 1 节点 | 2 节点 | 3 节点 | 并发 |
|---|---:|---:|---:|---:|---:|
| B1 | 5 | 5 | - | - | 1 |
| B6 | 5 | 5 | 5 | 5 | 1 |
| B30 | 5 | 5 | 5 | 5 | 1 |
| B97 | 5 | 5 | 5 | 5 | 1 |
| B300 | 3 | 3 | 3 | 3 | 1 |
| 3×B97 | - | - | - | 3 组 | 3 |

### 6.3 每次必须记录

- input frames、pixels、bytes；
- client prepare/upload；
- validated、queue、GPU execution、assembly、artifact return；
- 每节点 assigned/succeeded/attempts/gpu_service/P50/P95；
- workflow load、节点并发、GPU 利用率/显存/功耗；
- artifact bytes、SHA、输出质量结果；
- Cherry active、idle、package、delivery ack；
- true end-to-end。

## 7. 计算公式

```text
task_throughput_fpm = frames / gpu_execution_minutes
weighted_throughput = sum(frames) / sum(gpu_execution_minutes)
pixel_throughput = sum(input_pixels) / sum(gpu_execution_seconds)
paired_speedup = local_gpu_seconds / cluster_gpu_seconds
queue_wait = started_at - queued_at
straggler_ratio = (last_node_finish - median_node_finish) / parent_gpu_wall
routing_error = abs(predicted_finish - actual_finish) / actual_finish
```

报告任务中位数、加权吞吐、P50/P90/P95和 bootstrap 置信区间。不能只比较平均任务分钟数。

## 8. 冻结门槛

### 正确性

- N-01 至 N-08 全部通过；
- F-01 至 F-14 全部通过；
- 零重复父 batch、零静默 fallback、零错误用户取消；
- 输出数量/SHA/Alpha/路径 100% 正确；
- 新任务 delivery ack 覆盖 100%；
- stage/trace 覆盖 ≥95%，时间线冲突率 0。

### 性能

- B97 三节点热启动纯 GPU P50 ≤40 分钟，至少 90% ≤50 分钟；
- B97 三节点 paired speedup 中位 ≥2.7×，至少 90% ≥2.2×；
- queue wait P90 ≤5 秒；
- artifact return P95 ≤30 秒；
- straggler ratio P95 ≤15%；
- 三并发 B97 无任务等待超过同组中位完成时间的 1.5×；
- 7 天生产观察窗 GPU 批次成功率 ≥99%。

### 质量

- 与批准基线逐帧 alpha 输出通过现有质量门禁；
- 不允许丢帧、错序、错名、额外文件或无 Alpha；
- 如引入算法级加速，必须另做视觉回归并由业务验收。

## 9. 产出目录建议

```text
storage/gpu_control_v4_1_acceptance/<session_id>/
  environment.json
  manifests/
  requests/
  parent_status/
  child_jobs/
  node_metrics/
  artifacts/
  quality/
  report.json
  report.md
```

`report.json` 是机器事实源；`report.md` 只做摘要。双方分别保存一份不可变证据并交换最终 SHA。

## 10. 签署

```yaml
joint_acceptance:
  session_id: <id>
  started_at: <UTC>
  finished_at: <UTC>
  correctness_passed: true|false
  fault_injection_passed: true|false
  performance_passed: true|false
  quality_passed: true|false
  observation_window_passed: true|false
  assetclaw_report_sha256: <sha>
  gpu_control_report_sha256: <sha>
  assetclaw_signoff: <name/id/time>
  gpu_control_signoff: <name/id/time>
  unresolved_items: []
```

任一 `false` 或 unresolved P0/P1 存在时，合同状态保持 `JOINT ACCEPTANCE PENDING`。
