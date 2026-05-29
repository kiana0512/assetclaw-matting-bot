"""Repository for skill_calls audit table."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from assetclaw_matting.db.sqlite import get_connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def insert_skill_call(
    request_id: str,
    skill: str,
    arguments: dict[str, Any],
    result: Optional[dict[str, Any]],
    ok: bool,
    error: Optional[str],
    requested_by: str,
) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO skill_calls "
            "(request_id, skill, arguments_json, result_json, ok, error, requested_by, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                request_id,
                skill,
                json.dumps(arguments, default=str),
                json.dumps(result, default=str) if result is not None else None,
                int(ok),
                error,
                requested_by,
                _now(),
            ),
        )


def list_skill_calls(
    skill: Optional[str] = None,
    requested_by: Optional[str] = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    conditions: list[str] = []
    params: list[Any] = []
    if skill:
        conditions.append("skill = ?")
        params.append(skill)
    if requested_by:
        conditions.append("requested_by = ?")
        params.append(requested_by)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(min(limit, 500))
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT id, request_id, skill, ok, error, requested_by, created_at "
            f"FROM skill_calls {where} ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
    return [dict(r) for r in rows]
