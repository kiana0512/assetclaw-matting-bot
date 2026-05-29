"""Skill Gateway authentication and path validation — no FastAPI imports.

FastAPI dependency (`Header(...)`) lives in routes_skills.py so this
module is importable without FastAPI installed (CLI, tests, etc.).
"""
from __future__ import annotations

import logging
from pathlib import Path

from assetclaw_matting.skills.security import check_path_or_raise

log = logging.getLogger(__name__)


# ── Token validation ──────────────────────────────────────────────────────────

def check_skill_token(token: str) -> None:
    """Raise PermissionError if the Skill API token is invalid."""
    from assetclaw_matting.config import settings
    if not settings.skill_api_enabled:
        raise PermissionError("Skill API is disabled (SKILL_API_ENABLED=false)")
    if token != settings.skill_api_token:
        raise PermissionError("Invalid skill token")


# ── Path validation ───────────────────────────────────────────────────────────

def validate_skill_path(path: str) -> Path:
    """Validate a path for skill usage. Raises ValueError on failure."""
    return check_path_or_raise(path)


def validate_log_name(log_name: str) -> str:
    """Only allow reading known log file names."""
    allowed = {"gateway", "worker", "app"}
    name = log_name.strip().lower().removesuffix(".log")
    if name not in allowed:
        raise ValueError(
            f"Unknown log name {log_name!r}. Allowed: {sorted(allowed)}"
        )
    return name


# ── Audit logging ─────────────────────────────────────────────────────────────

def log_skill_call(
    request_id: str,
    skill: str,
    arguments: dict,
    result: dict | None,
    ok: bool,
    error: str | None,
    requested_by: str,
) -> None:
    """Write a skill call record to the audit table."""
    try:
        from assetclaw_matting.db.skill_call_repo import insert_skill_call
        insert_skill_call(
            request_id=request_id,
            skill=skill,
            arguments=arguments,
            result=result,
            ok=ok,
            error=error,
            requested_by=requested_by,
        )
    except Exception:
        log.warning("Failed to log skill call %s", skill, exc_info=True)
