from __future__ import annotations


def queue_status() -> dict:
    return {"ok": True, "status": "idle", "queued": 0, "running": 0, "partial": True}
