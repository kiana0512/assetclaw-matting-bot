from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


_THREAD_ROUTE_LOCK = threading.Lock()


@contextmanager
def matting_route_lock() -> Iterator[None]:
    """Serialize backend selection plus run creation across local processes.

    Hybrid routing is only safe when two workers cannot both observe an idle
    4070Ti and select it before either run is persisted.  The file lock covers
    detached video workers as well as Gateway threads.
    """

    from assetclaw_matting.config import settings

    root = Path(settings.storage_dir) / "gpu_control_batches"
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".route.lock"
    with _THREAD_ROUTE_LOCK, lock_path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return

        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def select_matting_backend(
    total: int,
    requested: str | None = None,
    *,
    include_handshake: bool = False,
) -> tuple[str, str] | tuple[str, str, dict[str, Any]]:
    """Return ``local`` or ``gpu_control`` and a human-readable reason.

    The caller must hold :func:`matting_route_lock` until its run row is
    committed, otherwise idle-local selection is racy across worker processes.
    """

    from assetclaw_matting.config import settings

    def result(backend: str, reason: str, handshake: dict[str, Any] | None = None):
        if include_handshake:
            return backend, reason, dict(handshake or {"checked": False})
        return backend, reason

    if settings.comfyui_fake_mode:
        return result("local", "fake mode always uses the local adapter")

    mode = str(settings.matting_backend_mode or "local").strip().lower()
    aliases = {"cluster": "gpu_control", "remote": "gpu_control", "comfyui": "local"}
    mode = aliases.get(mode, mode)
    forced = aliases.get(str(requested or "").strip().lower(), str(requested or "").strip().lower())
    if forced:
        if forced not in {"local", "gpu_control"}:
            raise ValueError("backend must be local or gpu_control")
        if forced == "gpu_control" and not _cluster_configured():
            raise RuntimeError("GPU Control backend was requested but is not configured")
        if forced == "local":
            return result("local", "explicit backend=local")
        handshake = _cluster_handshake() if include_handshake else None
        if handshake and not handshake.get("accepting_batches"):
            raise RuntimeError(f"GPU Control is not accepting batches: {handshake.get('reason') or 'capacity unavailable'}")
        return result("gpu_control", "explicit backend=gpu_control", handshake)

    if mode == "local":
        return result("local", "matting_backend_mode=local")
    if mode not in {"hybrid", "gpu_control"}:
        raise ValueError("MATTING_BACKEND_MODE must be local, hybrid, or gpu_control")
    if not _cluster_configured():
        if mode == "gpu_control":
            raise RuntimeError("MATTING_BACKEND_MODE=gpu_control but GPU_CONTROL_BASE_URL is empty")
        return result("local", "GPU Control is not configured")
    handshake = _cluster_handshake() if include_handshake else None
    if handshake and not handshake.get("accepting_batches"):
        if mode == "gpu_control":
            raise RuntimeError(f"GPU Control is not accepting batches: {handshake.get('reason') or 'capacity unavailable'}")
        return result("local", f"GPU Control handshake unavailable: {handshake.get('reason') or 'not accepting'}", handshake)
    if mode == "gpu_control":
        maximum = max(1, int(settings.gpu_control_max_batch_frames or 1))
        if int(total) > maximum:
            raise RuntimeError(f"GPU Control batch has {total} frames, exceeding configured limit {maximum}")
        return result("gpu_control", "matting_backend_mode=gpu_control", handshake)

    maximum = max(1, int(settings.gpu_control_max_batch_frames or 1))
    if int(total) > maximum:
        return result("local", f"batch size {total} exceeds remote limit {maximum}; keep the intact task local", handshake)
    threshold = max(1, int(settings.gpu_control_large_batch_threshold or 1))
    if int(total) >= threshold:
        return result("gpu_control", f"batch size {total} reached remote threshold {threshold}", handshake)
    if _active_local_run_count() > 0:
        return result("gpu_control", "local 4070Ti already has an active matting run", handshake)
    return result("local", "local 4070Ti is idle and the batch is below the remote threshold", handshake)


def _cluster_handshake() -> dict[str, Any]:
    """Probe readiness and optional scheduler capacity before routing a batch."""

    from assetclaw_matting.config import settings
    from assetclaw_matting.services.gpu_control_batch import GpuControlBatchClient

    checked_at = datetime.now(timezone.utc).isoformat()
    active_batches = _active_remote_run_count()
    client_limit = max(1, int(settings.gpu_control_max_inflight_batches or 1))
    base: dict[str, Any] = {
        "checked": True,
        "checked_at": checked_at,
        "ready": False,
        "capacity_supported": False,
        "active_batches": active_batches,
        "client_max_inflight_batches": client_limit,
        "accepting_batches": False,
    }
    try:
        client = GpuControlBatchClient()
        ready = client.health_ready(request_id="assetclaw-route-ready")
        base["readiness"] = {
            key: ready.get(key)
            for key in ("status", "database", "redis")
            if ready.get(key) is not None
        }
        base["ready"] = str(ready.get("status") or "").lower() == "ready"
        capacity = client.scheduler_capacity(request_id="assetclaw-route-capacity")
        supported = bool(capacity.get("supported"))
        base["capacity_supported"] = supported
        if supported:
            base["capacity"] = {
                key: capacity.get(key)
                for key in (
                    "status",
                    "accepting_batches",
                    "queue_depth",
                    "active_batches",
                    "idle_nodes",
                    "busy_nodes",
                    "online_nodes",
                    "suggested_max_new_batches",
                    "max_batch_frames",
                )
                if capacity.get(key) is not None
            }
            accepting = capacity.get("accepting_batches") is True
            suggested = capacity.get("suggested_max_new_batches")
            if suggested is not None:
                accepting = accepting and int(suggested) > 0
        else:
            accepting = active_batches < client_limit
        base["accepting_batches"] = bool(base["ready"] and accepting)
        if not base["ready"]:
            base["reason"] = "readiness probe did not return ready"
        elif not accepting:
            base["reason"] = "scheduler capacity is full"
        else:
            base["reason"] = "scheduler handshake accepted"
        return base
    except Exception as exc:
        base["reason"] = f"handshake failed: {exc}"
        base["error"] = str(exc)
        return base


def local_pipeline_serialization_required() -> bool:
    """Legacy whole-video serialization is only needed in local-only mode."""

    from assetclaw_matting.config import settings

    return str(settings.matting_backend_mode or "local").strip().lower() in {"local", "comfyui"}


def _cluster_configured() -> bool:
    from assetclaw_matting.config import settings

    return bool(str(settings.gpu_control_base_url or "").strip())


def _active_local_run_count() -> int:
    from assetclaw_matting.db.sqlite import get_connection

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT options_json
            FROM comfyui_runs
            WHERE status IN ('RUNNING', 'QUEUED', 'PAUSED')
            """
        ).fetchall()
    count = 0
    for row in rows:
        try:
            options = json.loads(row["options_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            options = {}
        if str(options.get("matting_backend") or "local") == "local":
            count += 1
    return count


def _active_remote_run_count() -> int:
    from assetclaw_matting.db.sqlite import get_connection

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT options_json
            FROM comfyui_runs
            WHERE status IN ('RUNNING', 'QUEUED', 'PAUSED')
            """
        ).fetchall()
    count = 0
    for row in rows:
        try:
            options = json.loads(row["options_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            options = {}
        if str(options.get("matting_backend") or "local") == "gpu_control":
            count += 1
    return count
