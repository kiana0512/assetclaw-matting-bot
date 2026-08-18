from __future__ import annotations

import zipfile
import threading
import time
from pathlib import Path

from PIL import Image

from assetclaw_matting.services import task_redelivery_service
from assetclaw_matting.skills import direct_image_skills, direct_video_skills
from assetclaw_matting.skills.registry import get_skill_meta


def _png(path: Path, color: tuple[int, int, int, int] = (10, 20, 30, 255)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (2, 2), color).save(path)


def test_video_full_redelivery_freshly_sends_all_four_sections(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(direct_video_skills, "RUNS_ROOT", tmp_path / "video_runs")
    run_dir = direct_video_skills.RUNS_ROOT / "VID_TEST"
    original = run_dir / "original_videos" / "01_test.mp4"
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(b"video")
    frame = run_dir / "frames" / "video_01" / "000001.png"
    matte = run_dir / "matte" / "video_01" / "000001.png"
    smooth = run_dir / "smooth" / "video_01" / "000001.png"
    for path in (frame, matte, smooth):
        _png(path)
    run = {
        "id": "VID_TEST",
        "status": "DONE",
        "stage": "done",
        "run_label": "test.mp4",
        "chat_id": "oc_test",
        "user_id": "",
        "videos": [{
            "index": 1,
            "source_name": "test.mp4",
            "original_path": str(original),
            "frame_dir": str(frame.parent),
            "matte_dir": str(matte.parent),
            "smooth_dir": str(smooth.parent),
        }],
        "redelivery": {"request_id": "REQ", "status": "QUEUED"},
        "log": [],
    }
    direct_video_skills._save(run)
    receipts: list[tuple[str, Path, str]] = []

    def fake_send(chat_id: str, path: Path, file_name: str) -> dict[str, str]:
        receipts.append((chat_id, path, file_name))
        return {"message_id": "om_new", "delivery_method": "message_attachment"}

    monkeypatch.setattr("assetclaw_matting.feishu.client.feishu_client.send_file_to_chat", fake_send)
    result = task_redelivery_service.run_redelivery("video", "VID_TEST", "REQ")
    assert result["ok"] is True
    assert receipts and receipts[0][0] == "oc_test"
    with zipfile.ZipFile(receipts[0][1]) as archive:
        names = archive.namelist()
    assert any(name.startswith("original_videos/") for name in names)
    assert any(name.startswith("frames/") for name in names)
    assert any(name.startswith("matte/") for name in names)
    assert any(name.startswith("smooth/") for name in names)
    latest = direct_video_skills._load("VID_TEST")
    assert latest["status"] == "DONE"
    assert latest["redelivery"]["status"] == "DONE"
    assert latest["redelivery"]["message_id"] == "om_new"


def test_video_redelivery_without_postprocess_still_delivers_matte_bundle(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(direct_video_skills, "RUNS_ROOT", tmp_path / "video_runs")
    run_dir = direct_video_skills.RUNS_ROOT / "VID_MATTE"
    original = run_dir / "original_videos" / "01_test.mp4"
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(b"video")
    frame = run_dir / "frames" / "video_01" / "000001.png"
    matte = run_dir / "matte" / "video_01" / "000001.png"
    _png(frame)
    _png(matte)
    run = {
        "id": "VID_MATTE",
        "status": "DONE",
        "stage": "done_matte_only",
        "run_label": "test.mp4",
        "chat_id": "oc_test",
        "user_id": "",
        "videos": [{
            "index": 1,
            "source_name": "test.mp4",
            "original_path": str(original),
            "frame_dir": str(frame.parent),
            "matte_dir": str(matte.parent),
            "smooth_dir": str(run_dir / "smooth" / "video_01"),
        }],
        "redelivery": {"request_id": "REQ", "status": "QUEUED"},
        "log": [],
    }
    direct_video_skills._save(run)
    sent: list[Path] = []

    def fake_send(_chat_id: str, path: Path, _file_name: str) -> dict[str, str]:
        sent.append(path)
        return {"message_id": "om_matte"}

    monkeypatch.setattr("assetclaw_matting.feishu.client.feishu_client.send_file_to_chat", fake_send)
    assert task_redelivery_service.run_redelivery("video", "VID_MATTE", "REQ")["ok"] is True
    with zipfile.ZipFile(sent[0]) as archive:
        names = archive.namelist()
    assert "smooth/README.txt" in names
    assert any(name.startswith("matte/") for name in names)


def test_image_sequence_full_redelivery_is_one_complete_zip(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(direct_image_skills, "RUNS_ROOT", tmp_path / "image_runs")
    run_dir = direct_image_skills.RUNS_ROOT / "IMG_TEST"
    items = []
    for index in (1, 2):
        original = run_dir / "original_images" / f"image_{index:02d}" / f"{index:02d}.png"
        matte = run_dir / "matte" / f"image_{index:02d}" / "result.png"
        smooth = run_dir / "smooth" / f"image_{index:02d}" / "result.png"
        _png(original)
        _png(matte)
        _png(smooth)
        items.append({
            "index": index,
            "source_name": f"{index:02d}.png",
            "original_path": str(original),
            "matte_dir": str(matte.parent),
            "smooth_dir": str(smooth.parent),
        })
    run = {
        "id": "IMG_TEST",
        "status": "DONE",
        "stage": "done",
        "run_label": "序列帧",
        "chat_id": "oc_test",
        "user_id": "",
        "images": items,
        "redelivery": {"request_id": "REQ", "status": "QUEUED"},
        "log": [],
    }
    direct_image_skills._save(run)
    sent: list[Path] = []

    def fake_send(_chat_id: str, path: Path, _file_name: str) -> dict[str, str]:
        sent.append(path)
        return {"message_id": "om_image"}

    monkeypatch.setattr("assetclaw_matting.feishu.client.feishu_client.send_file_to_chat", fake_send)
    assert task_redelivery_service.run_redelivery("image", "IMG_TEST", "REQ")["ok"] is True
    assert len(sent) == 1
    with zipfile.ZipFile(sent[0]) as archive:
        names = archive.namelist()
    assert sum(name.startswith("frames/") for name in names) == 2
    assert sum(name.startswith("matte/") for name in names) == 2
    assert sum(name.startswith("smooth/") for name in names) == 2


def test_full_redelivery_skills_are_registered_without_second_confirmation() -> None:
    for name in ("direct_video.full_resend", "direct_image.full_resend"):
        meta = get_skill_meta(name)
        assert meta is not None
        assert meta["requires_confirmation"] is False
        assert meta["risk_level"] == "egress_caution"


def test_redelivery_request_serializer_prevents_parallel_creation() -> None:
    active = 0
    peak = 0
    guard = threading.Lock()

    def work() -> None:
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with guard:
            active -= 1

    serialized = task_redelivery_service._serialized_request(work)
    workers = [threading.Thread(target=serialized) for _ in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert peak == 1
