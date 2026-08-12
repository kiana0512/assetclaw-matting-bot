from __future__ import annotations

from pathlib import Path

from PIL import Image

from assetclaw_matting.services import task_rerun_service
from assetclaw_matting.skills import direct_image_skills, direct_video_skills
from assetclaw_matting.skills.registry import get_skill_meta


def _png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (4, 4), (12, 34, 56, 255)).save(path)


def _skip_pipeline_preflight(monkeypatch) -> None:
    monkeypatch.setattr(task_rerun_service, "_preflight_latest_workflow", lambda _run: {})


def test_full_rerun_requires_explicit_confirmation(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(direct_image_skills, "RUNS_ROOT", tmp_path / "runs")
    assert task_rerun_service.request_full_rerun("image", "IMG_MISSING")["error"] == "完整重跑需要二次确认"


def test_image_full_rerun_reuses_original_task_and_archives_failure(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(direct_image_skills, "RUNS_ROOT", tmp_path / "runs")
    _skip_pipeline_preflight(monkeypatch)
    run_dir = direct_image_skills.RUNS_ROOT / "IMG_SOURCE"
    originals = [run_dir / "original_images" / f"image_{index:02d}" / f"{index:02d}.png" for index in (1, 2)]
    for path in originals:
        _png(path)
    source = {
        "id": "IMG_SOURCE",
        "status": "FAILED",
        "stage": "recovery_exhausted",
        "error": "missing node",
        "created_at": "2026-08-12T10:00:00",
        "run_label": "sequence.zip",
        "package_as_sequence": True,
        "chat_id": "oc_source",
        "user_id": "ou_source",
        "images": [
            {
                "index": index,
                "original_path": str(path),
                "source_name": f"frame_{index:04d}.png",
                "matte_dir": str(run_dir / "matte" / f"image_{index:02d}"),
                "smooth_dir": str(run_dir / "smooth" / f"image_{index:02d}"),
                "character_group_key": "sequence:gary",
                "character_id": "gary",
            }
            for index, path in enumerate(originals, start=1)
        ],
        "children": {"comfyui_run_id": "COMFY_FAILED"},
        "sent_files": [],
        "log": [],
    }
    direct_image_skills._save(source)
    started: list[str] = []
    monkeypatch.setattr(direct_image_skills, "_start_worker", lambda run_id: started.append(run_id))

    result = task_rerun_service.request_full_rerun("image", "IMG_SOURCE", confirmed=True)
    run = direct_image_skills._load("IMG_SOURCE")

    assert result["ok"] is True
    assert result["run_id"] == "IMG_SOURCE"
    assert "child_run_id" not in result
    assert started == ["IMG_SOURCE"]
    assert run["id"] == "IMG_SOURCE"
    assert run["created_at"] == "2026-08-12T10:00:00"
    assert run["status"] == "QUEUED"
    assert run["stage"] == "full_rerun_queued"
    assert run["children"] == {}
    assert [item["source_name"] for item in run["images"]] == ["frame_0001.png", "frame_0002.png"]
    assert run["rerun"]["in_place"] is True
    assert run["rerun_history"][-1]["status"] == "FAILED"
    assert run["rerun_history"][-1]["error"] == "missing node"


def test_full_rerun_is_idempotent_while_same_task_is_active(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(direct_image_skills, "RUNS_ROOT", tmp_path / "runs")
    source = {
        "id": "IMG_SOURCE",
        "status": "RUNNING",
        "stage": "matting",
        "images": [],
        "rerun": {"request_id": "RERUN_ONE", "status": "RUNNING", "in_place": True},
        "log": [],
    }
    direct_image_skills._save(source)
    monkeypatch.setattr(direct_image_skills, "_start_worker", lambda _run_id: (_ for _ in ()).throw(AssertionError("must not start")))

    result = task_rerun_service.request_full_rerun("image", "IMG_SOURCE", confirmed=True)
    assert result["ok"] is True
    assert result["already_running"] is True
    assert result["run_id"] == "IMG_SOURCE"


def test_video_full_rerun_preserves_task_id_and_frame_parameters(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(direct_video_skills, "RUNS_ROOT", tmp_path / "runs")
    _skip_pipeline_preflight(monkeypatch)
    original = direct_video_skills.RUNS_ROOT / "VID_SOURCE" / "original_videos" / "01_clip.mp4"
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(b"video")
    source = {
        "id": "VID_SOURCE",
        "status": "DONE",
        "stage": "done",
        "run_label": "clip.mp4",
        "fps": 30,
        "max_frames": 90,
        "matting_backend": "local",
        "videos": [{
            "index": 1,
            "original_path": str(original),
            "source_name": "clip.mp4",
            "frame_dir": str(original.parents[1] / "frames" / "video_01"),
            "matte_dir": str(original.parents[1] / "matte" / "video_01"),
            "smooth_dir": str(original.parents[1] / "smooth" / "video_01"),
            "frame_count": 10,
        }],
        "children": {},
        "sent_files": [],
        "log": [],
    }
    direct_video_skills._save(source)
    started: list[str] = []
    monkeypatch.setattr(direct_video_skills, "_start_worker", lambda run_id: started.append(run_id) or True)

    result = task_rerun_service.request_full_rerun("video", "VID_SOURCE", confirmed=True)
    run = direct_video_skills._load("VID_SOURCE")

    assert result["ok"] is True
    assert result["run_id"] == "VID_SOURCE"
    assert started == ["VID_SOURCE"]
    assert run["fps"] == 30
    assert run["max_frames"] == 90
    assert run["matting_backend"] == "local"
    assert run["videos"][0]["frame_count"] == 0
    assert run["rerun_history"][-1]["status"] == "DONE"


def test_full_rerun_skills_are_registered_for_ui_confirmation() -> None:
    for name in ("direct_image.full_rerun", "direct_video.full_rerun"):
        meta = get_skill_meta(name)
        assert meta is not None
        assert meta["requires_confirmation"] is False
        assert meta["risk_level"] == "egress_caution"
