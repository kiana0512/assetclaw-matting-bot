from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/skills/v1", tags=["skills"])


class SkillCallRequest(BaseModel):
    skill: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    requested_by: str = "api"


def _verify(x_skill_token: str = Header("")) -> None:
    from assetclaw_matting.config import settings

    if x_skill_token != settings.skill_api_token:
        raise HTTPException(status_code=401, detail="invalid X-Skill-Token")


@router.get("/manifest")
async def manifest() -> dict:
    from assetclaw_matting.skills.registry import get_manifest

    return get_manifest()


@router.post("/call")
async def call(body: SkillCallRequest, x_skill_token: str = Header("")) -> dict:
    _verify(x_skill_token)
    from assetclaw_matting.skills.registry import call_skill

    return call_skill(body.skill, body.arguments, requested_by=body.requested_by)
