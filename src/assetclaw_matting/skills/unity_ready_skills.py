from __future__ import annotations

from pathlib import Path
from typing import Any

from assetclaw_matting.skills.frame_skills import default_automation_paths
from assetclaw_matting.skills.security import validate_path
from tools.animation_automation.core import build_unity_ready


def _date_root(date_root: str | None = None) -> Path:
    if date_root:
        return validate_path(date_root, must_exist=False)
    return validate_path(default_automation_paths()["workspace_root"], must_exist=False)


def preview(date_root: str | None = None, copy_mode: str = "copy", **_: Any) -> dict[str, Any]:
    root = _date_root(date_root)
    unity_ready = root / "unity_ready"
    return {
        "ok": True,
        "date_root": str(root),
        "unity_ready": str(unity_ready),
        "copy_mode": copy_mode,
        "exists": unity_ready.exists(),
        "packages": {
            "scene": _package_paths(unity_ready / "scene"),
            "emoji": _package_paths(unity_ready / "emoji"),
        },
    }


def build(
    date_root: str | None = None,
    overwrite: bool = True,
    copy_mode: str = "copy",
    **_: Any,
) -> dict[str, Any]:
    root = _date_root(date_root)
    report = build_unity_ready(root, overwrite=bool(overwrite), copy_mode=copy_mode)
    return {"ok": True, "date_root": str(root), "unity_ready": str(root / "unity_ready"), "report": report}


def status(date_root: str | None = None, **_: Any) -> dict[str, Any]:
    root = _date_root(date_root)
    unity_ready = root / "unity_ready"
    packages = {
        "scene": _package_paths(unity_ready / "scene"),
        "emoji": _package_paths(unity_ready / "emoji"),
    }
    return {
        "ok": True,
        "date_root": str(root),
        "unity_ready": str(unity_ready),
        "exists": unity_ready.exists(),
        "manifest": str(unity_ready / "manifest.json"),
        "manifest_exists": (unity_ready / "manifest.json").is_file(),
        "packages": packages,
    }


def _package_paths(root: Path) -> dict[str, Any]:
    frames = root / "frames"
    manifest = root / "animation_resource_manifest.json"
    frame_count = sum(1 for _ in frames.rglob("*.png")) if frames.is_dir() else 0
    return {
        "root": str(root),
        "json": str(manifest),
        "json_exists": manifest.is_file(),
        "frames": str(frames),
        "frames_exists": frames.is_dir(),
        "frame_count": frame_count,
    }
