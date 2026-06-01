from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from assetclaw_matting.feishu.event_handler import handle_event

router = APIRouter(prefix="/feishu", tags=["feishu"])


@router.post("/events")
async def feishu_events(request: Request) -> dict:
    body = await request.json()
    result = handle_event(body)
    if result.get("error") == "invalid token":
        raise HTTPException(status_code=403, detail="invalid token")
    return result
