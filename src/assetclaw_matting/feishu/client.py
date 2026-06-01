from __future__ import annotations

import json
import logging
from pathlib import Path
import threading
import time

import requests

from assetclaw_matting.config import settings

log = logging.getLogger(__name__)
FEISHU_BASE = "https://open.feishu.cn/open-apis"


class FeishuClient:
    def __init__(self) -> None:
        self._token = ""
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def get_tenant_access_token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._expires_at - 60:
                return self._token
            response = requests.post(
                f"{FEISHU_BASE}/auth/v3/tenant_access_token/internal",
                json={"app_id": settings.feishu_app_id, "app_secret": settings.feishu_app_secret},
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
            if data.get("code") != 0:
                raise RuntimeError(f"get_tenant_access_token failed: {data.get('msg')}")
            self._token = data["tenant_access_token"]
            self._expires_at = time.time() + data.get("expire", 7200)
            return self._token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.get_tenant_access_token()}"}

    def reply_text(self, message_id: str, text: str) -> None:
        if not message_id:
            return
        payload = {"msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)}
        response = requests.post(
            f"{FEISHU_BASE}/im/v1/messages/{message_id}/reply",
            headers=self._headers(),
            json=payload,
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            log.error("feishu reply failed code=%s msg=%s", data.get("code"), data.get("msg"))

    def send_text_to_chat(self, chat_id: str, text: str) -> None:
        if not chat_id:
            return
        payload = {
            "receive_id": chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
        response = requests.post(
            f"{FEISHU_BASE}/im/v1/messages?receive_id_type=chat_id",
            headers=self._headers(),
            json=payload,
            timeout=15,
        )
        response.raise_for_status()

    def upload_file(self, path: Path, file_name: str | None = None) -> str:
        sent_name = file_name or path.name
        with path.open("rb") as f:
            response = requests.post(
                f"{FEISHU_BASE}/im/v1/files",
                headers=self._headers(),
                data={"file_type": "stream", "file_name": sent_name},
                files={"file": (sent_name, f)},
                timeout=60,
            )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            raise RuntimeError(f"upload_file failed: {data.get('msg')}")
        return data["data"]["file_key"]

    def send_file_to_chat(self, chat_id: str, path: Path, file_name: str | None = None) -> None:
        file_key = self.upload_file(path, file_name)
        payload = {
            "receive_id": chat_id,
            "msg_type": "file",
            "content": json.dumps({"file_key": file_key}, ensure_ascii=False),
        }
        response = requests.post(
            f"{FEISHU_BASE}/im/v1/messages?receive_id_type=chat_id",
            headers=self._headers(),
            json=payload,
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            raise RuntimeError(f"send_file_to_chat failed: {data.get('msg')}")


feishu_client = FeishuClient()
