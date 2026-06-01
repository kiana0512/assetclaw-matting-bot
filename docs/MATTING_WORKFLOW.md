# Matting Workflow

目标流程：用户提供 `input_dir` 和 `output_dir`，通过 `matting.batch_create` 建批次，`matting.batch_start` 开始，使用 `matting.batch_status` 查看进度，并支持 pause、resume、cancel。

当前 ComfyUI 是 fake mode：batch skills 会写入 SQLite、统计输入目录图片数量、更新批次状态，但不会调用 GPU。这样可以先让飞书和 Brain Router 跑通抠图管家的自然语言流程。

当前可用：

- `matting.batch_create`
- `matting.batch_start`
- `matting.batch_status`
- `matting.batch_pause`
- `matting.batch_resume`
- `matting.batch_cancel`

自然语言示例：

- `用 E:\assetclaw-matting-bot\storage\batch_inputs 创建一个抠图批次`
- `启动批次 BATCH_xxx`
- `查看批次 BATCH_xxx 的状态`
- `暂停批次 BATCH_xxx`
- `恢复批次 BATCH_xxx`
- `取消批次 BATCH_xxx`

接 real mode 时下一步补：

1. 为每张图片创建 task。
2. Worker 拉取 queued task。
3. 调 ComfyUI `/prompt`。
4. 使用 `workflow_patch.py` 替换 LoadImage。
5. 使用 `output_resolver.py` 定位输出。
6. 写 task 状态和失败重试。

