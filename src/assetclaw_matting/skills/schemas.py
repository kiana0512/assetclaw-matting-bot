"""Skill Gateway data models."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class SkillCallRequest(BaseModel):
    skill: str
    arguments: dict[str, Any] = {}
    request_id: str = ""
    requested_by: str = "api"


class SkillCallResponse(BaseModel):
    ok: bool
    skill: str
    result: Optional[dict[str, Any]] = None
    message: str = ""
    error: Optional[str] = None
    request_id: str = ""
