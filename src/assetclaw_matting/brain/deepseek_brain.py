from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path
from typing import Any

import requests
from requests import HTTPError, Timeout

from assetclaw_matting.brain.llm_proxy_brain import LLMProxyBrain
from assetclaw_matting.skills.security import redact_secrets
from assetclaw_matting.skills.translation_skills import _image_mime

log = logging.getLogger(__name__)


class DeepSeekBrain(LLMProxyBrain):
    name = "deepseek"

    def is_available(self) -> bool:
        from assetclaw_matting.config import settings

        return bool(settings.deepseek_api_key and settings.deepseek_base_url)

    def _endpoint(self) -> str:
        from assetclaw_matting.config import settings

        base = settings.deepseek_base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def _complete(self, messages: list[dict[str, Any]], model_kind: str) -> str:
        json_mode = model_kind == "tool"
        payload = self._build_payload(messages, purpose=model_kind, json_mode=json_mode)
        response = self._post_chat_completion(payload)
        if json_mode and response.status_code == 400 and "response_format" in payload:
            payload.pop("response_format", None)
            response = self._post_chat_completion(payload)
        response.raise_for_status()
        return self._extract_message_content(response.json())

    def _build_payload(self, messages: list[dict[str, Any]], purpose: str = "router", json_mode: bool = False) -> dict[str, Any]:
        from assetclaw_matting.config import settings

        model = settings.deepseek_model
        if purpose in {"tool", "router"}:
            model = settings.deepseek_router_model or model
        elif purpose == "summary":
            model = settings.deepseek_summary_model or model

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": bool(settings.deepseek_stream),
            "temperature": float(settings.deepseek_temperature),
        }
        thinking_type = (settings.deepseek_thinking_type or "disabled").strip().lower()
        if thinking_type in {"enabled", "disabled"}:
            payload["thinking"] = {"type": thinking_type}
        if thinking_type == "enabled":
            effort = (settings.deepseek_reasoning_effort or "medium").strip().lower()
            if effort not in {"low", "medium", "high"}:
                effort = "medium"
            payload["reasoning_effort"] = effort
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _post_chat_completion(self, payload: dict[str, Any]) -> requests.Response:
        from assetclaw_matting.config import settings

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.deepseek_api_key}",
        }
        attempts = max(0, int(settings.deepseek_max_retries)) + 1
        last_exc: Exception | None = None
        for index in range(attempts):
            started = time.perf_counter()
            try:
                response = requests.post(
                    self._endpoint(),
                    headers=headers,
                    json=payload,
                    timeout=settings.deepseek_timeout_seconds,
                )
                log.debug(
                    "deepseek.chat status=%s model=%s latency_ms=%s",
                    response.status_code,
                    payload.get("model"),
                    int((time.perf_counter() - started) * 1000),
                )
                if response.status_code in {429, 500, 502, 503, 504} and index < attempts - 1:
                    time.sleep(min(2 ** index, 5))
                    continue
                return response
            except Timeout as exc:
                last_exc = exc
                if index >= attempts - 1:
                    raise
                time.sleep(min(2 ** index, 5))
        assert last_exc is not None
        raise last_exc

    def _extract_message_content(self, data: dict[str, Any]) -> str:
        choices = data.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            normalized = []
            for item in tool_calls:
                fn = item.get("function") or {}
                raw_args = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except Exception:
                    args = {}
                normalized.append({"name": fn.get("name") or item.get("name") or "", "arguments": args})
            return json.dumps({"type": "tool_calls", "tool_calls": normalized}, ensure_ascii=False)
        return str(message.get("content") or "").strip()

    def _parse_json(self, raw: str) -> dict[str, Any]:
        parsed = super()._parse_json(raw)
        return _normalize_router_payload(parsed)

    def _format_http_error(self, exc: HTTPError) -> str:
        response = exc.response
        if response is None:
            return str(exc)
        detail = response.text[:500] if response.text else response.reason
        if response.status_code == 401:
            return f"DeepSeek 401 鉴权失败。请检查 DEEPSEEK_API_KEY。详情：{redact_secrets(detail)}"
        if response.status_code == 402:
            return f"DeepSeek 402 余额不足或计费不可用。请检查平台充值/额度。详情：{redact_secrets(detail)}"
        if response.status_code == 404:
            return f"DeepSeek 404 接口或模型不存在。请检查 DEEPSEEK_BASE_URL 和模型名。详情：{redact_secrets(detail)}"
        if response.status_code == 429:
            return f"DeepSeek 429 触发限流。请稍后重试或调低频率。详情：{redact_secrets(detail)}"
        return f"DeepSeek {response.status_code} {redact_secrets(detail)}"

    def _complete_multimodal(self, prompt: str, image_paths: list[Path]) -> str:
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for path in image_paths:
            mime = _image_mime(path)
            data = base64.b64encode(path.read_bytes()).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}})
        payload = self._build_payload([{"role": "user", "content": content}], purpose="general", json_mode=False)
        response = self._post_chat_completion(payload)
        response.raise_for_status()
        return self._extract_message_content(response.json())


def _normalize_router_payload(parsed: dict[str, Any]) -> dict[str, Any]:
    kind = str(parsed.get("type") or "").strip().lower()
    if kind == "final":
        return {"tool_calls": [], "text": parsed.get("content") or parsed.get("text") or ""}
    raw_tool_calls = parsed.get("tool_calls") or []
    normalized: list[dict[str, Any]] = []
    for item in raw_tool_calls:
        if not isinstance(item, dict):
            continue
        name = item.get("skill") or item.get("name")
        args = item.get("arguments") or {}
        if name:
            normalized.append({"skill": str(name), "arguments": args if isinstance(args, dict) else {}})
    text = parsed.get("text") or parsed.get("content") or parsed.get("reply") or ""
    return {"tool_calls": normalized, "text": text}
