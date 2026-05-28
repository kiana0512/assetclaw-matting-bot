from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import requests

from assetclaw_matting.config import settings

log = logging.getLogger(__name__)

_FEISHU_BASE = "https://open.feishu.cn/open-apis"


class FeishuClient:
    """Thin wrapper around the Feishu Open Platform messaging API."""

    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._token_expires: float = 0.0
        self._lock = threading.Lock()

    # ── Auth ──────────────────────────────────────────────────────────────────

    def get_tenant_access_token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._token_expires - 60:
                return self._token
            resp = requests.post(
                f"{_FEISHU_BASE}/auth/v3/tenant_access_token/internal",
                json={
                    "app_id": settings.feishu_app_id,
                    "app_secret": settings.feishu_app_secret,
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(
                    f"get_tenant_access_token failed: {data.get('msg')}"
                )
            self._token = data["tenant_access_token"]
            self._token_expires = time.time() + data.get("expire", 7200)
            return self._token  # type: ignore[return-value]

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.get_tenant_access_token()}"}

    # ── Messaging ─────────────────────────────────────────────────────────────

    def reply_text(self, message_id: str, text: str) -> None:
        if not message_id:
            return
        resp = requests.post(
            f"{_FEISHU_BASE}/im/v1/messages/{message_id}/reply",
            headers=self._auth_headers(),
            json={
                "msg_type": "text",
                "content": f'{{"text":"{_escape(text)}"}}',
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            log.error("reply_text failed code=%s msg=%s", data.get("code"), data.get("msg"))

    def send_text_to_chat(self, chat_id: str, text: str) -> None:
        if not chat_id:
            return
        resp = requests.post(
            f"{_FEISHU_BASE}/im/v1/messages?receive_id_type=chat_id",
            headers=self._auth_headers(),
            json={
                "receive_id": chat_id,
                "msg_type": "text",
                "content": f'{{"text":"{_escape(text)}"}}',
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            log.error(
                "send_text_to_chat failed code=%s msg=%s",
                data.get("code"), data.get("msg"),
            )


def _escape(s: str) -> str:
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "")
    )


feishu_client = FeishuClient()
