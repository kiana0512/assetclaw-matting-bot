"""Skill Gateway authentication and path security."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass  # TYPE_CHECKING-only imports go here

log = logging.getLogger(__name__)


# ── Token auth ────────────────────────────────────────────────────────────────

def verify_skill_token(x_skill_token: str = "") -> None:
    """FastAPI dependency: verify X-Skill-Token header.

    NOTE: The actual FastAPI dependency signature uses Header(...).
    Import Header/HTTPException inside the function body to allow
    importing this module in non-FastAPI contexts (e.g. tests, CLI).
    """
    from fastapi import Header, HTTPException  # noqa: F401 (used in route definitions)
    """FastAPI dependency: verify X-Skill-Token header."""
    from assetclaw_matting.config import settings
    if not settings.skill_api_enabled:
        raise HTTPException(status_code=503, detail="Skill API is disabled")
    if x_skill_token != settings.skill_api_token:
        raise HTTPException(status_code=401, detail="Invalid skill token")


# ── Path security ─────────────────────────────────────────────────────────────

def validate_skill_path(path: str) -> Path:
    """Validate a path parameter for skill usage.

    Checks:
    1. Path is under one of the ALLOWED_ROOTS.
    2. Path does not contain any DENY_PATH_PATTERNS.
    3. No path traversal (..).

    Returns the resolved Path if valid. Raises ValueError otherwise.
    """
    from assetclaw_matting.config import settings
    from assetclaw_matting.services.file_store import validate_allowed_path

    try:
        resolved = Path(path).resolve()
    except Exception as exc:
        raise ValueError(f"Invalid path: {path!r} — {exc}")

    # Disallow path traversal via double-dot patterns in the raw input
    if ".." in Path(path).parts:
        raise ValueError(f"Path traversal not allowed: {path!r}")

    # Check deny patterns (case-insensitive)
    path_str = str(resolved)
    for pattern in settings.deny_path_patterns_list:
        if pattern.lower() in path_str.lower():
            raise ValueError(
                f"Path contains denied pattern {pattern!r}: {resolved}"
            )

    # Check allowed roots (if configured)
    validate_allowed_path(resolved)

    return resolved


def validate_log_name(log_name: str) -> str:
    """Only allow reading known log files under the logs directory."""
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
    import json
    from datetime import datetime, timezone
    from assetclaw_matting.db.sqlite import get_connection
    now = datetime.now(timezone.utc).isoformat()
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO skill_calls "
                "(request_id, skill, arguments_json, result_json, ok, error, requested_by, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    request_id, skill,
                    json.dumps(arguments, default=str),
                    json.dumps(result, default=str) if result is not None else None,
                    int(ok), error, requested_by, now,
                ),
            )
    except Exception:
        log.warning("Failed to log skill call %s", skill, exc_info=True)
