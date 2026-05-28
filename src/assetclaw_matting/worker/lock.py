from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

_LOCK_PATH = Path(__file__).parent.parent.parent.parent.parent / "worker.lock"


def acquire() -> None:
    """Write PID to lock file. Logs a warning if another lock file exists."""
    if _LOCK_PATH.exists():
        try:
            existing_pid = int(_LOCK_PATH.read_text().strip())
            log.warning(
                "worker.lock exists (PID %s). "
                "If no other worker is running, delete %s and restart.",
                existing_pid, _LOCK_PATH,
            )
        except Exception:
            log.warning("worker.lock exists but could not read PID. Overwriting.")
    _LOCK_PATH.write_text(str(os.getpid()))
    log.debug("Worker lock acquired (PID %s): %s", os.getpid(), _LOCK_PATH)


def release() -> None:
    try:
        _LOCK_PATH.unlink(missing_ok=True)
        log.debug("Worker lock released")
    except Exception:
        log.warning("Failed to release worker lock at %s", _LOCK_PATH)
