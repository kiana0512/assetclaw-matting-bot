"""Skill implementations for log access.

Only reads from the configured logs/ directory.
Only allows known log names (gateway, worker, app).
Maximum 200 lines per request.
"""
from __future__ import annotations

from typing import Any

from assetclaw_matting.skills.auth import validate_log_name

_MAX_LINES = 200


def log_tail(log_name: str = "gateway", lines: int = 50) -> dict[str, Any]:
    from assetclaw_matting.config import settings

    name = validate_log_name(log_name)
    log_path = settings.log_dir / f"{name}.log"

    if not log_path.exists():
        return {
            "log_name": name,
            "path": str(log_path),
            "lines": [],
            "message": "Log file does not exist yet.",
        }

    max_lines = min(int(lines), _MAX_LINES)
    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as fh:
            all_lines = fh.readlines()
        tail = [l.rstrip("\n") for l in all_lines[-max_lines:]]
    except OSError as exc:
        raise RuntimeError(f"Could not read log file: {exc}") from exc

    return {
        "log_name": name,
        "path": str(log_path),
        "total_lines": len(all_lines),
        "returned_lines": len(tail),
        "lines": tail,
    }
