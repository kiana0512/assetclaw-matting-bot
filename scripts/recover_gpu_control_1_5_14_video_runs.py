from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from assetclaw_matting.config import settings
from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import get_connection, init_db
from assetclaw_matting.services.character_resolution import reopen_failed_run_resolutions
from assetclaw_matting.skills import direct_video_skills


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def recover(run_id: str) -> dict[str, object]:
    run = direct_video_skills._load(run_id)
    if not run:
        raise RuntimeError(f"direct video run not found: {run_id}")
    children = run.setdefault("children", {})
    child_id = str(children.get("comfyui_run_id") or "")
    if not child_id:
        raise RuntimeError(f"matting child is missing: {run_id}")

    # Technical matting failures close role questions. Reopen them before
    # recovery reaches the postprocess gate so valid user choices survive.
    reopened_character_units = reopen_failed_run_resolutions("direct_video", run_id)

    with get_connection() as conn:
        row = conn.execute(
            "SELECT status, options_json FROM comfyui_runs WHERE id = ?",
            (child_id,),
        ).fetchone()
        if not row:
            raise RuntimeError(f"matting child row not found: {child_id}")
        options = json.loads(row["options_json"] or "{}")
        previous_backend = str(options.get("matting_backend") or "local")
        remote = dict(options.get("gpu_control") or {})
        remote.setdefault("trace_id", str(options.get("trace_id") or f"assetclaw-{child_id.lower()}"))
        remote["status"] = str(remote.get("status") or "PREPARING")
        remote["recovered_for_contract"] = "gpu-control-1.5.14"
        remote["recovered_at"] = _now()
        for key in ("client_error", "failed_at", "poll_error", "poll_error_count"):
            remote.pop(key, None)
        options["matting_backend"] = "gpu_control"
        options["backend_selection_reason"] = "GPU Control 1.5.14 incident recovery; local matting removed"
        options["gpu_control"] = remote
        if previous_backend != "gpu_control":
            options["prompt_map"] = []
        conn.execute(
            "UPDATE comfyui_runs SET status = 'RUNNING', options_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(options, ensure_ascii=False), _now(), child_id),
        )

    run["status"] = "RUNNING"
    run["stage"] = "matting"
    run["recovery_from_stage"] = "matting"
    run["matting_backend"] = "gpu_control"
    run["error"] = ""
    run["worker_pid"] = 0
    children["comfyui"] = {
        **dict(children.get("comfyui") or {}),
        "status": "RUNNING",
        "backend": "gpu_control",
        "last_error": "",
        "remote_status": str(remote.get("status") or "PREPARING"),
    }
    direct_video_skills._append_log(
        run,
        f"GPU Control 1.5.14 状态修复：重新附着抠图子任务 {child_id}，复用原业务 ID 与幂等键",
    )
    direct_video_skills._save(run)
    started = direct_video_skills._start_worker(run_id, recover=True)
    return {
        "run_id": run_id,
        "child_id": child_id,
        "previous_backend": previous_backend,
        "status": "RUNNING",
        "worker_started": started,
        "reopened_character_units": reopened_character_units,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Recover AssetClaw video tasks after the GPU Control 1.5.14 identity cutover.")
    parser.add_argument("run_ids", nargs="+")
    args = parser.parse_args()
    init_db(settings.data_db_path)
    create_tables()
    results = [recover(run_id) for run_id in args.run_ids]
    print(json.dumps({"ok": True, "results": results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
