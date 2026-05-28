# Future Skills Roadmap

Skills are registered in `skills/registry.py`. Adding a new skill is 4 steps:
1. Implement function in the relevant `skills/*.py`
2. Add entry to `SKILL_CATALOG` with `implemented: True`
3. Update this doc
4. Redeploy gateway

## Planned Skills

### frame.extract
- **Purpose**: Extract frames from video files at a specified FPS
- **Inputs**: `video_path`, `output_dir`, `fps` (default 1), `max_frames`
- **Notes**: Requires ffmpeg. Add to `file_skills.py` or new `video_skills.py`
- **Security**: output_dir must be under ALLOWED_ROOTS

### model3d.generate
- **Purpose**: Generate a 3D mesh from input images (e.g., via TripoSR or Wonder3D)
- **Inputs**: `input_dir`, `output_dir`, `model_type`
- **Notes**: High VRAM, may block ComfyUI — needs concurrency control
- **Danger**: high — requires confirmation

### texture.apply
- **Purpose**: Bake diffuse texture onto a 3D mesh
- **Inputs**: `mesh_path`, `texture_path`, `output_path`
- **Notes**: May use Blender CLI or a ComfyUI 3D extension

### workflow.run
- **Purpose**: Run any registered ComfyUI workflow by name
- **Inputs**: `workflow_name`, `input_image_path`, `output_image_path`, `params`
- **Notes**: Danger=high because arbitrary workflows can have side effects
- **Security**: workflow_name must be in a registered whitelist

## Workflow for Adding a Skill

```python
# 1. In skills/your_module.py:
def frame_extract(video_path: str, output_dir: str, fps: int = 1, max_frames: int = 100) -> dict:
    from assetclaw_matting.skills.auth import validate_skill_path
    video = validate_skill_path(video_path)
    out_dir = validate_skill_path(output_dir)
    # ... extract frames ...
    return {"extracted": N, "output_dir": str(out_dir)}

# 2. In skills/registry.py SKILL_CATALOG:
{
    "name": "frame.extract",
    "description": "Extract frames from video",
    "danger_level": "medium",
    "requires_confirmation": False,
    "implemented": True,
    "fn": video_skills.frame_extract,
},
```

## Skill Naming Convention

Use dot-namespace format: `{domain}.{action}`

- `batch.*` — batch management
- `task.*` — task management
- `worker.*` / `queue.*` — execution status
- `comfyui.*` — ComfyUI control
- `file.*` — file system (read-only metadata)
- `log.*` — log access
- `frame.*` — video/frame processing
- `model3d.*` — 3D generation
- `texture.*` — texture operations
- `workflow.*` — ComfyUI workflow management
