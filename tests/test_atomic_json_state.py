from __future__ import annotations

import json
import threading
from pathlib import Path

from assetclaw_matting.services.atomic_json_state import atomic_save_task_json


def test_status_compare_and_swap_allows_only_one_character_resume(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    atomic_save_task_json(path, {"id": "VID_TEST", "status": "WAITING_CHARACTER", "stage": "waiting_character"})
    barrier = threading.Barrier(3)
    results: list[bool] = []

    def claim(worker: str) -> None:
        barrier.wait()
        results.append(
            atomic_save_task_json(
                path,
                {"id": "VID_TEST", "status": "QUEUED", "stage": "character_resolved", "claim": worker},
                expected_statuses={"WAITING_CHARACTER"},
            )
        )

    threads = [threading.Thread(target=claim, args=(name,)) for name in ("one", "two")]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert sorted(results) == [False, True]
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "QUEUED"


def test_canceled_status_cannot_be_resurrected_by_stale_worker(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    atomic_save_task_json(path, {"id": "IMG_TEST", "status": "CANCELED", "stage": "canceled"})
    stale = {"id": "IMG_TEST", "status": "RUNNING", "stage": "postprocess"}

    atomic_save_task_json(path, stale)

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["status"] == "CANCELED"
    assert saved["stage"] == "canceled"
    assert stale["status"] == "CANCELED"
