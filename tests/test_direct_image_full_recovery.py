from __future__ import annotations

import zipfile
import json
from pathlib import Path

from PIL import Image

from assetclaw_matting.config import settings
from assetclaw_matting.skills import comfyui_skills, direct_image_skills, direct_video_skills
from assetclaw_matting.services import character_resolution


def _png(path: Path, size: tuple[int, int] = (64, 64)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, (20, 40, 60, 255)).save(path)
    return path


def _run(tmp_path: Path, source: Path, target: Path) -> dict:
    return {
        "id": "IMG_RECOVERY",
        "status": "RUNNING",
        "stage": "matting",
        "images": [
            {
                "index": 1,
                "item_id": "IMG_RECOVERY:image-item:0001",
                "source_path": str(source),
                "source_name": source.name,
                "source_size_bytes": source.stat().st_size if source.is_file() else 0,
                "original_path": str(target),
                "width": 64,
                "height": 64,
            }
        ],
        "children": {},
        "log": [],
        "worker_pid": 0,
    }


def test_missing_original_restores_from_persistent_source(monkeypatch, tmp_path: Path) -> None:
    source = _png(tmp_path / "imports" / "frame.png")
    target = tmp_path / "runs" / "IMG_RECOVERY" / "original_images" / "image_01" / "frame.png"
    run = _run(tmp_path, source, target)
    monkeypatch.setattr(direct_image_skills, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(direct_image_skills, "_save", lambda *_args, **_kwargs: True)

    actions = direct_image_skills._ensure_original_images(run)

    assert target.read_bytes() == source.read_bytes()
    assert actions[0]["method"] == "source_path"
    assert actions[0]["restored_path"] == str(target)
    assert actions[0]["sha256"]


def test_missing_original_restores_from_feishu_zip_cache(monkeypatch, tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    inbox = storage / "feishu_inbox" / "20260729" / "conversation"
    source = _png(tmp_path / "source" / "0006.png")
    archive = inbox / "valentina_third_wink.zip"
    archive.parent.mkdir(parents=True)
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.write(source, "valentina_third_wink/0006.png")
    source.unlink()
    target = tmp_path / "runs" / "IMG_RECOVERY" / "original_images" / "image_01" / "0006.png"
    run = _run(tmp_path, source, target)
    run["run_label"] = archive.name
    run["images"][0]["source_name"] = "0006.png"
    monkeypatch.setattr(settings, "storage_dir", storage)
    monkeypatch.setattr(direct_image_skills, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(direct_image_skills, "_save", lambda *_args, **_kwargs: True)

    actions = direct_image_skills._ensure_original_images(run)

    assert target.is_file()
    assert actions[0]["method"] == "feishu_zip_cache"
    assert actions[0]["source"] == str(archive)
    assert actions[0]["archive_member"] == "valentina_third_wink/0006.png"


def test_full_pipeline_retry_is_bounded_and_audited(monkeypatch, tmp_path: Path) -> None:
    source = _png(tmp_path / "imports" / "frame.png")
    run_dir = tmp_path / "runs" / "IMG_RECOVERY"
    target = _png(run_dir / "original_images" / "image_01" / "frame.png")
    run = _run(tmp_path, source, target)
    monkeypatch.setattr(direct_image_skills, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(direct_image_skills, "_save", lambda *_args, **_kwargs: True)

    assert direct_image_skills._prepare_full_pipeline_retry(run, RuntimeError("first")) is True
    assert direct_image_skills._prepare_full_pipeline_retry(run, RuntimeError("second")) is True
    assert direct_image_skills._prepare_full_pipeline_retry(run, RuntimeError("third")) is False

    recovery = run["full_pipeline_recovery"]
    assert recovery["attempt_count"] == 2
    assert recovery["max_attempts"] == 2
    assert recovery["exhausted"] is True
    assert [entry["error"] for entry in recovery["attempts"]] == ["first", "second"]


def test_full_pipeline_retry_skips_deterministic_missing_node_error() -> None:
    run = {"status": "FAILED", "stage": "matting"}
    error = RuntimeError(
        "ComfyUI workflow/runtime incompatibility; retry disabled: missing_node_type: Node 'Int' not found"
    )

    assert direct_image_skills._prepare_full_pipeline_retry(run, error) is False
    assert run["full_pipeline_recovery"]["retry_disabled_reason"] == "deterministic_workflow_error"


def test_unknown_image_character_waits_for_confirmation(monkeypatch) -> None:
    run = {"id": "IMG_UNKNOWN", "images": [{"item_id": "image:1"}], "log": []}
    calls: list[tuple[str, bool, bool]] = []
    waiting: list[tuple[str, str]] = []
    monkeypatch.setattr(
        character_resolution,
        "bind_run_items",
        lambda *_args, **_kwargs: {"ready": False, "missing": ["image:1"], "missing_profiles": []},
    )
    monkeypatch.setattr(direct_image_skills, "_save", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        character_resolution,
        "mark_run_waiting",
        lambda kind, run_id: waiting.append((kind, run_id)) or True,
    )
    monkeypatch.setattr(
        direct_image_skills,
        "deliver_matte_only",
        lambda run_id, resend=True, _allow_active=False, **_kwargs: (
            calls.append((run_id, resend, _allow_active)) or {"ok": True}
        ),
    )

    assert direct_image_skills._prepare_character_gate(run) is False
    assert calls == []
    assert waiting == [("direct_image", "IMG_UNKNOWN")]
    assert run["status"] == "WAITING_CHARACTER"
    assert run["character_resolution"]["pending"] == 1


def test_selected_video_character_without_required_profile_is_delivered_as_matte_only(monkeypatch) -> None:
    run = {"id": "VID_UNKNOWN", "videos": [{"item_id": "video:1"}], "log": []}
    calls: list[tuple[str, bool, bool]] = []
    monkeypatch.setattr(
        character_resolution,
        "bind_run_items",
        lambda *_args, **_kwargs: {"ready": False, "missing": [], "missing_profiles": ["video:1"]},
    )
    monkeypatch.setattr(direct_video_skills, "_save", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        direct_video_skills,
        "deliver_matte_only",
        lambda run_id, resend=True, _allow_active=False, **_kwargs: (
            calls.append((run_id, resend, _allow_active)) or {"ok": True}
        ),
    )

    assert direct_video_skills._prepare_character_gate(run) is False
    assert calls == [("VID_UNKNOWN", True, True)]
    assert run.get("status") != "WAITING_CHARACTER"


def test_unknown_video_character_waits_for_confirmation(monkeypatch) -> None:
    run = {"id": "VID_WAIT_ROLE", "videos": [{"item_id": "video:1"}], "log": []}
    deliveries: list[str] = []
    waiting: list[tuple[str, str]] = []
    monkeypatch.setattr(
        character_resolution,
        "bind_run_items",
        lambda *_args, **_kwargs: {"ready": False, "missing": ["2-1.mp4"], "missing_profiles": []},
    )
    monkeypatch.setattr(direct_video_skills, "_save", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        direct_video_skills,
        "deliver_matte_only",
        lambda run_id, **_kwargs: deliveries.append(run_id) or {"ok": True},
    )
    monkeypatch.setattr(
        character_resolution,
        "mark_run_waiting",
        lambda kind, run_id: waiting.append((kind, run_id)) or True,
    )

    assert direct_video_skills._prepare_character_gate(run) is False
    assert deliveries == []
    assert waiting == [("direct_video", "VID_WAIT_ROLE")]
    assert run["status"] == "WAITING_CHARACTER"
    assert run["stage"] == "waiting_character"
    assert run["character_resolution"]["pending"] == 1


def test_video_partial_gpu_result_retries_only_missing_frames(monkeypatch, tmp_path: Path) -> None:
    run_id = "VID_PARTIAL"
    run_dir = tmp_path / "runs" / run_id
    frame_dir = run_dir / "frames" / "video_01"
    matte_dir = run_dir / "matte" / "video_01"
    for index in range(3):
        _png(frame_dir / f"{index:04d}.png")
    _png(matte_dir / "0000.png")
    _png(matte_dir / "0002.png")
    run = {
        "id": run_id,
        "status": "RUNNING",
        "stage": "matting",
        "workflow_path": "workflow.json",
        "notify_interval_seconds": 60,
        "matting_generation": 0,
        "matting_backend": "",
        "children": {},
        "integrity": {},
        "log": [],
        "videos": [{"index": 1, "frame_dir": str(frame_dir), "matte_dir": str(matte_dir)}],
    }
    starts: list[dict] = []

    def run_start(**kwargs):
        starts.append(kwargs)
        if len(starts) == 2:
            _png(matte_dir / "0001.png")
        return {"run_id": f"COMFY_{len(starts)}"}

    def run_status(child_id, include_gpu=False):
        if child_id == "COMFY_1":
            return {
                "status": "DONE_WITH_ERRORS",
                "completed": 2,
                "failed": 1,
                "error_items": [{"frame": "0001.png", "error": "COMFY_TIMEOUT"}],
            }
        return {"status": "DONE", "completed": 1, "failed": 0}

    monkeypatch.setattr(direct_video_skills, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(direct_video_skills, "_save", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(direct_video_skills, "_is_canceled", lambda _run: False)
    monkeypatch.setattr(direct_video_skills, "validate_matte_sequence", lambda *_args, **_kwargs: {"count": 3})
    monkeypatch.setattr(comfyui_skills, "run_start", run_start)
    monkeypatch.setattr(comfyui_skills, "run_status", run_status)

    direct_video_skills._run_comfyui_unlocked(run)

    assert len(starts) == 2
    assert starts[0]["skip_existing"] is False
    assert starts[1]["skip_existing"] is True
    assert starts[1]["backend"] == "gpu_control"
    assert run["partial_matting_repairs"][0]["completed_preserved"] == 2
    assert run["partial_matting_repairs"][0]["failed"] == 1
    assert run["integrity"]["matte"]["video_01"]["count"] == 3


def test_image_partial_gpu_result_retries_only_missing_images(monkeypatch, tmp_path: Path) -> None:
    run_id = "IMG_PARTIAL"
    run_dir = tmp_path / "runs" / run_id
    for index in range(3):
        _png(run_dir / "original_images" / f"{index:04d}.png")
    _png(run_dir / "matte" / "0000.png")
    _png(run_dir / "matte" / "0002.png")
    run = {
        "id": run_id,
        "status": "RUNNING",
        "stage": "matting",
        "workflow_path": "workflow.json",
        "notify_interval_seconds": 60,
        "matting_generation": 0,
        "matting_backend": "",
        "children": {},
        "log": [],
        "images": [{"index": index} for index in range(3)],
    }
    starts: list[dict] = []

    def run_start(**kwargs):
        starts.append(kwargs)
        if len(starts) == 2:
            _png(run_dir / "matte" / "0001.png")
        return {"run_id": f"COMFY_IMG_{len(starts)}"}

    def run_status(child_id, include_gpu=False):
        if child_id == "COMFY_IMG_1":
            return {
                "status": "DONE_WITH_ERRORS",
                "completed": 2,
                "failed": 1,
                "error_items": [{"ordinal": 1, "frame": "0001.png", "error_kind": "COMFY_TIMEOUT"}],
            }
        return {"status": "DONE", "completed": 1, "failed": 0}

    monkeypatch.setattr(direct_image_skills, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(direct_image_skills, "_save", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(direct_image_skills, "_is_canceled", lambda _run: False)
    monkeypatch.setattr(comfyui_skills, "run_start", run_start)
    monkeypatch.setattr(comfyui_skills, "run_status", run_status)

    direct_image_skills._run_comfyui(run)

    assert len(starts) == 2
    assert starts[0]["skip_existing"] is False
    assert starts[1]["skip_existing"] is True
    assert starts[1]["backend"] == "gpu_control"
    assert run["partial_matting_repairs"][0]["completed_preserved"] == 2
    assert run["partial_matting_repairs"][0]["failed"] == 1


def test_character_timeout_and_cancel_are_never_auto_retried() -> None:
    timeout = {
        "status": "FAILED",
        "stage": "character_confirmation_timeout",
        "error": "[WinError 5]",
    }
    canceled = {"status": "CANCELED", "stage": "canceled", "error": "[WinError 5]"}
    waiting = {"status": "WAITING_CHARACTER", "stage": "waiting_character", "error": "[WinError 5]"}

    assert direct_image_skills._should_auto_retry_failed_run(timeout) is False
    assert direct_image_skills._should_auto_retry_failed_run(canceled) is False
    assert direct_image_skills._should_auto_retry_failed_run(waiting) is False
    assert direct_image_skills._prepare_full_pipeline_retry(timeout, RuntimeError("x")) is False
    assert direct_image_skills._prepare_full_pipeline_retry(canceled, RuntimeError("x")) is False
    assert direct_image_skills._prepare_full_pipeline_retry(waiting, RuntimeError("x")) is False


def test_confirmed_delivery_converges_to_done_without_resend(monkeypatch, tmp_path: Path) -> None:
    package = tmp_path / "result.zip"
    package.write_bytes(b"zip")
    run = {
        "id": "IMG_DELIVERED",
        "status": "RUNNING",
        "stage": "send",
        "sequence_zip_path": str(package),
        "drive_file": {"message_id": "om_delivered"},
        "sent_files": [],
        "log": [],
    }
    saved: list[dict] = []
    monkeypatch.setattr(direct_image_skills, "_save", lambda value, **_kwargs: saved.append(dict(value)) or True)

    assert direct_image_skills._finish_after_confirmed_delivery(run) is True
    assert run["status"] == "DONE"
    assert run["sent_files"] == [str(package)]
    assert saved[-1]["status"] == "DONE"


def test_recoverable_failed_classifier_is_specific_and_exhaustion_safe() -> None:
    retryable = {"status": "FAILED", "stage": "send", "error": "[WinError 5] Access is denied"}
    unrelated = {"status": "FAILED", "stage": "postprocess", "error": "invalid workflow"}
    exhausted = {
        **retryable,
        "full_pipeline_recovery": {"attempt_count": direct_image_skills.MAX_FULL_PIPELINE_RETRIES},
    }

    assert direct_image_skills._should_auto_retry_failed_run(retryable) is True
    assert direct_image_skills._should_auto_retry_failed_run(unrelated) is False
    assert direct_image_skills._should_auto_retry_failed_run(exhausted) is False


def test_verified_gpu_result_resumes_parent_even_after_retry_budget_is_exhausted(monkeypatch, tmp_path: Path) -> None:
    run_id = "IMG_MANIFEST_RECOVERY"
    child_id = "COMFY_MANIFEST_RECOVERY"
    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True)
    run = {
        "id": run_id,
        "status": "FAILED",
        "stage": "recovery_exhausted",
        "error": "result manifest fields do not match GPU Control V2",
        "children": {"comfyui_run_id": child_id},
        "full_pipeline_recovery": {"attempt_count": direct_image_skills.MAX_FULL_PIPELINE_RETRIES},
        "log": [],
        "worker_pid": 0,
    }
    (run_dir / "status.json").write_text(json.dumps(run), encoding="utf-8")
    saved: list[dict] = []
    started: list[str] = []
    monkeypatch.setattr(direct_image_skills, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(direct_image_skills, "_comfyui_child_status", lambda _run_id: {"status": "DONE"})
    monkeypatch.setattr(
        direct_image_skills,
        "_save",
        lambda value, **_kwargs: saved.append(dict(value)) or True,
    )
    monkeypatch.setattr(
        direct_image_skills,
        "_start_recovery_worker",
        lambda recovered_id: started.append(recovered_id) or True,
    )

    result = direct_image_skills.recover_incomplete_runs()

    assert result["closed"] == [run_id]
    assert started == [run_id]
    assert saved[-1]["status"] == "QUEUED"
    assert saved[-1]["recovery_from_stage"] == "matting"
    assert saved[-1]["error"] == ""


def test_image_preparing_run_is_recovered_after_service_restart(monkeypatch, tmp_path: Path) -> None:
    run_id = "IMG_PREPARING_RECOVERY"
    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "status.json").write_text(
        json.dumps(
            {
                "id": run_id,
                "status": "PREPARING",
                "stage": "pipeline_preflight",
                "children": {},
                "log": [],
                "worker_pid": 0,
            }
        ),
        encoding="utf-8",
    )
    saved: list[dict] = []
    started: list[str] = []
    monkeypatch.setattr(direct_image_skills, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(direct_image_skills, "_save", lambda value, **_kwargs: saved.append(dict(value)) or True)
    monkeypatch.setattr(
        direct_image_skills,
        "_start_recovery_worker",
        lambda recovered_id: started.append(recovered_id) or True,
    )

    result = direct_image_skills.recover_incomplete_runs()

    assert result["closed"] == [run_id]
    assert started == [run_id]
    assert saved[-1]["status"] == "QUEUED"
    assert saved[-1]["stage"] == "recovery_queued"
    assert saved[-1]["recovery_from_stage"] == "pipeline_preflight"


def test_video_preparing_run_is_recovered_after_service_restart(monkeypatch, tmp_path: Path) -> None:
    run_id = "VID_PREPARING_RECOVERY"
    run_dir = tmp_path / "video-runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "status.json").write_text(
        json.dumps(
            {
                "id": run_id,
                "status": "PREPARING",
                "stage": "pipeline_preflight",
                "children": {},
                "log": [],
                "worker_pid": 0,
            }
        ),
        encoding="utf-8",
    )
    saved: list[dict] = []
    started: list[tuple[str, bool]] = []
    monkeypatch.setattr(direct_video_skills, "RUNS_ROOT", tmp_path / "video-runs")
    monkeypatch.setattr(direct_video_skills, "_save", lambda value, **_kwargs: saved.append(dict(value)) or True)
    monkeypatch.setattr(
        direct_video_skills,
        "_start_worker",
        lambda recovered_id, recover=False: started.append((recovered_id, recover)) or True,
    )

    result = direct_video_skills.recover_incomplete_runs()

    assert result["recovered"] == [run_id]
    assert started == [(run_id, True)]
    assert saved[-1]["status"] == "QUEUED"
    assert saved[-1]["stage"] == "recovery_queued"
    assert saved[-1]["recovery_from_stage"] == "pipeline_preflight"


def test_video_manifest_recovery_reopens_role_gate_and_refreshes_child_snapshot(monkeypatch, tmp_path: Path) -> None:
    run_id = "VID_MANIFEST_RECOVERY"
    child_id = "COMFY_VIDEO_RECOVERY"
    run_dir = tmp_path / "video-runs" / run_id
    run_dir.mkdir(parents=True)
    run = {
        "id": run_id,
        "status": "FAILED",
        "stage": "matting",
        "error": "result manifest fields do not match GPU Control V2",
        "children": {"comfyui_run_id": child_id, "comfyui": {"status": "FAILED"}},
        "log": [],
        "worker_pid": 0,
    }
    (run_dir / "status.json").write_text(json.dumps(run), encoding="utf-8")
    saved: list[dict] = []
    started: list[str] = []
    reopened: list[tuple[str, str]] = []
    child = {"status": "DONE", "completed": 121, "total": 121, "last_error": ""}
    monkeypatch.setattr(direct_video_skills, "RUNS_ROOT", tmp_path / "video-runs")
    monkeypatch.setattr(direct_video_skills, "_comfyui_child_status", lambda _run_id: child)
    monkeypatch.setattr(direct_video_skills, "_save", lambda value, **_kwargs: saved.append(dict(value)) or True)
    monkeypatch.setattr(direct_video_skills, "_start_worker", lambda recovered_id, recover=False: started.append(recovered_id) or True)
    monkeypatch.setattr(
        character_resolution,
        "reopen_failed_run_resolutions",
        lambda kind, recovered_id: reopened.append((kind, recovered_id)) or 1,
    )

    result = direct_video_skills.recover_incomplete_runs()

    assert result["recovered"] == [run_id]
    assert started == [run_id]
    assert reopened == [("direct_video", run_id)]
    assert saved[-1]["status"] == "QUEUED"
    assert saved[-1]["children"]["comfyui"] == child
    assert saved[-1]["error"] == ""


def test_worker_retries_before_any_terminal_failure_notification(monkeypatch) -> None:
    run = {
        "id": "IMG_WORKER_RETRY",
        "status": "RUNNING",
        "stage": "matting",
        "images": [],
        "log": [],
    }
    calls = 0
    prepared: list[str] = []
    notifications: list[str] = []

    def worker_once(_run_id: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient pipeline error")
        run["status"] = "DONE"

    monkeypatch.setattr(direct_image_skills, "_worker_once", worker_once)
    monkeypatch.setattr(direct_image_skills, "_load", lambda _run_id: run)
    monkeypatch.setattr(direct_image_skills, "_finish_after_confirmed_delivery", lambda _run: False)
    monkeypatch.setattr(
        direct_image_skills,
        "_prepare_full_pipeline_retry",
        lambda _run, error: prepared.append(str(error)) or True,
    )
    monkeypatch.setattr(direct_image_skills, "_notify", lambda _run, text: notifications.append(text))

    direct_image_skills._worker(run["id"])

    assert calls == 2
    assert prepared == ["transient pipeline error"]
    assert notifications == []
    assert run["status"] == "DONE"
