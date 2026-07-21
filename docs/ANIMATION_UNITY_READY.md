# 动画自动化 7 步流程

本项目不修改 Unity 插件源码。Unity 导入阶段只调用现有 AnimTextureImporter API/MCP；不点击 UI，不改 `AnimTextureImportEditorWindow.cs` / `AnimTextureImportModule.cs`。

## 主流程

用户在飞书或 WebUI 说“开始动画自动化流程”时，后端走 `animation_flow.start`，固定执行：

1. 飞书文档/表格下载视频。
2. 抽帧，序列从 `0000.png` 开始。
3. ComfyUI 抠图，使用与直接图片/视频任务相同的 ComfyUI 队列和 ImageClip 执行组件。
4. Cherry 后处理，使用默认 HTML 处理链；时序平滑默认关闭。
5. `unity_ready` 整理。
6. Unity 插件导入引擎。该步骤必须显式支持两种模式：
   - `import`：执行批量导入，新建/导入到 importing 或 charImproting 目录。
   - `iteration`：执行资源迭代，直接替换已有 K 好帧贴图，例如 CharacterAnim/{角色}/Common。
7. P4 同步、创建 CL、reconcile、shelve、report，并把 CL/Shelf ID 回给飞书。

第 7 步只允许 shelve-only。`submit` 是绝对红线。WebUI 默认关闭 P4；关闭时不会创建 changelist，可按当前流程的 P4 恢复机制另行继续。

飞书表格只处理「进度」精确等于 `待抽帧` 且包含动画附件的记录。`已完成`、空状态、`K帧中`、`不处理` 等其他状态都不会下载或抽帧。

抽帧 FPS 可由 WebUI 自由填写任意正整数，默认 `24`；该值会传入抽帧子任务，不修改视频原文件。

飞书短指令也会进入同一个 7 步入口，例如：

```text
动画自动化20260610 迭代
动画自动化 2026-06-10 导入
完整动画流程进度
```

`20260610` 会解析为 `<animation-root>/2026-06-10`。启动类指令仍然走确认码，不会无确认直接执行。

## 飞书运行日志

完整流程运行时会在同一个飞书对话里持续推送关键日志：

- 步骤 1/2：下载了多少视频附件、处理了多少记录、跳过了多少记录，以及跳过原因。
- 步骤 3：每个 scene/emoji route 的抠图输入/输出、完成数量、失败数量。
- 步骤 4：Cherry 后处理输入、输出、完成数和失败数。
- 步骤 5：`unity_ready` 的 scene/emoji 任务数、图片数、warnings。
- 步骤 6：Unity 是新导入还是资源迭代；迭代模式会报告替换贴图数和跳过贴图数。
- 步骤 7：P4 stream、get latest、check、preview、CL、reconcile、shelve 的阶段结果。

最终消息必须包含 CL/Shelf ID，并明确 `Submit: disabled`。

## 单步入口

每一步都可以单独运行：

| 步骤 | Skill |
| --- | --- |
| 1/2 飞书下载 + 抽帧 | `frame.run_preview` / `frame.run_start` / `frame.run_status` |
| 3 抠图 | `comfyui.run_preview` / `comfyui.run_start` / `comfyui.run_status` |
| 4 Cherry 后处理 | `cherry.run_preview` / `cherry.run_start` / `cherry.run_status` |
| 5 unity_ready | `unity_ready.preview` / `unity_ready.build` / `unity_ready.status` |
| 6 Unity 导入 | `unity_import.preview` / `unity_import.run` / `unity_import.status` |
| 7 P4 Shelve-only | `p4.switch_stream` / `p4.get_latest` / `p4.check` / `p4.preview` / `p4.create_cl` / `p4.reconcile` / `p4.shelve` / `p4.report` |

历史 pipeline 入口已从 Agent / 飞书 / WebUI 的生产入口中移除。`cherry.*` 仍可单独调用或用于第 4 步补跑。

## 目录结构

```text
<animation-root>/2026-06-09/
  source_manifest.json
  scene/{videos,frames,matte,smooth}
  emoji/{videos,frames,matte,smooth}
  unity_ready/
    manifest.json
    scene/animation_resource_manifest.json
    scene/frames/
    emoji/animation_resource_manifest.json
    emoji/frames/
```

不再按 `default / temporal_smooth` 分流。`unity_ready` 只拆成 `scene` 和 `emoji` 两包，图片来源为 Cherry 后处理后的 `smooth` 目录。

## P4 默认

- Unity project: `<unity-project>`（默认自动发现，也可用 `UNITY_PROJECT_DIR` 覆盖）
- Unity mode: 默认 `import`；飞书里出现“迭代 / 替换 / 高清化 / 资源迭代”时用 `iteration`。
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
python -m tools.animation_automation.cli build-unity-ready --date-root "<animation-root>/2026-06-09" --overwrite
```

单独预览 Unity 导入：

```bash
python -m tools.animation_automation.cli import-unity-ready --unity-ready "<animation-root>/2026-06-09/unity_ready" --package both
```
