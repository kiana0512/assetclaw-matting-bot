from __future__ import annotations

from pathlib import Path
import re


SECRET_RE = re.compile(
    r"((?:token|secret|api[_-]?key|authorization|bearer|password)\s*[:=]\s*)[^\s,;]+",
    re.IGNORECASE,
)


def normalize_path(path: str | Path) -> Path:
    raw = str(path).strip().strip('"')
    if not raw:
        raise ValueError("path is required")
    if ".." in Path(raw).parts:
        raise ValueError("path traversal is not allowed")
    return Path(raw).expanduser().resolve()


def has_denied_pattern(path: str | Path) -> bool:
    from assetclaw_matting.config import settings

    normalized = str(path).replace("/", "\\").lower()
    return any(pattern.lower() in normalized for pattern in settings.deny_path_patterns_list)


def is_under_allowed_roots(path: str | Path) -> bool:
    from assetclaw_matting.config import settings

    normalized = str(Path(path).resolve()).replace("/", "\\").lower()
    for root in settings.allowed_roots_list:
        root_path = _normalize_allowed_root(root)
        if normalized == root_path or normalized.startswith(root_path.rstrip("\\") + "\\"):
            return True
    return False


def _normalize_allowed_root(root: str) -> str:
    value = root.strip().replace("/", "\\")
    if re.fullmatch(r"[A-Za-z]:", value):
        value = value + "\\"
    return str(Path(value).resolve()).replace("/", "\\").lower()


def validate_path(path: str | Path, must_exist: bool = False) -> Path:
    resolved = normalize_path(path)
    if has_denied_pattern(resolved):
        raise PermissionError(f"path is denied: {redact_secrets(str(path))}")
    if not is_under_allowed_roots(resolved):
        raise PermissionError(f"path is outside ALLOWED_ROOTS: {redact_secrets(str(path))}")
    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"path does not exist: {redact_secrets(str(path))}")
    return resolved


def redact_secrets(text: object) -> str:
    return SECRET_RE.sub(r"\1[REDACTED]", str(text))


sanitize_log_line = redact_secrets
