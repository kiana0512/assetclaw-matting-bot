# Animation Unity Ready Skill

## 什么时候使用

当用户需要以下任一任务时使用本 skill：

- 从飞书表格下载动画视频。
- 过滤 `进度 = 已完成 / 不处理` 的任务。
- 按 `scene / emoji` 分流动画资产。
- 按 `default / temporal_smooth` 分流处理选项。
- 生成 Unity 插件可直接读取的 `unity_ready`。

## 安全边界

- 不修改 Unity 插件 C# 代码，尤其不修改 `AnimTextureImportEditorWindow.cs` 和 `AnimTextureImportModule.cs`。
- 不执行 P4 submit。
- 不保存飞书 token、cookie、登录态或 P4 密码。
- 不覆盖已有 `unity_ready`，除非用户显式传入 `--overwrite`。
- P4 assistant 只做 shelve-only：status、check、preview、create changelist、reconcile、shelve、report。

## 常用命令

从飞书读取并下载、抽帧：

```bash
python -m assetclaw_matting.cli.main worker
```

或通过现有飞书 skill/GUI 启动 frame/pipeline 流程。下载阶段应生成：

```text
E:/animation_automation/YYYY-MM-DD/source_manifest.json
```

生成 Unity Ready：

```bash
python -m tools.animation_automation.cli build-unity-ready --date-root "E:/animation_automation/YYYY-MM-DD"
```

覆盖重建：

```bash
python -m tools.animation_automation.cli build-unity-ready --date-root "E:/animation_automation/YYYY-MM-DD" --overwrite
```

P4 report 追加 Unity Ready 摘要：

```bash
python -m tools.p4_assistant.cli report --cl 123456 --unity-ready-manifest "E:/animation_automation/YYYY-MM-DD/unity_ready/manifest.json"
```

## 回复模板

下载完成：

```text
飞书下载完成：
Date Root: E:/animation_automation/YYYY-MM-DD
Source Manifest: E:/animation_automation/YYYY-MM-DD/source_manifest.json
已跳过：N 条（进度 = 已完成 / 不处理）
已分流：scene/default、scene/temporal_smooth、emoji/default、emoji/temporal_smooth
```

Unity Ready 生成完成：

```text
Unity Ready 已生成：
Scene JSON: E:/animation_automation/YYYY-MM-DD/unity_ready/scene/animation_resource_manifest.json
Scene Frames: E:/animation_automation/YYYY-MM-DD/unity_ready/scene/frames
Emoji JSON: E:/animation_automation/YYYY-MM-DD/unity_ready/emoji/animation_resource_manifest.json
Emoji Frames: E:/animation_automation/YYYY-MM-DD/unity_ready/emoji/frames
下一步：在 Unity 插件中分别选择对应 JSON 和 Frames 源根目录导入。
```

重复任务阻断：

```text
同一个 unity_ready/{scene|emoji} 里出现重复任务 {角色-动画}：
- {source A}
- {source B}
请检查飞书表格是否重复，或手动确认使用哪一条。
```

缺帧 warning：

```text
Warnings:
- {角色-动画} 缺帧: 0032.png
```

