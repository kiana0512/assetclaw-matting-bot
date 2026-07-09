# Batch Matting SOP

后续 SOP：

1. 确认 `input_dir` 和 `output_dir`。
2. 调 `matting.batch_create`。
3. 调 `matting.batch_start`。
4. 用 `matting.batch_status` 跟踪。
5. 需要时 pause、resume、cancel。
6. fake mode 先验证队列，real mode 再接 ComfyUI。
