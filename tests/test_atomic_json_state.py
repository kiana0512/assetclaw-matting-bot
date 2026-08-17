from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from assetclaw_matting.services.atomic_json_state import atomic_save_task_json
from assetclaw_matting.services import atomic_json_state


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


def test_confirmed_rerun_can_atomically_restart_a_canceled_task(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    atomic_save_task_json(path, {"id": "IMG_TEST", "status": "CANCELED", "stage": "canceled"})
    rerun = {"id": "IMG_TEST", "status": "QUEUED", "stage": "full_rerun_queued"}

    saved = atomic_save_task_json(
        path,
        rerun,
        expected_statuses={"CANCELED"},
        allow_canceled_restart=True,
    )

    assert saved is True
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["status"] == "QUEUED"
    assert persisted["stage"] == "full_rerun_queued"


def test_canceled_restart_override_requires_compare_and_swap(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    atomic_save_task_json(path, {"id": "IMG_TEST", "status": "CANCELED", "stage": "canceled"})
    unsafe = {"id": "IMG_TEST", "status": "QUEUED", "stage": "full_rerun_queued"}

    atomic_save_task_json(path, unsafe, allow_canceled_restart=True)

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["status"] == "CANCELED"
    assert persisted["stage"] == "canceled"


def test_windows_atomic_replace_retries_transient_access_denied(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    real_replace = os.replace
    calls: list[tuple[Path, Path]] = []

    def flaky_replace(source: Path, target: Path) -> None:
        calls.append((Path(source), Path(target)))
        if len(calls) < 3:
            error = PermissionError(13, "destination is temporarily locked", str(target))
            error.winerror = 5
            raise error
        real_replace(source, target)

    monkeypatch.setattr(atomic_json_state.os, "replace", flaky_replace)
    monkeypatch.setattr(atomic_json_state, "_atomic_replace_attempts", lambda: 12)
    monkeypatch.setattr(atomic_json_state, "_is_transient_windows_replace_error", lambda _exc: True)
    monkeypatch.setattr(atomic_json_state.time, "sleep", lambda _seconds: None)

    atomic_save_task_json(path, {"id": "IMG_RETRY", "status": "RUNNING"})

    assert len(calls) == 3
    assert json.loads(path.read_text(encoding="utf-8"))["id"] == "IMG_RETRY"
    assert not list(tmp_path.glob(".status.json.*.tmp"))


def test_windows_atomic_replace_keeps_retry_bounded(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    calls = 0

    def always_locked(_source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        error = PermissionError(13, "destination is locked", str(target))
        error.winerror = 5
        raise error

    monkeypatch.setattr(atomic_json_state.os, "replace", always_locked)
    monkeypatch.setattr(
        atomic_json_state,
        "_atomic_replace_attempts",
        lambda: atomic_json_state._WINDOWS_REPLACE_ATTEMPTS,
    )
    monkeypatch.setattr(atomic_json_state, "_is_transient_windows_replace_error", lambda _exc: True)
    monkeypatch.setattr(atomic_json_state.time, "sleep", lambda _seconds: None)

    with pytest.raises(PermissionError):
        atomic_save_task_json(path, {"id": "IMG_RETRY", "status": "RUNNING"})

    assert calls == atomic_json_state._WINDOWS_REPLACE_ATTEMPTS
    assert not list(tmp_path.glob(".status.json.*.tmp"))
