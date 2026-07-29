from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import uuid
from pathlib import Path
from typing import BinaryIO


log = logging.getLogger(__name__)

_thread: threading.Thread | None = None
_stop_event: threading.Event | None = None
_lock_handle: BinaryIO | None = None
_state_lock = threading.Lock()


def _acquire_instance_lock(path: Path) -> BinaryIO | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        handle.close()
        return None
    return handle


def _release_instance_lock(handle: BinaryIO | None) -> None:
    if handle is None or handle.closed:
        return
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        handle.close()


def run_monitor_loop(stop_event: threading.Event, *, lease_owner: str = "") -> None:
    """Run the low-cost DB/Feishu monitor; never enters a GPU task queue."""

    from assetclaw_matting.config import settings
    from assetclaw_matting.services.character_resolution import (
        bootstrap_waiting_character_runs,
        sweep_character_resolution_questions,
    )

    owner = lease_owner or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    interval = max(1, int(settings.character_confirmation_sweep_interval_seconds or 30))
    while not stop_event.is_set():
        try:
            bootstrap = bootstrap_waiting_character_runs()
            if bootstrap.get("discovered"):
                log.info("character monitor bootstrapped waiting runs=%s", bootstrap["discovered"])
            if bootstrap.get("errors"):
                log.warning("character monitor bootstrap errors=%s", bootstrap["errors"])
            result = sweep_character_resolution_questions(lease_owner=owner)
            if result.get("claimed") or result.get("errors"):
                log.info("character monitor sweep=%s", result)
        except Exception:
            log.exception("character confirmation monitor tick failed")
        stop_event.wait(interval)


def start_background_monitor() -> bool:
    """Start one in-process monitor unless a sidecar already owns the lock."""

    global _thread, _stop_event, _lock_handle
    from assetclaw_matting.config import settings

    with _state_lock:
        if _thread is not None and _thread.is_alive():
            return True
        lock_path = Path(settings.storage_dir) / ".character_resolution_monitor.lock"
        handle = _acquire_instance_lock(lock_path)
        if handle is None:
            log.info("character confirmation monitor already runs in another process")
            return False
        stop_event = threading.Event()
        thread = threading.Thread(
            target=_background_target,
            args=(stop_event, handle),
            name="character_confirmation_monitor",
            daemon=True,
        )
        _lock_handle = handle
        _stop_event = stop_event
        _thread = thread
        thread.start()
        return True


def _background_target(stop_event: threading.Event, handle: BinaryIO) -> None:
    global _thread, _stop_event, _lock_handle
    try:
        run_monitor_loop(stop_event)
    finally:
        _release_instance_lock(handle)
        with _state_lock:
            _thread = None
            _stop_event = None
            _lock_handle = None


def stop_background_monitor(timeout: float = 5.0) -> None:
    with _state_lock:
        thread = _thread
        stop_event = _stop_event
    if stop_event is not None:
        stop_event.set()
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=max(0.0, timeout))


def main() -> None:
    from assetclaw_matting.config import settings
    from assetclaw_matting.db.schema import create_tables
    from assetclaw_matting.db.sqlite import init_db
    from assetclaw_matting.logging_setup import setup_logging

    settings.ensure_dirs()
    setup_logging(settings.log_dir, name="character_resolution_monitor")
    init_db(settings.data_db_path)
    create_tables()
    lock_path = Path(settings.storage_dir) / ".character_resolution_monitor.lock"
    handle = _acquire_instance_lock(lock_path)
    if handle is None:
        log.info("character confirmation monitor already running; sidecar exits")
        return

    stop_event = threading.Event()

    def _stop(signum, _frame) -> None:
        log.info("character confirmation monitor received signal=%s", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop)
    log.info("character confirmation sidecar started pid=%s", os.getpid())
    try:
        run_monitor_loop(stop_event)
    finally:
        _release_instance_lock(handle)
        log.info("character confirmation sidecar stopped")


if __name__ == "__main__":
    main()
