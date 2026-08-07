# GPU Control 部分成功交付与失败帧增量补算对齐说明

日期：2026-08-05  
提出方：AssetClaw 动画管家  
优先级：P0（避免成功 GPU 计算结果被整批丢弃）

## 1. 现场事故与结论

任务：`VID_A473715AB10C`  
文件：`爆炸头男子头部转正动作-3.mp4`  
远端批次：`40bf32a0-17b4-47ce-8173-77d383867253`

- 输入与抽帧：97/97 正常，960×960。
- GPU Control 工作流身份：`VERIFIED`，V2 契约字段完整。
- 远端执行：96 帧成功，1 帧失败。
- 失败帧：原始 ordinal `93`，相对路径 `video_01/0093.png`。
- 错误：`COMFY_TIMEOUT`，历史 ID `8194798a-fa38-40b6-abb7-72db806e7f39`。
- 失败节点：`worker-3090-b`。
- 远端未发生重分配：`reassignments=0`。
- 远端将批次整体标记为 `FAILED`，且没有发布成功 96 帧的 result archive。
- 动画管家本地 matte 目录最终为 0 张，因此无法保留远端已经完成的 96 帧。

这不是工作流、输入全集或 V2 身份契约故障，而是单节点、单帧超时。成功帧被整体丢弃不可接受。

## 2. 必须遵守的恢复原则

1. 帧级故障必须帧级恢复。
2. 已通过 SHA-256、透明 PNG、输入身份校验的成功帧不可重新计算。
3. 单帧超时、节点离线、瞬时上传/下载错误不得触发整批重跑。
4. 失败帧第一次重试必须优先换节点；不得立即回到原故障节点。
5. 只有以下批次级重大故障允许整批失败：
   - 工作流身份不匹配；
   - 输入 Manifest 整体损坏或签名不一致；
   - 输出路径映射发生冲突；
   - 结果归属无法验证；
   - 全部节点均无法加载被批准的工作流；
   - 操作员明确要求整批重跑。

## 3. GPU Control 需要实现的 P0 行为

### 3.1 集群内部先进行失败帧换节点重试

建议每帧最多执行 3 次：

1. 首次执行节点失败；
2. 第二次必须排除上一次节点；
3. 第三次在其余健康节点中选择；
4. 三次均失败后，该帧才进入最终失败清单。

批次不应因为一个 job 失败立即终止，其他成功 job 和正在执行的 job 必须继续完成。

### 3.2 支持终态 `PARTIAL_SUCCESS`

当 `succeeded > 0` 且 `failed > 0` 时，推荐终态：

```json
{
  "status": "PARTIAL_SUCCESS",
  "counts": {
    "total": 97,
    "succeeded": 96,
    "failed": 1,
    "pending": 0,
    "queued": 0,
    "running": 0,
    "cancelled": 0
  }
}
```

兼容方案：暂时仍返回 `FAILED`，但只要 `succeeded > 0`，也必须发布部分成功产物和失败帧清单。动画管家已经兼容这两种状态。

### 3.3 返回逐帧失败清单

状态响应必须增加 `failed_items`：

```json
{
  "failed_items": [
    {
      "ordinal": 93,
      "input_relative_path": "video_01/0093.png",
      "input_sha256": "<64-char-lowercase-sha256>",
      "code": "COMFY_TIMEOUT",
      "message": "ComfyUI timeout: /history/8194798a-fa38-40b6-abb7-72db806e7f39",
      "node_id": "worker-3090-b",
      "attempts": 3,
      "attempted_node_ids": ["worker-3090-b", "worker-3090-a", "control-4090"]
    }
  ]
}
```

`ordinal + input_relative_path + input_sha256` 必须共同匹配原输入 Manifest，禁止只返回文件名。

### 3.4 失败批次也必须发布成功帧结果包

当存在成功帧时，`artifacts` 必须包含且只能包含一个 `kind=result_archive` 的可下载产物。ZIP 仍使用 `ZIP_STORED`，并提供：

- `size_bytes`
- `sha256`
- `download_url`
- `content_type=application/zip`

部分结果 `manifest.json`：

```json
{
  "schema_version": "1.0",
  "batch_id": "40bf32a0-17b4-47ce-8173-77d383867253",
  "external_batch_id": "assetclaw:VID_A473715AB10C:matting:g1",
  "total": 97,
  "items": [
    {
      "ordinal": 0,
      "input_relative_path": "video_01/0000.png",
      "input_sha256": "<sha256>",
      "output_relative_path": "video_01/0000.png",
      "output_sha256": "<sha256>",
      "status": "SUCCEEDED",
      "job_id": "<job-id>",
      "node_id": "control-4090",
      "attempts": 1
    }
  ]
}
```

约束：

- `total` 是原批次总帧数，本例必须为 97。
- `items` 只包含成功帧，本例为 96 项。
- ordinal 保持原批次编号，升序、唯一，但允许不连续。
- ZIP 只包含 `manifest.json` 和这些成功帧的 `results/<output_relative_path>`。
- 失败帧不得伪造空 PNG 或沿用旧输出。

## 4. 动画管家已经实现的接收逻辑

动画管家会执行以下强校验后才接受部分结果：

1. 校验 ZIP 总 SHA-256。
2. 校验 batch ID 与 external batch ID。
3. 校验每个成功项的原始 ordinal、输入路径和输入 SHA-256。
4. 校验输出路径和输出 SHA-256。
5. 校验 PNG 具有有效透明 Alpha。
6. 校验帧身份，防止串帧。
7. 将成功帧原子写入原任务 matte 目录，保留已有合格帧。
8. 根据原输入全集与已发布成功 ordinal 计算缺失帧。
9. 新建只包含缺失帧的 GPU 批次；使用新的 external batch ID 和幂等键。
10. 补算结果合并后，必须通过 97/97 连续性校验，才能进入后处理和交付。

补算上限默认 2 轮。超过上限后任务失败，但已经成功的帧仍保留，不再整批删除。

## 5. 状态机

```text
RUNNING
  ├─ 全部成功 ──────────────> SUCCEEDED -> 下载完整包
  ├─ 部分成功、仍可重试 ────> RETRYING_FAILED_FRAMES
  │                              ├─ 补齐 -> SUCCEEDED
  │                              └─ 达上限 -> PARTIAL_SUCCESS
  └─ 批次级重大故障 ─────────> FAILED

PARTIAL_SUCCESS
  -> 发布成功帧包
  -> 返回 failed_items
  -> 动画管家仅提交失败帧补算
  -> 合并并验证全集
```

## 6. 当前事故的恢复要求

请 GPU Control 对批次 `40bf32a0-17b4-47ce-8173-77d383867253` 执行以下任一方案：

优先方案：

1. 从持久化 job 产物恢复 96 张成功 PNG；
2. 按本文件格式生成部分 result archive；
3. 在原批次状态接口补充 artifact 和 failed_items；
4. 返回新回执。

如果成功产物已被清理：

1. 明确回复 `SUCCESS_ARTIFACTS_PURGED`；
2. 给出清理时间与保留策略；
3. 修复保留策略后重新执行本批次；
4. 后续不得在批次终态确认及客户端下载前清理成功帧。

## 7. 联合验收用例

### B1：97 帧中第 93 帧首次超时

- 第 93 帧自动换节点重试。
- 其他 96 帧不重算。
- 最终成功时产物 97/97。

### B2：第 93 帧三次仍失败

- 状态为 `PARTIAL_SUCCESS`。
- 成功包包含 96 张，不包含第 93 帧。
- `failed_items` 精确返回 ordinal 93。
- 动画管家下载 96 张后只提交 1 张补算。

### B3：部分结果被篡改

- 任意输入 SHA、输出 SHA、ordinal 或相对路径不匹配时，动画管家拒绝发布。
- 已有合格 matte 不得被覆盖或删除。

### B4：批次级工作流身份不匹配

- 不发布任何可交付结果。
- 返回明确的批次级错误码。
- 禁止自动进行帧级补算。

## 8. GPU 集群回执必须包含

```text
GPU Control 版本：
部署 commit：
部署时间：
PARTIAL_SUCCESS 已实现：是/否
FAILED + partial artifact 兼容：是/否
failed_items 已实现：是/否
失败帧换节点重试：是/否
成功帧保留时长：
事故批次 96 张是否仍可恢复：是/否
事故批次新 artifact：
B1/B2/B3/B4 测试结果：
```

没有上述回执和联合验收，不视为问题 2 完成。
