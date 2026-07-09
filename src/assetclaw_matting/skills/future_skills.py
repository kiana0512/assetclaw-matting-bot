from __future__ import annotations

from typing import Any


def not_implemented(**_: Any) -> dict[str, Any]:
    return {"ok": False, "implemented": False, "message": "reserved future skill"}
