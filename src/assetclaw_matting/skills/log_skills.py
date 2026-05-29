"""Log tail skill — reads last N lines from gateway or worker logs.

Security: only reads from the configured logs/ directory.
Only allows known log names (gateway, worker, app).
Maximum 200 lines per request.
All lines are sanitised to remove tokens/secrets before returning.
"""
from __future__ import annotations

from typing import Any

from assetclaw_matting.skills.auth import validate_log_name
from assetclaw_matting.skills.security import sanitize_log_lines

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
        raw_tail = [l.rstrip("\n") for l in all_lines[-max_lines:]]
        # Sanitise before returning — strip tokens/secrets
        clean_tail = sanitize_log_lines(raw_tail)
    except OSError as exc:
        raise RuntimeError(f"Could not read log file: {exc}") from exc

    return {
        "log_name": name,
        "path": str(log_path),
        "total_lines": len(all_lines),
        "returned_lines": len(clean_tail),
        "sanitized": True,
        "lines": clean_tail,
    }
