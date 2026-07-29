from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    """Cross-process one-byte lock used only for one task's status file."""

    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_save_task_json(
    path: Path,
    payload: dict[str, Any],
    *,
    expected_statuses: Iterable[str] | None = None,
) -> bool:
    """Atomically save status, optionally as a compare-and-swap transition.

    ``CANCELED`` is irreversible: a stale worker may still persist progress,
    but it can never resurrect a canceled task.
    """

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    expected = {str(value).upper() for value in expected_statuses or []}
    with _exclusive_lock(path):
        existing: dict[str, Any] = {}
        if path.is_file():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                existing = {}
        existing_status = str(existing.get("status") or "").upper()
        if expected and existing_status not in expected:
            return False
        incoming_status = str(payload.get("status") or "").upper()
        if existing_status == "CANCELED" and incoming_status != "CANCELED":
            payload["status"] = "CANCELED"
            payload["stage"] = str(existing.get("stage") or "canceled")
            payload["error"] = str(existing.get("error") or payload.get("error") or "")
        temporary = path.with_name(
            f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return True
