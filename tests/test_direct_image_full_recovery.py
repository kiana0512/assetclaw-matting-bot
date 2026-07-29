from __future__ import annotations

import zipfile
from pathlib import Path

from PIL import Image

from assetclaw_matting.config import settings
from assetclaw_matting.skills import direct_image_skills


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
