from __future__ import annotations

from contextvars import ContextVar
from typing import Callable

_sender: ContextVar[Callable[[str], None] | None] = ContextVar("progress_sender", default=None)


def set_progress_sender(sender: Callable[[str], None] | None):
    return _sender.set(sender)


def reset_progress_sender(token) -> None:
    _sender.reset(token)


def notify_progress(text: str) -> None:
    sender = _sender.get()
    if not sender:
        return
    sender(text)
