from __future__ import annotations

import base64
import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from assetclaw_matting.brain.schemas import BrainMessage

router = APIRouter(prefix="/brain", tags=["brain"])


class BrainTestRequest(BaseModel):
    text: str
    conversation_id: str = "test"
    user_id: str = "local"
    source: str = "webui"
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    async_mode: bool = True


@router.post("/test")
async def brain_test(body: BrainTestRequest) -> dict:
    attachments = _prepare_webui_attachments(body.attachments, body.conversation_id or "test")
    message = BrainMessage(
        channel="brain_test",
        conversation_id=body.conversation_id or "test",
        user_id=body.user_id or body.source or "local",
        text=body.text,
        attachments=attachments,
    )
    if body.async_mode:
        from assetclaw_matting.services.agent_job_queue import enqueue_brain_job

        job = enqueue_brain_job(
            message,
            context={
                "channel": "brain_test",
                "user_id": body.user_id or body.source or "local",
                "conversation_id": body.conversation_id or "test",
                "trace_id": "webui",
            },
        )
        return {
            "ok": True,
            "queued": True,
            "job_id": job["job_id"],
            "status": job["status"],
            "position": job.get("position"),
            "conversation_id": job["conversation_id"],
            "message": "已进入 Agent 后台队列，前端可以继续刷新状态。",
        }

    from assetclaw_matting.brain import router as brain_router

    response = brain_router.handle_message(message)
    return response.model_dump()


@router.get("/jobs")
async def brain_jobs(conversation_id: str = "", limit: int = 20) -> dict:
    from assetclaw_matting.services.agent_job_queue import list_brain_jobs, queue_snapshot

    queue, items = queue_snapshot(), list_brain_jobs(conversation_id, limit)
    return {"ok": True, "queue": queue, "items": items}


@router.get("/jobs/{job_id}")
async def brain_job(job_id: str) -> dict:
    from assetclaw_matting.services.agent_job_queue import get_brain_job

    job = get_brain_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job


def _prepare_webui_attachments(items: list[dict[str, Any]], conversation_id: str) -> list[dict[str, Any]]:
    if not items:
        return []
    from assetclaw_matting.config import settings

    day = datetime.now().strftime("%Y-%m-%d")
    safe_conversation = re.sub(r"[^A-Za-z0-9_.-]+", "_", conversation_id or "test")[:80]
    target_dir = Path(settings.storage_dir) / "webui_uploads" / day / safe_conversation
    target_dir.mkdir(parents=True, exist_ok=True)
    prepared: list[dict[str, Any]] = []
    for index, item in enumerate(items[:8], start=1):
        name = _safe_file_name(str(item.get("name") or item.get("file_name") or f"webui_attachment_{index}.bin"))
        target = target_dir / name
        saved = False
        error = ""
        try:
            if item.get("data_url"):
                raw = str(item["data_url"])
                encoded = raw.split(",", 1)[1] if "," in raw else raw
                data = base64.b64decode(encoded, validate=False)
                if len(data) > 25 * 1024 * 1024:
                    raise ValueError("附件超过 25MB，WebUI 为了避免卡住没有写入。请改用本机路径或飞书附件。")
                target.write_bytes(data)
                saved = True
            elif item.get("text") is not None:
                target.write_text(str(item.get("text") or ""), encoding="utf-8")
                saved = True
        except Exception as exc:
            error = str(exc)
        payload = {
            "type": item.get("type") or item.get("mime") or "file",
            "file_name": name,
            "size": item.get("size") or (target.stat().st_size if saved and target.exists() else 0),
            "source": "external_webui",
            "downloaded": saved,
            "local_path": str(target) if saved else "",
        }
        if error:
            payload["error"] = error
        prepared.append(payload)
    return prepared


def _safe_file_name(raw: str) -> str:
    name = Path(raw.replace("\\", "/")).name or "webui_attachment.bin"
    return re.sub(r"[^A-Za-z0-9_.\-\u4e00-\u9fff]+", "_", name)[:120]
