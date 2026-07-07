from __future__ import annotations

import json
import logging
import mimetypes
from pathlib import Path
import threading
import time
from typing import Literal

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
                data={"file_type": _feishu_file_type(sent_name), "file_name": sent_name},
                files={"file": (sent_name, f, _guess_mime_type(sent_name))},
                timeout=60,
            )
        self._raise_for_api_error(response, "upload_file")
        data = response.json()
        if data.get("code") != 0:
            raise RuntimeError(f"upload_file failed: code={data.get('code')} msg={data.get('msg')} error={data.get('error')}")
        return data["data"]["file_key"]

    def send_file_to_chat(self, chat_id: str, path: Path, file_name: str | None = None) -> None:
        try:
            file_key = self.upload_file(path, file_name)
        except requests.HTTPError as exc:
            if not _is_message_file_size_error(exc):
                raise
            drive_file = self.upload_drive_file(path, file_name)
            url = str(drive_file.get("url") or "")
            if not url:
                raise RuntimeError(f"drive upload succeeded but url is empty: {drive_file}") from exc
            self.send_text_to_chat(chat_id, f"文件已生成：{file_name or path.name}\n{url}")
            return
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
        self._raise_for_api_error(response, "send_file_to_chat")
        data = response.json()
        if data.get("code") != 0:
            raise RuntimeError(f"send_file_to_chat failed: code={data.get('code')} msg={data.get('msg')} error={data.get('error')}")

    def upload_drive_file(self, path: Path, file_name: str | None = None) -> dict[str, str]:
        sent_name = file_name or path.name
        size = path.stat().st_size
        if size > 20 * 1024 * 1024:
            return self.upload_drive_file_chunked(path, sent_name)
        with path.open("rb") as f:
            response = requests.post(
                f"{FEISHU_BASE}/drive/v1/files/upload_all",
                headers=self._headers(),
                data={
                    "file_name": sent_name,
                    "parent_type": "explorer",
                    "parent_node": "",
                    "size": str(size),
                },
                files={"file": (sent_name, f, _guess_mime_type(sent_name))},
                timeout=300,
            )
        self._raise_for_api_error(response, "upload_drive_file")
        data = response.json()
        if data.get("code") != 0:
            raise RuntimeError(f"upload_drive_file failed: code={data.get('code')} msg={data.get('msg')} error={data.get('error')}")
        payload = data.get("data") or {}
        return {
            "file_token": str(payload.get("file_token") or ""),
            "url": str(payload.get("url") or ""),
            "version": str(payload.get("version") or ""),
        }

    def upload_drive_file_chunked(self, path: Path, file_name: str | None = None) -> dict[str, str]:
        sent_name = file_name or path.name
        size = path.stat().st_size
        prepare = requests.post(
            f"{FEISHU_BASE}/drive/v1/files/upload_prepare",
            headers=self._headers(),
            data={
                "file_name": sent_name,
                "parent_type": "explorer",
                "parent_node": "",
                "size": str(size),
            },
            timeout=60,
        )
        self._raise_for_api_error(prepare, "upload_drive_file_prepare")
        prepare_data = prepare.json()
        if prepare_data.get("code") != 0:
            raise RuntimeError(
                f"upload_drive_file_prepare failed: code={prepare_data.get('code')} "
                f"msg={prepare_data.get('msg')} error={prepare_data.get('error')}"
            )
        payload = prepare_data.get("data") or {}
        upload_id = str(payload.get("upload_id") or "")
        block_size = int(payload.get("block_size") or 4 * 1024 * 1024)
        block_num = int(payload.get("block_num") or 0)
        if not upload_id or block_size <= 0 or block_num <= 0:
            raise RuntimeError(f"invalid drive upload_prepare response: {payload}")

        with path.open("rb") as f:
            for seq in range(block_num):
                chunk = f.read(block_size)
                if not chunk:
                    break
                part = requests.post(
                    f"{FEISHU_BASE}/drive/v1/files/upload_part",
                    headers=self._headers(),
                    data={"upload_id": upload_id, "seq": str(seq), "size": str(len(chunk))},
                    files={"file": (sent_name, chunk, _guess_mime_type(sent_name))},
                    timeout=120,
                )
                self._raise_for_api_error(part, "upload_drive_file_part")
                part_data = part.json()
                if part_data.get("code") != 0:
                    raise RuntimeError(
                        f"upload_drive_file_part failed: seq={seq} code={part_data.get('code')} "
                        f"msg={part_data.get('msg')} error={part_data.get('error')}"
                    )

        finish = requests.post(
            f"{FEISHU_BASE}/drive/v1/files/upload_finish",
            headers=self._headers(),
            data={"upload_id": upload_id, "block_num": str(block_num)},
            timeout=120,
        )
        self._raise_for_api_error(finish, "upload_drive_file_finish")
        finish_data = finish.json()
        if finish_data.get("code") != 0:
            raise RuntimeError(
                f"upload_drive_file_finish failed: code={finish_data.get('code')} "
                f"msg={finish_data.get('msg')} error={finish_data.get('error')}"
            )
        result = finish_data.get("data") or {}
        return {
            "file_token": str(result.get("file_token") or ""),
            "url": str(result.get("url") or ""),
            "version": str(result.get("version") or ""),
        }

    def download_message_resource(
        self,
        message_id: str,
        resource_key: str,
        target_path: Path,
        resource_type: Literal["image", "file", "video", "audio", "media"] | str = "file",
    ) -> None:
        if not message_id or not resource_key:
            raise ValueError("message_id and resource_key are required")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        response = requests.get(
            f"{FEISHU_BASE}/im/v1/messages/{message_id}/resources/{resource_key}",
            headers=self._headers(),
            params={"type": resource_type},
            stream=True,
            timeout=60,
        )
        self._write_download_response(response, target_path, error_label="download_message_resource")

    def download_image(self, image_key: str, target_path: Path) -> None:
        if not image_key:
            raise ValueError("image_key is required")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        response = requests.get(
            f"{FEISHU_BASE}/im/v1/images/{image_key}",
            headers=self._headers(),
            params={"image_type": "message"},
            stream=True,
            timeout=60,
        )
        self._write_download_response(response, target_path, error_label="download_image")

    def _write_download_response(self, response: requests.Response, target_path: Path, error_label: str) -> None:
        self._raise_for_download_error(response, error_label)
        content_type = response.headers.get("Content-Type", "")
        if "json" in content_type.lower():
            data = response.json()
            if data.get("code") != 0:
                raise RuntimeError(f"{error_label} failed: {data.get('msg') or data}")
            raise RuntimeError(f"{error_label} returned json instead of binary data")
        with target_path.open("wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)

    def _raise_for_download_error(self, response: requests.Response, error_label: str) -> None:
        if response.status_code < 400:
            return
        detail = response.text[:1200] if response.text else response.reason
        message = f"{error_label} failed: {response.status_code} {detail}"
        raise requests.HTTPError(message, response=response)

    def _raise_for_api_error(self, response: requests.Response, error_label: str) -> None:
        if response.status_code < 400:
            return
        detail = response.text[:2000] if response.text else response.reason
        raise requests.HTTPError(f"{error_label} failed: {response.status_code} {detail}", response=response)

    def upload_image(self, path: Path) -> str:
        with path.open("rb") as f:
            response = requests.post(
                f"{FEISHU_BASE}/im/v1/images",
                headers=self._headers(),
                data={"image_type": "message"},
                files={"image": (path.name, f)},
                timeout=60,
            )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            raise RuntimeError(f"upload_image failed: {data.get('msg')}")
        return data["data"]["image_key"]

    def send_image_to_chat(self, chat_id: str, path: Path) -> None:
        image_key = self.upload_image(path)
        payload = {
            "receive_id": chat_id,
            "msg_type": "image",
            "content": json.dumps({"image_key": image_key}, ensure_ascii=False),
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
            raise RuntimeError(f"send_image_to_chat failed: {data.get('msg')}")


feishu_client = FeishuClient()


def _feishu_file_type(file_name: str) -> str:
    suffix = Path(file_name).suffix.lower()
    if suffix == ".mp4":
        return "mp4"
    if suffix == ".opus":
        return "opus"
    if suffix in {".doc", ".docx"}:
        return "doc"
    if suffix in {".xls", ".xlsx"}:
        return "xls"
    if suffix in {".ppt", ".pptx"}:
        return "ppt"
    if suffix == ".pdf":
        return "pdf"
    return "stream"


def _guess_mime_type(file_name: str) -> str:
    suffix = Path(file_name).suffix.lower()
    if suffix == ".zip":
        return "application/zip"
    return mimetypes.guess_type(file_name)[0] or "application/octet-stream"


def _is_message_file_size_error(exc: requests.HTTPError) -> bool:
    response = exc.response
    if response is None:
        return False
    try:
        data = response.json()
    except Exception:
        return "234006" in str(exc)
    return str(data.get("code") or "") == "234006"
