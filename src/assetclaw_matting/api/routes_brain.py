from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

log = logging.getLogger(__name__)
router = APIRouter(prefix="/brain", tags=["brain"])


class BrainTestRequest(BaseModel):
    text: str


@router.post("/test")
async def brain_test(body: BrainTestRequest) -> JSONResponse:
    """Test the Brain Router directly, without going through Feishu.

    Useful for validating: LLM Proxy Brain -> skill calls -> results.

    Example:
        POST /brain/test
        {"text": "看看 E 盘有哪些文件"}
    """
    from assetclaw_matting.brain.schemas import BrainMessage
    from assetclaw_matting.brain import router as brain_router

    msg = BrainMessage(
        channel="api_test",
        conversation_id="test",
        user_id="test",
        text=body.text,
    )
    try:
        response = brain_router.handle_message(msg)
        return JSONResponse(content={"ok": True, "response": response.text, "provider": response.provider})
    except Exception as exc:
        log.exception("Brain test failed")
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})
