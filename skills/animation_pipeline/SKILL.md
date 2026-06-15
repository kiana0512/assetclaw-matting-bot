# Animation Pipeline Skill

Use this skill when the user wants to run, inspect, or resume the complete animation automation flow.

## Current Flow

The production flow is now one 6-stage chain:

1. Feishu document/table video download.
2. Frame extraction. PNG frames must be zero-based: `0000.png`, `0001.png`, ...
3. ComfyUI matting. The current matting workflow already includes the required sharpening, resize, temporal handling, and other post-processing.
4. Build `unity_ready`.
5. Import into Unity by calling the AnimTextureImporter API/MCP. This has two modes:
   - `import`: new batch import, matching the red "执行批量导入" button.
   - `iteration`: resource iteration/replacement, matching the green "执行资源迭代（直接替换原贴图）" button.
   Do not click the Unity UI and do not modify plugin source.
6. P4 switch/get-latest/check/preview/create changelist/reconcile/shelve/report. Submit is disabled.

Cherry remains available as a standalone manual tool, but it is no longer part of the production `animation_flow`.

The old 3-step pipeline entry is removed from Agent, Feishu, and WebUI production routing. Use `animation_flow.start` for user requests like "开始动画自动化流程".

## Skills

Whole flow:

- `animation_flow.preview`
- `animation_flow.start`
- `animation_flow.status`
- `animation_flow.list`
- `animation_flow.cancel`

Standalone stages:

- Step 1/2: `frame.run_preview`, `frame.run_start`, `frame.run_status`, `frame.run_list`, `frame.run_cancel`
- Step 3: `comfyui.run_preview`, `comfyui.run_start`, `comfyui.run_status`, `comfyui.run_list`
- Step 4: `unity_ready.preview`, `unity_ready.build`, `unity_ready.status`
- Step 5: `unity_import.preview`, `unity_import.run`, `unity_import.status`
- Step 6: `p4.switch_stream`, `p4.get_latest`, `p4.check`, `p4.preview`, `p4.create_cl`, `p4.reconcile`, `p4.shelve`, `p4.report`

## Defaults

- Date root: `E:/animation_automation/YYYY-MM-DD`
- Feishu progress policy: skip only `已完成` and `不处理`; all other statuses, including `待抽帧`, `抽帧中`, `K帧中`, `待整理`, `整理中`, and `待提交`, are downloaded and extracted again.
- Unity project: `D:/Spark/Client`
- Unity import mode: `import` by default; use `iteration` when the user says 迭代 / 替换 / 高清化.
- P4 stream: `//streams/rel_0.0.1`
- Unity package: `both`
- P4 submit: disabled forever

## Feishu Phrases

- `开始动画自动化流程`
- `动画自动化20260610 迭代`
- `动画自动化 2026-06-10 导入`
- `完整动画流程进度`

## Feishu Progress Logs

During a full run, send progress logs back to the same Feishu conversation:

- Step 1/2: processed records, downloaded video attachment count, skipped record count and skip reasons.
- Step 3: matting route input/output, completed count, failed count and status.
- Step 4: `unity_ready` scene/emoji task counts, frame counts and warnings.
- Step 5: Unity import mode and import/replace/skip counts.
- Step 6: P4 stream, get latest, check, preview counts, CL ID, reconcile counts and shelve result.

## Unity Ready Shape

```text
E:/animation_automation/YYYY-MM-DD/unity_ready/
  manifest.json
  scene/animation_resource_manifest.json
  scene/frames/
  emoji/animation_resource_manifest.json
  emoji/frames/
```

`temporal_smooth` is no longer used by the production flow. `unity_ready` is split only by `scene / emoji`.
