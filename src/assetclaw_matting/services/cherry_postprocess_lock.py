from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from assetclaw_matting.config import settings


@contextmanager
def cherry_postprocess_lock() -> Iterator[None]:
    """Serialize only the shared Cherry browser runtime across processes.

    GPU Control submission and polling remain fully parallel.  The lock covers
    only local HTML/Chrome postprocessing, whose concurrent sessions otherwise
    interfere and can report false overall-deadline failures.
    """

    lock_dir = Path(settings.storage_dir) / ".runtime_locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "cherry_postprocess.lock"
    with lock_path.open("a+b") as handle:
        if lock_path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        if os.name == "nt":
            import msvcrt

            while True:
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(2)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return

        import fcntl

        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(2)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
