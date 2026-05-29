"""Skill Gateway path and content security utilities.

Centralises all security checks used across skill implementations:
- Path validation against ALLOWED_ROOTS and DENY_PATH_PATTERNS
- Log line sanitisation (strip secrets before returning to callers)
- Sensitive string detection
"""
from __future__ import annotations

import re
from pathlib import Path

# ── Log sanitisation ──────────────────────────────────────────────────────────

# Patterns that may contain secrets in log lines
_SECRET_RE = re.compile(
    r"""((?:token|key|secret|password|api_key|bearer|authorization|credential)
         [\s=:]+)[^\s"',;]+""",
    re.IGNORECASE | re.VERBOSE,
)


def sanitize_log_line(line: str) -> str:
    """Replace secret values with [REDACTED] in a log line."""
    return _SECRET_RE.sub(r"\1[REDACTED]", line)


def sanitize_log_lines(lines: list[str]) -> list[str]:
    return [sanitize_log_line(l) for l in lines]


# ── Path helpers ──────────────────────────────────────────────────────────────

def is_path_safe(path: str | Path) -> tuple[bool, str]:
    """Check a path without raising. Returns (ok, reason)."""
    from assetclaw_matting.config import settings
    from assetclaw_matting.services.file_store import validate_allowed_path

    try:
        resolved = Path(str(path)).resolve()
    except Exception as exc:
        return False, f"Cannot resolve path: {exc}"

    if ".." in Path(str(path)).parts:
        return False, "Path traversal not allowed"

    path_str = str(resolved)
    for pattern in settings.deny_path_patterns_list:
        if pattern.lower() in path_str.lower():
            return False, f"Path contains denied pattern: {pattern!r}"

    try:
        validate_allowed_path(resolved)
    except ValueError as exc:
        return False, str(exc)

    return True, ""


def check_path_or_raise(path: str | Path) -> Path:
    """Validate a path and return it resolved, or raise ValueError."""
    ok, reason = is_path_safe(path)
    if not ok:
        raise ValueError(reason)
    return Path(str(path)).resolve()
