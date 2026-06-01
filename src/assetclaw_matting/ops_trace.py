from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import threading
from typing import Any

from assetclaw_matting.config import settings
from assetclaw_matting.skills.security import redact_secrets

_lock = threading.Lock()


def trace(event: str, **fields: Any) -> None:
    """Append one operator-facing line to logs/conversation.log."""
    settings.ensure_dirs()
    path: Path = settings.log_dir / "conversation.log"
    payload = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "event": event,
        **fields,
    }
    line = redact_secrets(json.dumps(payload, ensure_ascii=False, default=str))
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
