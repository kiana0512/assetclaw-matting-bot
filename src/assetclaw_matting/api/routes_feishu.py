from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse

from assetclaw_matting.feishu.event_handler import handle_event

log = logging.getLogger(__name__)
router = APIRouter(prefix="/feishu", tags=["feishu"])


@router.post("/events")
async def feishu_events(
    request: Request,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    """Receive Feishu event callbacks.

    Must return quickly: URL-verification and challenge are handled synchronously;
    all other processing is delegated to a background task so the 3-second Feishu
    timeout is never hit.
    """
    try:
        raw: dict[str, Any] = await request.json()
    except Exception:
        log.warning("Failed to parse Feishu event body")
        return JSONResponse(status_code=400, content={"error": "bad json"})

    # URL verification must be synchronous
    if raw.get("type") == "url_verification":
        from assetclaw_matting.feishu.event_handler import _handle_url_verification
        return JSONResponse(content=_handle_url_verification(raw))

    # All real events are processed in the background
    background_tasks.add_task(_process_event, raw)
    return JSONResponse(content={"ok": True})


def _process_event(raw: dict[str, Any]) -> None:
    try:
        handle_event(raw)
    except Exception:
        log.exception("Error processing Feishu event")
