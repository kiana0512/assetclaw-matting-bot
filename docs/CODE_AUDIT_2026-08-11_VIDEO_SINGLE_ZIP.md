# 2026-08-11 视频单 ZIP 交付修复与性能审计

## 结论

视频直传任务的最终交付合同已统一为：**一个视频任务只发送一个完整 ZIP**。ZIP 内同时保存原视频、原始抽帧、透明抠图、Cherry 后处理结果和任务 manifest，不再把三个阶段拆成三个附件。

本次修复遵循“稳定性、质量、速度”的优先级。视频与序列帧补充修复后的完整回归为 `482 passed`；未发现本次改动引入的失败。

## 根因

完整打包函数 `_make_zip` 原本已经生成包含 `original_videos/`、`frames/`、`matte/`、`smooth/` 的总包，但交付函数 `_video_delivery_specs` 又把三个阶段分别打成 ZIP 并逐个发送，导致用户看到一个视频返回三个压缩包。

matte-only 路径也依赖分阶段交付：本地归档只含 `matte/`，发送阶段再额外生成抽帧 ZIP。该设计使“本地完整归档”和“用户实际收到内容”不一致，增加重试、恢复和人工排查复杂度。

审计还发现小 MP4 使用 `file_type=mp4` 上传后仍以 `msg_type=file` 发送，会被飞书拒绝为文件类型与消息类型不匹配。

## 已实施修复

### 1. 单一交付物

- `_video_delivery_specs` 只返回一个 `complete_bundle`。
- 正常任务只发送 `<视频名>_animation_processed.zip`。
- 重试和 `resend_zip` 只针对同一个完整包，不再逐阶段补发。
- 用户通知改为“1 个 ZIP，包内包含三个阶段”。

正常包结构：

```text
manifest.json
original_videos/
frames/
matte/
smooth/
```

### 2. matte-only 一致性

- matte-only 仍只发送一个 ZIP。
- 包内保留 `original_videos/`、`frames/`、`matte/`。
- `smooth/README.txt` 明确记录后处理未生成及原因。
- 不复制或伪造后处理帧。

### 3. 原子打包与完整性

- 先写入 `.zip.part`。
- 完成后执行 ZIP CRC 校验。
- 校验通过才用 `os.replace` 原子替换正式结果包。
- 中断或失败时清理 `.part`，保留已有正式 ZIP，不让半包进入发送阶段。
- 启用 ZIP64，支持大视频结果包。

### 4. 打包性能

PNG、JPEG、WebP、GIF 和常见视频格式本身已压缩，重复 DEFLATE 会消耗 CPU，但通常只减少少量体积。现在这些文件使用 `ZIP_STORED`，manifest 和 README 仍使用 `ZIP_DEFLATED`。

使用真实任务 `VID_B42753F81177` 的 40 张 PNG 和 1 个 MP4 做本机抽样：

| 模式 | 时间 | 大小 |
|---|---:|---:|
| 全部 DEFLATE | 1.272 秒 | 57,475,241 bytes |
| 媒体 STORED | 0.081 秒 | 58,918,418 bytes |

抽样打包速度提升约 `15.8x`，包体增加约 `1.38 MiB`。文件内容和图像质量不变。实际收益随帧数、磁盘和素材压缩率变化。

### 5. 飞书发送稳定性

- 小 MP4 现在使用 `msg_type=media`；Opus 使用 `msg_type=audio`；普通文件继续使用 `msg_type=file`。
- 大文件继续走飞书云盘分片上传。
- 每个分片读取长度必须符合预期，结束前校验累计上传字节数；源文件中途变化或提前结束会明确失败，不会提交不完整上传。
- 最终状态仍要求真实消息回执，缺少 `message_id` 不标记为已交付。

### 6. 序列帧 ZIP 与视频任务对齐

- 单张图片继续按图片结果交付；序列帧 ZIP 在业务上按视频类任务处理。
- 序列帧任务只发送一个完整包，不再分别发送 `_01_matte.zip` 和 `_02_postprocessed.zip`。
- 完整包固定包含 `01_original_frames/`、`02_matte/`、`03_postprocessed/` 和 `manifest.json`。
- 三阶段帧数、ZIP 条目数和 CRC 均通过后才允许发送。
- matte-only 序列仍保留原始帧与抠图帧，并在 `03_postprocessed/README.txt` 说明后处理缺失原因。

## 验证记录

- 定向视频交付与飞书兼容测试：`6 passed`。
- `tests/test_multimodal_feishu.py`：`77 passed`。
- 全仓测试：`482 passed`，`14 warnings`。
- ZIP CRC、目录结构、媒体 `ZIP_STORED`、manifest `ZIP_DEFLATED`、`.part` 清理均有回归断言。
- Ruff `F` 类阻断性检查通过；全规则扫描仍暴露历史行宽、import 排序与宽泛异常捕获债务，不在本次稳定性修复中批量改写，避免扩大热修范围。

## 已知低优先级债务

1. FastAPI/Starlette 测试层存在 `httpx` 迁移弃用提示，应在依赖升级窗口处理。
2. `Pillow` 的 `Image.getdata()` 在 Pillow 14 前需要迁移到新接口。
3. 历史代码存在宽泛 `except Exception` 和 import 排序告警。批量调整异常边界可能改变恢复语义，应按模块逐步处理，不应与生产交付热修混合。
4. 安全路径规则会拒绝默认名称含 `pytest` 的临时目录；全仓验证应使用 `--basetemp tmp/test-audit`。

## 上线与回滚

新任务在运行进程加载新代码后生效。上线前应确认没有处于抽帧、抠图、Cherry 或发送阶段的活动视频任务，再重启对应 Gateway/worker。回滚只需恢复本次代码提交；已有任务目录和旧 ZIP 不需要删除。
