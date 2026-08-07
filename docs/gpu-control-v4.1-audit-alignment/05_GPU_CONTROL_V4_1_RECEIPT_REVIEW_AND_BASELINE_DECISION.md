# GPU Control V4.1 首轮回执审阅与联合基线决定

## 1. 回执证据

- 原始文件：`C:\Users\zhangqichao\Downloads\64_2026-07-30_ASSETCLAW_GPU_CONTROL_V4_1_RECEIPT.md`
- SHA-256：`7D6DE2BC7F77770D87F32CEC313640A09715D461FB1F2F5FB171625DC8FD039D`
- GPU Control 审阅提交：`63deec8f57dede18ee64703ccc2b2726032e2f07`
- 回执状态：`IMPLEMENTING / JOINT ACCEPTANCE PENDING / RUNTIME UNCHANGED`
- 结论：这是可信的首轮事实回执，不是 V4.1 上线签字或速度验收报告。

## 2. ImageClip 身份决定

动画管家批准当前生产身份，不要求 GPU Control 为匹配旧文档回滚：

```yaml
workflow_key: imageclip-rgba
workflow_version: 2026.07.30-691770c-r1
imageclip_commit: 691770cd6a59fd7c51391456fe900dc57a313233
pipeline_sha256: 00e7104762f0a1fdf3a4c20e043bec2b9f088132452d5a5ce4302ba268edac0b
output_node: "SaveImage #25"
```

依据：GPU Control 三节点和生产数据库使用该身份；动画管家 2026-07-30 稳定回滚快照记录的 `C:\imageclip` HEAD 也是同一个 `691770c...`；新旧身份的 pipeline SHA-256 相同。历史任务仍保留其原始 `721f7d6` 身份，不能改写历史记录。

从本决定开始，新提交观察到任一非空 workflow key/version/commit/SHA 与以上基线不一致时，动画管家 fail closed。GPU Control 暂未返回的字段标为 `UNVERIFIED_MISSING`，不能伪造为已验证。

## 3. 动画管家已开始执行的责任项

- watchdog 超过观察阈值只告警和继续用原 batch ID 对账，不自动 cancel、不切本机重跑；
- 显式取消先持久化 actor/source/reason/request ID/idempotency key，再调用远端；
- 收到远端 `CANCELLED` 但没有本地取消意图时，判定为合同异常并失败闭锁；
- create/cancel 稀疏响应继续安全合并，不抹掉已知身份、时间、计数和进度；
- 记录准备、创建、轮询、远端终态、下载、发布、完成阶段时间；
- 记录 trace ID、request ID、轮询错误总数、最后状态变化和 idle gap；
- 结果 ZIP 支持已完成文件复用和 `.part` Range 断点续传，最终仍执行 size/SHA 验证与原子发布；
- 本机 4070 Ti 已忙时，小任务进入 GPU Control 持久队列，避免本机串行阻塞并提高两端并行利用率。

## 4. GPU Control 上线前仍必须修复

以下服务端 P0 未完成前，禁止把状态写为 `PRODUCTION ACCEPTED`：

1. 禁止 batch child 通过通用 job cancel 绕过父取消合同；
2. 父批次未 `SUCCEEDED` 时禁止访问 child artifact；
3. 公共父取消提供完整、持久、幂等的 operation/audit；
4. 发布标签、运行包版本、Web 版本、source revision 和 registry manifest digest 形成同一证据链；
5. 新批次不可变快照 workflow identity，并在 create、每次 GET 和 artifact manifest 返回；
6. 修复不兼容节点被优先选择后阻塞兼容节点的问题；
7. 修复 prompt submit 与 prompt ID 持久化之间的重复执行窗口。

## 5. 性能拉满所需的服务端数据

三台 GPU 是否真正达到预期吞吐，必须由 GPU Control 补齐 `performance.nodes[]`、纯 `gpu_service_ms`、像素量、每节点 P50/P95、attempt 分层、最大并发 prompt、reassignment、scheduler restart、queue estimate 和 straggler。之后使用固定 B1/B6/B30/B64/B97/B300 做同素材本机/1/2/3 节点热跑与并发 `3×B97`；没有原始 report JSON/MD 时不接受“约三倍”结论。

## 6. 当前发布门禁

- 动画管家本地优化：已通过 455 项回归并完成 Gateway 最小重载，状态 `DEPLOYED_NOT_ACCEPTED`；
- 读取现有生产状态：可以；
- 修改/重启 GPU Control 生产：不可以，等待 GPU Control 新镜像与签署回执；
- 联合固定素材压测：等待服务端 P0、隔离 tenant/key 和固定 bundles；
- 灰度：等待联合验收通过后按 10% → 50% → 100%；
- 回滚：动画管家使用 `20260730-115717_stable-pre-v4_1_1f84ceba1276`；GPU Control 必须补正式 registry digest 与 rollback version。
