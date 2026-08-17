from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from assetclaw_matting.config import settings
from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import get_connection, init_db
from assetclaw_matting.services.character_resolution import (
    get_run_resolutions,
    reopen_failed_run_resolutions,
    try_resolve_reply,
)
from assetclaw_matting.skills import direct_video_skills


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def repair(run_ids: list[str], character_id: str) -> dict[str, object]:
    # Put completed matte-only incidents back at the character gate before
    # querying pending rows. This prevents normal terminal-state pruning from
    # cancelling the operator-approved repair assignment.
    for run_id in run_ids:
        run = direct_video_skills._load(run_id)
        if not run:
            raise RuntimeError(f"direct video run not found: {run_id}")
        if str(run.get("result_mode") or "") != "matte_only":
            raise RuntimeError(f"{run_id} is not a matte-only incident repair")
        run["status"] = "WAITING_CHARACTER"
        run["stage"] = "waiting_character"
        run["recovery_from_stage"] = "postprocess"
        run["error"] = ""
        run["worker_pid"] = 0
        run["updated_at"] = _now()
        direct_video_skills._save(run)

    rows: list[dict[str, object]] = []
    for run_id in run_ids:
        resolutions = get_run_resolutions("direct_video", run_id)
        if len(resolutions) != 1:
            raise RuntimeError(f"expected exactly one role unit for {run_id}, got {len(resolutions)}")
        row = dict(resolutions[0])
        if str(row.get("status") or "") == "FAILED":
            reopen_failed_run_resolutions("direct_video", run_id)
            row = dict(get_run_resolutions("direct_video", run_id)[0])
        if str(row.get("status") or "") == "CANCELLED":
            # Only this explicit matte-only incident repair may revive a row
            # cancelled by terminal-state pruning. Normal recovery never
            # reopens user-cancelled role questions.
            with get_connection() as conn:
                conn.execute(
                    "UPDATE character_resolutions SET status='PENDING', version=version+1, updated_at=? "
                    "WHERE run_kind='direct_video' AND run_id=? AND status='CANCELLED'",
                    (_now(), run_id),
                )
                conn.execute(
                    "UPDATE character_resolution_questions SET status='PENDING', failed_at=NULL, "
                    "next_action_at=NULL, lease_owner=NULL, lease_until=NULL, updated_at=? "
                    "WHERE run_kind='direct_video' AND run_id=? AND status='CANCELLED'",
                    (_now(), run_id),
                )
            row = dict(get_run_resolutions("direct_video", run_id)[0])
        rows.append(row)

    actors = {
        (str(row.get("conversation_id") or ""), str(row.get("user_id") or ""))
        for row in rows
    }
    if len(actors) != 1:
        raise RuntimeError("all repaired tasks must belong to the same Feishu actor")
    conversation_id, user_id = next(iter(actors))
    assignments = []
    for row in rows:
        status = str(row.get("status") or "")
        if status == "FROZEN":
            if str(row.get("character_id") or "").casefold() != character_id.casefold():
                raise RuntimeError(f"{row['run_id']} is already frozen to another character")
            continue
        if status != "PENDING":
            raise RuntimeError(f"{row['run_id']} role status is not repairable: {status}")
        assignments.append(f"{row['question_token']}={character_id}")

    resolution_result: dict[str, object] = {"handled": True, "ok": True, "affected_runs": []}
    if assignments:
        resolution_result = try_resolve_reply(
            conversation_id=conversation_id,
            user_id=user_id,
            message_id=f"operator-character-repair-{int(datetime.now().timestamp())}",
            text=" ".join(assignments),
        )
        if not resolution_result.get("ok"):
            raise RuntimeError(str(resolution_result.get("message") or "role binding repair failed"))

    launched: list[dict[str, object]] = []
    for run_id in run_ids:
        run = direct_video_skills._load(run_id)
        if not run:
            raise RuntimeError(f"direct video run not found: {run_id}")
        run["status"] = "QUEUED"
        run["stage"] = "character_resolved"
        run["recovery_from_stage"] = "postprocess"
        run["result_mode"] = "full"
        run["postprocess_skipped"] = {}
        run["character_question"] = ""
        run.setdefault("character_resolution", {})["pending"] = 0
        run["error"] = ""
        run["worker_pid"] = 0
        run["updated_at"] = _now()
        direct_video_skills._append_log(
            run,
            f"角色绑定修复：{character_id}；复用已完成抠图帧，仅重跑 Cherry 后处理、打包和交付。",
        )
        direct_video_skills._save(run)
        launched.append({
            "run_id": run_id,
            "worker_started": direct_video_skills._start_worker(run_id, recover=True),
        })

    return {
        "ok": True,
        "character_id": character_id,
        "assignments": assignments,
        "resolution_result": resolution_result,
        "launched": launched,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bind one character to direct-video tasks and resume from verified mattes."
    )
    parser.add_argument("--character", required=True)
    parser.add_argument("run_ids", nargs="+")
    args = parser.parse_args()
    init_db(settings.data_db_path)
    create_tables()
    print(json.dumps(repair(args.run_ids, args.character), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
