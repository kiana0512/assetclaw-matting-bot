"""Placeholder skills for future capabilities.

These are registered in the manifest so OpenClaw knows they are planned,
but return "not_implemented" when called.

Future skills:
- frame.extract:    Video frame extraction
- model3d.generate: 3D model generation from images
- texture.apply:    Texture baking / UV mapping
- workflow.run:     Run arbitrary ComfyUI workflow by name
"""
from __future__ import annotations

from typing import Any


def _not_implemented(skill_name: str) -> dict[str, Any]:
    return {
        "status": "not_implemented",
        "skill": skill_name,
        "message": f"Skill '{skill_name}' is planned but not yet implemented.",
    }


def frame_extract(**kwargs: Any) -> dict[str, Any]:
    return _not_implemented("frame.extract")


def model3d_generate(**kwargs: Any) -> dict[str, Any]:
    return _not_implemented("model3d.generate")


def texture_apply(**kwargs: Any) -> dict[str, Any]:
    return _not_implemented("texture.apply")


def workflow_run(**kwargs: Any) -> dict[str, Any]:
    return _not_implemented("workflow.run")


def future_placeholder(name: str = "", **kwargs: Any) -> dict[str, Any]:
    return _not_implemented(name or "future.placeholder")
