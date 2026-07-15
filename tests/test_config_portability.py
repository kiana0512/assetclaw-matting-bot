from __future__ import annotations

from pathlib import Path

from assetclaw_matting.config import Settings


def test_settings_derive_sibling_runtime_layout(tmp_path: Path) -> None:
    project = tmp_path / "assetclaw-matting-bot"
    project.mkdir()
    aki = tmp_path / "20250904"
    (aki / "ComfyUI").mkdir(parents=True)
    (aki / "python").mkdir()
    unity = tmp_path / "project"
    (unity / "Assets").mkdir(parents=True)
    (unity / "ProjectSettings").mkdir()

    configured = Settings(assetclaw_root=project)

    assert configured.assetclaw_root == project.resolve()
    assert configured.animation_root == tmp_path / "animation_auto"
    assert configured.matting_pipeline_repo_dir == tmp_path / "imageclip"
    assert configured.cherry_postprocess_html_path == tmp_path / "imageclip" / "cherry-postprocess.html"
    assert configured.comfyui_aki_root == aki
    assert configured.comfyui_dir == aki / "ComfyUI"
    assert configured.comfyui_python_dir == aki / "python"
    assert configured.comfyui_workflow_path == aki / "ComfyUI" / "user" / "default" / "workflows" / "ImageClip.json"
    assert configured.unity_project_dir == unity


def test_settings_explicit_external_paths_override_discovery(tmp_path: Path) -> None:
    project = tmp_path / "assetclaw-matting-bot"
    project.mkdir()
    custom_aki = tmp_path / "custom-aki"
    custom_pipeline = tmp_path / "custom-imageclip"
    custom_animation = tmp_path / "custom-animation"

    configured = Settings(
        assetclaw_root=project,
        animation_root=custom_animation,
        comfyui_aki_root=custom_aki,
        matting_pipeline_repo_dir=custom_pipeline,
    )

    assert configured.animation_root == custom_animation
    assert configured.comfyui_aki_root == custom_aki
    assert configured.comfyui_dir == custom_aki / "ComfyUI"
    assert configured.matting_pipeline_repo_dir == custom_pipeline
    assert configured.cherry_postprocess_html_path == custom_pipeline / "cherry-postprocess.html"
