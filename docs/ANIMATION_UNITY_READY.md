# 动画自动化 7 步流程

本项目不修改 Unity 插件源码。Unity 导入阶段只调用现有 AnimTextureImporter API/MCP；不点击 UI，不改 `AnimTextureImportEditorWindow.cs` / `AnimTextureImportModule.cs`。

## 主流程

用户在飞书或 WebUI 说“开始动画自动化流程”时，后端走 `animation_flow.start`，固定执行：

1. 飞书文档/表格下载视频。
2. 抽帧，序列从 `0000.png` 开始。
3. 抠图。
4. Cherry 后处理：按类型选择平滑和分辨率。
5. `unity_ready` 整理。
6. Unity 插件导入引擎。
7. P4 同步、创建 CL、reconcile、shelve、report，并把 CL/Shelf ID 回给飞书。

第 7 步只允许 shelve-only。`submit` 是绝对红线。

## 单步入口

每一步都可以单独运行：

| 步骤 | Skill |
| --- | --- |
| 1/2 飞书下载 + 抽帧 | `frame.run_preview` / `frame.run_start` / `frame.run_status` |
| 3 抠图 | `comfyui.run_preview` / `comfyui.run_start` / `comfyui.run_status` |
| 4 Cherry | `cherry.run_preview` / `cherry.run_start` / `cherry.run_status` |
| 5 unity_ready | `unity_ready.preview` / `unity_ready.build` / `unity_ready.status` |
| 6 Unity 导入 | `unity_import.preview` / `unity_import.run` / `unity_import.status` |
| 7 P4 Shelve-only | `p4.switch_stream` / `p4.get_latest` / `p4.check` / `p4.preview` / `p4.create_cl` / `p4.reconcile` / `p4.shelve` / `p4.report` |

旧 `pipeline.run_start` 只覆盖抽帧、抠图、Cherry 三步，属于 legacy 入口，不再作为主流程。

## 目录结构

```text
E:/animation_automation/2026-06-09/
  source_manifest.json
  scene/default/{videos,frames,matte,smooth}
  scene/temporal_smooth/{videos,frames,matte,smooth}
  emoji/default/{videos,frames,matte,smooth}
  emoji/temporal_smooth/{videos,frames,matte,smooth}
  unity_ready/
    manifest.json
    scene/animation_resource_manifest.json
    scene/frames/
    emoji/animation_resource_manifest.json
    emoji/frames/
```

`default` 和 `temporal_smooth` 只是前处理来源，不是 Unity 导入分类。`unity_ready` 只拆成 `scene` 和 `emoji` 两包。

## P4 默认

- Unity project: `D:/Spark/Client`
- P4 stream: `//streams/rel_0.0.1`
- P4 server/client 由本机配置读取。
- P4 target paths 来自 `unity_ready/{scene,emoji}/animation_resource_manifest.json`，只 reconcile Unity 导入产生的目标资源目录。
- 最终飞书消息必须包含 CL/Shelf ID 和 `Submit: disabled`。

禁止：

- `submit`
- `merge / copy / integrate`
- 创建 stream
- 保存密码、token、ticket

## 常用命令

完整 7 步通过飞书/WebUI 调用 `animation_flow.preview` 和 `animation_flow.start`。

单独生成 `unity_ready`：

```bash
python -m tools.animation_automation.cli build-unity-ready --date-root "E:/animation_automation/2026-06-09" --overwrite
```

单独预览 Unity 导入：

```bash
python -m tools.animation_automation.cli import-unity-ready --unity-ready "E:/animation_automation/2026-06-09/unity_ready" --package both
```
