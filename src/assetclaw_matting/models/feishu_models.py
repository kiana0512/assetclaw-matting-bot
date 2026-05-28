from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class FeishuEventHeader(BaseModel):
    event_id: str = ""
    event_type: str = ""
    create_time: str = ""
    token: str = ""
    app_id: str = ""
    tenant_key: str = ""


class FeishuSender(BaseModel):
    sender_id: Optional[dict[str, Any]] = None
    sender_type: str = ""
    tenant_key: str = ""


class FeishuMessage(BaseModel):
    message_id: str = ""
    root_id: Optional[str] = None
    parent_id: Optional[str] = None
    create_time: str = ""
    chat_id: str = ""
    chat_type: str = ""
    message_type: str = ""
    content: str = "{}"
    mentions: Optional[list[dict[str, Any]]] = None


class FeishuMessageEvent(BaseModel):
    sender: Optional[FeishuSender] = None
    message: Optional[FeishuMessage] = None


class FeishuEventEnvelope(BaseModel):
    schema_version: str = "2.0"
    header: Optional[FeishuEventHeader] = None
    event: Optional[FeishuMessageEvent] = None

    # URL verification fields (present only for challenge)
    type: Optional[str] = None
    token: Optional[str] = None
    challenge: Optional[str] = None

    class Config:
        populate_by_name = True
