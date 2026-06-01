from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from assetclaw_matting.brain.schemas import BrainMessage

router = APIRouter(prefix="/brain", tags=["brain"])


class BrainTestRequest(BaseModel):
    text: str


@router.post("/test")
async def brain_test(body: BrainTestRequest) -> dict:
    from assetclaw_matting.brain import router as brain_router

    response = brain_router.handle_message(
        BrainMessage(channel="brain_test", conversation_id="test", user_id="local", text=body.text)
    )
    return response.model_dump()
