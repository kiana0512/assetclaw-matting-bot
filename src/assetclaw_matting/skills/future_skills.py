"""Future skill placeholders.

These are registered in the manifest so ArkClaw / Claude / LLM Proxy
know what's coming. All return 'not_implemented' when called.

Planned skill tracks:
  Video / Frame:  video.download_or_import, video.extract_frames, frames.*
  Matting:        matting.batch (current: batch.create)
  Post-process:   noise.cleanup, image.package_review
  3D/Animation:   model3d.generate, texture.apply, animation.*
  Engine:         asset.import_engine, p4.submit, resource.cleanup
  QA:             qa.review_effects
  Workflow:       workflow.run
"""
from __future__ import annotations

from typing import Any


def _nyi(skill: str) -> dict[str, Any]:
    return {
        "status": "not_implemented",
        "skill": skill,
        "message": f"Skill '{skill}' is planned but not yet implemented.",
    }


# ── Video / Frame ─────────────────────────────────────────────────────────────

def video_download_or_import(**kw: Any) -> dict[str, Any]:
    return _nyi("video.download_or_import")


def video_extract_frames(**kw: Any) -> dict[str, Any]:
    return _nyi("video.extract_frames")


def frames_rename_from_table(**kw: Any) -> dict[str, Any]:
    return _nyi("frames.rename_from_table")


def frames_delete_bad_frames(**kw: Any) -> dict[str, Any]:
    return _nyi("frames.delete_bad_frames")


def frames_dedupe_similar(**kw: Any) -> dict[str, Any]:
    return _nyi("frames.dedupe_similar")


# ── Image post-process ────────────────────────────────────────────────────────

def noise_cleanup(**kw: Any) -> dict[str, Any]:
    return _nyi("noise.cleanup")


def image_package_review(**kw: Any) -> dict[str, Any]:
    return _nyi("image.package_review")


# ── 3D / Animation ────────────────────────────────────────────────────────────

def model3d_generate(**kw: Any) -> dict[str, Any]:
    return _nyi("model3d.generate")


def texture_apply(**kw: Any) -> dict[str, Any]:
    return _nyi("texture.apply")


def animation_state_machine_create(**kw: Any) -> dict[str, Any]:
    return _nyi("animation.state_machine.create")


def animation_kframe_create(**kw: Any) -> dict[str, Any]:
    return _nyi("animation.kframe.create")


def qa_review_effects(**kw: Any) -> dict[str, Any]:
    return _nyi("qa.review_effects")


# ── Engine / Pipeline ─────────────────────────────────────────────────────────

def asset_import_engine(**kw: Any) -> dict[str, Any]:
    return _nyi("asset.import_engine")


def resource_cleanup(**kw: Any) -> dict[str, Any]:
    return _nyi("resource.cleanup")


def p4_submit(**kw: Any) -> dict[str, Any]:
    return _nyi("p4.submit")


# ── Workflow ──────────────────────────────────────────────────────────────────

def workflow_run(**kw: Any) -> dict[str, Any]:
    return _nyi("workflow.run")


# ── Generic placeholder ───────────────────────────────────────────────────────

def frame_extract(**kw: Any) -> dict[str, Any]:
    return _nyi("frame.extract")


def future_placeholder(name: str = "", **kw: Any) -> dict[str, Any]:
    return _nyi(name or "future.placeholder")
