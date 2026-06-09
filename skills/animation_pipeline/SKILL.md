# Animation Pipeline Skill

Use this skill when the user wants to run, inspect, or resume the complete animation automation flow.

## Current Flow

The production flow is now one 7-stage chain:

1. Feishu document/table video download.
2. Frame extraction. PNG frames must be zero-based: `0000.png`, `0001.png`, ...
3. Matting.
4. Cherry post-processing, including temporal smoothing and resolution selection.
5. Build `unity_ready`.
6. Import into Unity by calling the AnimTextureImporter API/MCP. Do not click the Unity UI and do not modify plugin source.
7. P4 switch/get-latest/check/preview/create changelist/reconcile/shelve/report. Submit is disabled.

The old `pipeline.run_start` 3-step flow is legacy-only. Prefer `animation_flow.start` for user requests like "开始动画自动化流程".

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
- Step 4: `cherry.run_preview`, `cherry.run_start`, `cherry.run_status`, `cherry.run_list`
- Step 5: `unity_ready.preview`, `unity_ready.build`, `unity_ready.status`
- Step 6: `unity_import.preview`, `unity_import.run`, `unity_import.status`
- Step 7: `p4.switch_stream`, `p4.get_latest`, `p4.check`, `p4.preview`, `p4.create_cl`, `p4.reconcile`, `p4.shelve`, `p4.report`

## Defaults

- Date root: `E:/animation_automation/YYYY-MM-DD`
- Unity project: `D:/Spark/Client`
- P4 stream: `//streams/rel_0.0.1`
- Unity package: `both`
- P4 submit: disabled forever

## Unity Ready Shape

```text
E:/animation_automation/YYYY-MM-DD/unity_ready/
  manifest.json
  scene/animation_resource_manifest.json
  scene/frames/
  emoji/animation_resource_manifest.json
  emoji/frames/
```

`temporal_smooth` only affects Cherry behavior. `unity_ready` is split only by `scene / emoji`.
