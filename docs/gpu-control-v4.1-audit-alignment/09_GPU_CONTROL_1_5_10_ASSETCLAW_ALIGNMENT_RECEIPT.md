# GPU Control 1.5.10 × AssetClaw 动画管家对齐回执

日期：2026-08-05  
GPU 回执：`84_2026-08-05_PARTIAL_SUCCESS_AND_FAILED_FRAME_REPAIR_HANDOFF.md`  
GPU 回执 SHA-256：`41E4F4848417F26C8C5CF3C6BDAE09CC0F51DDFB06A51891E1122AA2F1BBC70C`  
GPU Control：`1.5.10` / `d504a820239797dd66d5ffe11178127743b99d6d`  
本地状态：`DEPLOYED`；代码、WebUI 与后端安全重载均已完成

## 1. 已完成的动画管家侧修改

1. 将 `PARTIAL_SUCCESS` 作为正式终态接收，不再兼容 `FAILED + partial artifact`。
2. 远端状态持久化保留完整 `failed_items`，避免轮询合并时丢失失败帧身份。
3. 部分结果在发布前执行严格分区校验：
   - `counts.total/succeeded/failed/pending/queued/running/cancelled` 必须与原批次一致；
   - 成功 ordinal 与失败 ordinal 必须无重叠、无遗漏，刚好覆盖原始输入全集；
   - 每个失败项必须以 `ordinal + input_relative_path + input_sha256` 对上原始 Manifest；
   - `attempts` 必须为 1–3，`attempted_node_ids` 数量必须一致且节点不得重复；
   - `node_id` 必须等于最后一次实际尝试节点；
   - 任一身份、路径、SHA、计数或错误证据不一致，拒绝原子发布。
4. 成功子集先在 staging 验证 PNG、Alpha、输出 SHA 与防串帧身份；全部合同验证通过后才原子合并。
5. 视频直发和批量图片直发均支持失败项补算：
   - 保留已验收输出；
   - `skip_existing=true`；
   - 新建 generation、`external_batch_id` 与幂等键；
   - 只提交缺失帧/图片；
   - 补算强制走 GPU Control；
   - 客户端最多两轮；
   - 全部补齐后再进入角色校色、位置矫正与交付。
6. 4070 Ti 本机明确 OOM 时，仅该任务切换 GPU Control；原有正常路由策略不变。
7. WebUI 新增“GPU 失败帧补算”状态，展示累计已保留数量、失败 ordinal、错误码及跨节点尝试次数。

## 2. 明确的失败边界

- `FAILED`、工作流身份不匹配、Manifest/路径/SHA 篡改、结果装配异常继续 fail closed。
- 这些批次不会发布部分文件，也不会以补算掩盖批次级重大故障。
- 历史失败批次保持原始审计记录，不恢复、不改写；新逻辑只作用于新批次。

## 3. 离线验收结果

- Python 编译检查：通过。
- GPU Control、ComfyUI、图片/视频恢复定向回归：`74 passed`。
- 动画管家全量 Python 回归：`478 passed`。
- WebUI 领域测试：`12 passed`。
- WebUI 生产构建：Vite 构建通过。
- 未执行 B1–B4 生产故障注入；按 GPU 回执要求只能使用隔离测试批次，不能破坏真实用户任务。

## 4. 上线结果

代码修改期间未重启 Gateway、飞书 WS、API 或任何任务 worker，也未取消、迁移或改写当时的生产任务。2026-08-05 16:14（Asia/Shanghai）再次核验全部本地活动任务为 0 后，完成 Gateway 与飞书 WS 最小重载；WebUI 返回 HTTP 200。

上线后只读健康检查：

- Gateway：HTTP 200，DeepSeek brain 正常，ComfyUI 非 fake mode；
- GPU Control：`database=ok`、`redis=ok`、`accepting_batches=true`；
- GPU Control 队列：queued 0、running 0、active batches 0；
- GPU 节点：3/3 在线、3/3 空闲、可用 slot 3；
- 本地活动任务：0；
- 上线前回滚快照：`20260805-161113_pre-gpu-control-1_5_10-alignment-final_5509ef61b888`，1786 个载荷文件，验证通过。

## 5. 联合验收结论

| ID | 动画管家侧结果 |
|---|---|
| B1 | 能接收 GPU 内部换节点后完整 `SUCCEEDED`，无需客户端补算 |
| B2 | 能验收 96/97 子集并仅提交缺失 ordinal 93，最多两轮 |
| B3 | 路径、SHA、ordinal、计数或失败身份被篡改时拒绝合并，已有 matte 不删除 |
| B4 | 工作流身份不匹配时保持 `FAILED`，不下载、不发布、不补算 |

结论：双方合同已经在代码与生产运行层闭环。剩余动作只有双方约定隔离批次的 B1–B4 故障注入联合验收；不得使用真实用户任务进行破坏性取证。
