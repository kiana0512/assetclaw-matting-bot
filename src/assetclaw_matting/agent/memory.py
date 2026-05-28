"""Simple in-process conversation memory (per-chat context window)."""
from __future__ import annotations

from collections import defaultdict
from typing import Any


class ConversationMemory:
    def __init__(self, max_turns: int = 10) -> None:
        self._max_turns = max_turns
        self._history: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def add(self, chat_id: str, role: str, content: str) -> None:
        history = self._history[chat_id]
        history.append({"role": role, "content": content})
        if len(history) > self._max_turns * 2:
            # Keep only the most recent turns
            self._history[chat_id] = history[-(self._max_turns * 2):]

    def get_messages(self, chat_id: str) -> list[dict[str, Any]]:
        return list(self._history.get(chat_id, []))

    def clear(self, chat_id: str) -> None:
        self._history.pop(chat_id, None)


memory = ConversationMemory()
