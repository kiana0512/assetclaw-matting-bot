from __future__ import annotations

import logging
import time
from pathlib import Path

import requests

from assetclaw_matting.config import settings
from assetclaw_matting.logging_setup import setup_logging
from assetclaw_matting.worker import lock as worker_lock

log = logging.getLogger(__name__)


def _headers() -> dict[str, str]:
    return {"X-Worker-Token": settings.worker_token}


def _gw(path: str) -> str:
    return f"{settings.gateway_base_url.rstrip('/')}{path}"


def run_worker() -> None:
    settings.ensure_dirs()
    setup_logging(settings.log_dir, name="worker")
    log.info(
        "Worker starting: id=%s  gateway=%s  fake_mode=%s",
        settings.worker_id,
        settings.gateway_base_url,
        settings.comfyui_fake_mode,
    )
    worker_lock.acquire()
    try:
        _loop()
    finally:
        worker_lock.release()
        log.info("Worker stopped")


def _loop() -> None:
    interval = settings.worker_poll_interval_seconds
    while True:
        try:
            _poll_once()
        except KeyboardInterrupt:
            log.info("Worker interrupted by user")
            break
        except Exception:
            log.exception("Unexpected error in worker poll loop")
            time.sleep(interval)


def _poll_once() -> None:
    interval = settings.worker_poll_interval_seconds
    try:
        resp = requests.get(_gw("/worker/tasks/next"), headers=_headers(), timeout=15)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("Failed to poll gateway: %s", exc)
        time.sleep(interval)
        return

    data = resp.json()
    task = data.get("task")
    if task is None:
        time.sleep(interval)
        return

    task_id: str = task["task_id"]
    input_path_str: str = task["input_path"]
    output_path_str: str = task["output_path"]
    workflow_type: str = task["workflow_type"]

    log.info(
        "Picked up task %s  workflow=%s  input=%s",
        task_id, workflow_type, input_path_str,
    )
    _process_task(task_id, input_path_str, output_path_str)


def _process_task(
    task_id: str,
    input_path_str: str,
    output_path_str: str,
) -> None:
    from assetclaw_matting.worker.comfy_worker import run_matting

    # 1. Mark started
    try:
        resp = requests.post(
            _gw(f"/worker/tasks/{task_id}/started"),
            headers=_headers(),
            json={"worker_id": settings.worker_id},
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as exc:
        log.error("Failed to mark task %s started: %s", task_id, exc)
        return

    try:
        input_path = _resolve_input(task_id, input_path_str)
        output_path = Path(output_path_str)

        # 2. Run matting
        run_matting(input_path, output_path, task_id=task_id)

        # 3. Report success
        _report_succeeded(task_id, str(output_path))

    except Exception as exc:
        log.exception("Task %s failed", task_id)
        _report_failed(task_id, str(exc))


def _resolve_input(task_id: str, input_path_str: str) -> Path:
    """Return local path to input file.

    If the path exists on this machine, use it directly (same-machine deployment).
    Otherwise, download via HTTP from the gateway (remote deployment).
    """
    local = Path(input_path_str)
    if local.exists():
        return local

    log.info(
        "Input path %s not accessible locally, downloading via HTTP for task %s",
        input_path_str, task_id,
    )
    return _download_input(task_id, input_path_str)


def _download_input(task_id: str, input_path_str: str) -> Path:
    try:
        resp = requests.get(
            _gw(f"/worker/tasks/{task_id}/input"),
            headers=_headers(),
            stream=True,
            timeout=60,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"Failed to download input for task {task_id}: {exc}") from exc

    dest = settings.tasks_dir / task_id / "input_downloaded.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as fh:
        for chunk in resp.iter_content(chunk_size=65536):
            fh.write(chunk)
    log.debug("Downloaded input to %s", dest)
    return dest


def _report_succeeded(task_id: str, output_path: str) -> None:
    try:
        resp = requests.post(
            _gw(f"/worker/tasks/{task_id}/succeeded"),
            headers=_headers(),
            json={"worker_id": settings.worker_id, "output_path": output_path},
            timeout=30,
        )
        resp.raise_for_status()
        log.info("Task %s succeeded, output=%s", task_id, output_path)
    except Exception as exc:
        log.error("Failed to report task %s succeeded: %s", task_id, exc)


def _report_failed(task_id: str, error: str) -> None:
    try:
        resp = requests.post(
            _gw(f"/worker/tasks/{task_id}/failed"),
            headers=_headers(),
            json={"worker_id": settings.worker_id, "error": error[:2000]},
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as exc:
        log.error("Failed to report task %s failed: %s", task_id, exc)
