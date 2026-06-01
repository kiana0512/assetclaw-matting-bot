from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_context: ContextVar[dict[str, Any]] = ContextVar("assetclaw_runtime_context", default={})


def set_runtime_context(**fields: Any):
    return _context.set(dict(fields))


def reset_runtime_context(token) -> None:
    _context.reset(token)


def get_runtime_context() -> dict[str, Any]:
    return dict(_context.get() or {})
