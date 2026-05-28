from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ParsedCommand:
    name: str                         # e.g. "help", "queue", "batch_status"
    args: dict[str, str] = field(default_factory=dict)
    raw: str = ""


def parse(text: str) -> ParsedCommand:
    """Parse a text message into a structured command."""
    raw = text.strip()
    lower = raw.lower()
    tokens = raw.split()

    if not tokens:
        return ParsedCommand(name="unknown", raw=raw)

    t0 = tokens[0].lower()

    if t0 == "help":
        return ParsedCommand(name="help", raw=raw)

    if t0 == "queue":
        return ParsedCommand(name="queue", raw=raw)

    if t0 == "batch" and len(tokens) >= 2:
        t1 = tokens[1].lower()

        if t1 == "list":
            return ParsedCommand(name="batch_list", raw=raw)

        if t1 == "status" and len(tokens) >= 3:
            return ParsedCommand(
                name="batch_status",
                args={"batch_id": tokens[2]},
                raw=raw,
            )

        if t1 == "cancel" and len(tokens) >= 3:
            return ParsedCommand(
                name="batch_cancel",
                args={"batch_id": tokens[2]},
                raw=raw,
            )

        if t1 == "pause" and len(tokens) >= 3:
            return ParsedCommand(
                name="batch_pause",
                args={"batch_id": tokens[2]},
                raw=raw,
            )

        if t1 == "resume" and len(tokens) >= 3:
            return ParsedCommand(
                name="batch_resume",
                args={"batch_id": tokens[2]},
                raw=raw,
            )

    if t0 == "task" and len(tokens) >= 3 and tokens[1].lower() == "status":
        return ParsedCommand(
            name="task_status",
            args={"task_id": tokens[2]},
            raw=raw,
        )

    return ParsedCommand(name="unknown", raw=raw)
