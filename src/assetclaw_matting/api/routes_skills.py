"""Skill Gateway API routes.

All endpoints require X-Skill-Token header.
These are the primary integration surface for the OpenClaw cloud agent.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse

from assetclaw_matting.skills.schemas import SkillCallRequest, SkillCallResponse

log = logging.getLogger(__name__)
router = APIRouter(prefix="/skills/v1", tags=["skills"])


# FastAPI dependency — defined here to keep skills/auth.py free of fastapi imports
def _verify_skill_token(x_skill_token: str = Header(...)) -> None:
    from assetclaw_matting.skills.auth import check_skill_token
    try:
        check_skill_token(x_skill_token)
    except PermissionError as exc:
        code = 503 if "disabled" in str(exc).lower() else 401
        raise HTTPException(status_code=code, detail=str(exc))


@router.get("/manifest", dependencies=[Depends(_verify_skill_token)])
async def skill_manifest() -> JSONResponse:
    """Return the full skill manifest for this machine.

    OpenClaw should call this first to discover available skills.
    """
    from assetclaw_matting.skills.registry import get_manifest
    return JSONResponse(get_manifest())


@router.post("/call", dependencies=[Depends(_verify_skill_token)])
async def skill_call(body: SkillCallRequest) -> SkillCallResponse:
    """Execute a skill by name.

    All calls are authenticated, logged, and subject to path/security validation.
    """
    from assetclaw_matting.skills.registry import call_skill

    result = call_skill(
        name=body.skill,
        arguments=body.arguments,
        requested_by=body.requested_by,
        request_id=body.request_id,
    )
    return SkillCallResponse(
        ok=result.get("ok", False),
        skill=body.skill,
        result=result.get("result"),
        message=result.get("message", ""),
        error=result.get("error"),
        request_id=body.request_id,
    )


@router.get("/calls", dependencies=[Depends(_verify_skill_token)])
async def list_skill_calls(limit: int = 50) -> JSONResponse:
    """Return recent skill call audit log."""
    from assetclaw_matting.db.sqlite import get_connection
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, request_id, skill, ok, error, requested_by, created_at "
            "FROM skill_calls ORDER BY created_at DESC LIMIT ?",
            (min(limit, 200),),
        ).fetchall()
    return JSONResponse({"calls": [dict(r) for r in rows], "count": len(rows)})
