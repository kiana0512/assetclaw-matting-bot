from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator


_WINDOWS_REPLACE_ATTEMPTS = 12
_WINDOWS_REPLACE_INITIAL_DELAY_SECONDS = 0.025
_WINDOWS_REPLACE_MAX_DELAY_SECONDS = 0.5


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
            _replace_with_transient_lock_retry(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return True


def _replace_with_transient_lock_retry(source: Path, target: Path) -> None:
    """Replace a state file despite short-lived Windows reader/AV handles.

    Windows rejects an otherwise valid atomic replace while another process
    has the destination open without delete sharing. Dashboard readers,
    indexers and antivirus scanners can all create that brief window. Retrying
    the same atomic operation keeps readers from ever observing partial JSON;
    permanent ACL/read-only failures still surface after a small bounded wait.
    """

    attempts = _atomic_replace_attempts()
    delay = _WINDOWS_REPLACE_INITIAL_DELAY_SECONDS
    for attempt in range(1, attempts + 1):
        try:
            os.replace(source, target)
            return
        except OSError as exc:
            if attempt >= attempts or not _is_transient_windows_replace_error(exc):
                raise
            time.sleep(delay)
            delay = min(delay * 2, _WINDOWS_REPLACE_MAX_DELAY_SECONDS)


def _is_transient_windows_replace_error(exc: OSError) -> bool:
    # ERROR_ACCESS_DENIED, ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION.
    return os.name == "nt" and (
        isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in {5, 32, 33}
    )


def _atomic_replace_attempts() -> int:
    return _WINDOWS_REPLACE_ATTEMPTS if os.name == "nt" else 1
