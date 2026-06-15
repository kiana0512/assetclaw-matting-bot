from __future__ import annotations

import json
import logging
import base64
from pathlib import Path
import re
from typing import Any

import requests
from requests import HTTPError

from assetclaw_matting.brain.base import BrainProvider
from assetclaw_matting.brain.context_builder import build_memory_prompt, build_skill_manifest_prompt
from assetclaw_matting.brain.emotion_planner import plan_emotional_reply
from assetclaw_matting.brain.pre_llm_router import handle_pre_llm_message
from assetclaw_matting.brain.prompts import SYSTEM_PROMPT
from assetclaw_matting.brain.result_formatter import format_skill_results
from assetclaw_matting.brain.schemas import BrainMessage, BrainResponse, ToolCall
from assetclaw_matting.ops_trace import trace
from assetclaw_matting.progress import notify_progress
from assetclaw_matting.skills.security import redact_secrets
from assetclaw_matting.skills.translation_skills import _image_mime

log = logging.getLogger(__name__)


class LLMProxyBrain(BrainProvider):
    name = "llm_proxy"

    def is_available(self) -> bool:
        from assetclaw_matting.config import settings

        return bool(settings.llm_proxy_enabled and settings.llm_proxy_base_url and settings.llm_proxy_api_key)

    def handle_message(self, message: BrainMessage) -> BrainResponse:
        pre_llm_response = handle_pre_llm_message(self, message)
        if pre_llm_response:
            return pre_llm_response

        if message.attachments and _asks_for_visual_analysis(message.text):
            response = self._analyze_attachments(message)
            self.log_message(message, response)
            return response

        if not message.text.strip():
            response = BrainResponse(
                text="我收到了空消息。可以直接发文字指令，或发图片后说明要提取文字、翻译还是保存。",
                provider=self.name,
            )
            self.log_message(message, response)
            return response

        emotional_reply = plan_emotional_reply(message.text)
        if emotional_reply:
            response = BrainResponse(text=emotional_reply, provider=self.name)
            self.log_message(message, response)
            return response

        if not self.is_available():
            from assetclaw_matting.brain.local_command_brain import LocalCommandBrain

            return LocalCommandBrain().handle_message(message)

        raw_first = ""
        memory_prompt = build_memory_prompt(message.conversation_id)
        system_content = SYSTEM_PROMPT + "\nAvailable skills:\n" + build_skill_manifest_prompt()
        if memory_prompt:
            system_content += "\n\n" + memory_prompt
        trace(
            "brain.input",
            conversation_id=message.conversation_id,
            user_id=message.user_id,
            provider=self.name,
            text=message.text,
        )
        try:
            notify_progress("正在理解意图并选择可用技能")
            raw_first = self._complete(
                [
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": message.text},
                ],
                model_kind="tool",
            )
            parsed = self._parse_json(raw_first)
        except HTTPError as exc:
            response = BrainResponse(
                text=f"LLM Proxy 请求失败：{self._format_http_error(exc)}",
                raw={"error": str(exc)},
                provider=self.name,
            )
            self.log_message(message, response)
            return response
        except Exception as first_error:
            try:
                repaired = self._complete(
                    [
                        {"role": "system", "content": "Repair the following into valid JSON only."},
                        {"role": "user", "content": raw_first},
                    ],
                    model_kind="tool",
                )
                parsed = self._parse_json(repaired)
                raw_first = repaired
            except HTTPError as exc:
                response = BrainResponse(
                    text=f"LLM Proxy 请求失败：{self._format_http_error(exc)}",
                    raw={"error": str(exc)},
                    provider=self.name,
                )
                self.log_message(message, response)
                return response
            except Exception as repair_error:
                from assetclaw_matting.brain.local_command_brain import LocalCommandBrain

                fallback = LocalCommandBrain().handle_message(message)
                fallback.provider = self.name
                fallback.raw = {
                    "fallback": "local_command_after_invalid_json",
                    "first_error": str(first_error),
                    "repair_error": str(repair_error),
                }
                self.log_message(message, fallback)
                return fallback

        tool_calls = [ToolCall(**item) for item in parsed.get("tool_calls", [])]
        if not tool_calls:
            reply = parsed.get("text") or parsed.get("reply") or parsed.get("content") or "我理解了。"
            if _is_empty_understanding(reply):
                reply = (
                    "我刚才没有执行任何操作。"
                    "请把目标路径或文件名说完整一点，我会直接处理。"
                )
            response = BrainResponse(
                text=reply,
                tool_calls=[],
                raw={"llm_tool_json": redact_secrets(raw_first)},
                provider=self.name,
            )
            self.log_message(message, response)
            trace(
                "brain.output",
                conversation_id=message.conversation_id,
                provider=self.name,
                text=response.text,
            )
            return response
        trace(
            "brain.tool_plan",
            conversation_id=message.conversation_id,
            provider=self.name,
            tool_calls=[tc.model_dump() for tc in tool_calls],
        )
        results = self.execute_tool_calls(
            tool_calls,
            conversation_id=message.conversation_id,
            user_id=message.user_id,
        )
        notify_progress("工具执行完成，正在整理回复")
        summary = self._summarize(message.text, results)
        response = BrainResponse(
            text=summary,
            tool_calls=tool_calls,
            raw={"llm_tool_json": redact_secrets(raw_first), "skill_results": results},
            provider=self.name,
        )
        self.log_message(message, response)
        trace(
            "brain.output",
            conversation_id=message.conversation_id,
            provider=self.name,
            text=response.text,
        )
        return response

    def _endpoint(self) -> str:
        from assetclaw_matting.config import settings

        base = settings.llm_proxy_base_url.rstrip("/")
        if not settings.llm_proxy_openai_compatible:
            if base.endswith("/v1/messages"):
                return base
            if base.endswith("/v1"):
                return f"{base}/messages"
            return f"{base}/v1/messages"
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def _complete(self, messages: list[dict[str, Any]], model_kind: str) -> str:
        from assetclaw_matting.config import settings

        model = settings.llm_proxy_model
        if model_kind == "summary":
            model = settings.llm_proxy_summary_model or model
        if settings.llm_proxy_openai_compatible:
            payload = {
                "model": model,
                "messages": messages,
                "temperature": 0,
            }
            response = self._post_chat_completion(payload, auth_header="bearer")
            if response.status_code == 401:
                response = self._post_chat_completion(payload, auth_header="x-api-key")
        else:
            payload = self._anthropic_payload(model, messages)
            response = self._post_anthropic_messages(payload)
        response.raise_for_status()
        data = response.json()
        if not settings.llm_proxy_openai_compatible:
            return self._extract_anthropic_text(data)
        return data["choices"][0]["message"]["content"]

    def _anthropic_payload(self, model: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        system_parts: list[str] = []
        chat_messages: list[dict[str, str]] = []
        for message in messages:
            if message["role"] == "system":
                system_parts.append(message["content"])
            else:
                chat_messages.append(message)
        return {
            "model": model,
            "max_tokens": 2048,
            "temperature": 0,
            "system": "\n\n".join(system_parts),
            "messages": chat_messages,
        }

    def _extract_anthropic_text(self, data: dict[str, Any]) -> str:
        content = data.get("content", [])
        parts: list[str] = []
        for item in content:
            if item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "\n".join(part for part in parts if part).strip()

    def _post_chat_completion(self, payload: dict[str, Any], auth_header: str) -> requests.Response:
        from assetclaw_matting.config import settings

        headers = {"Content-Type": "application/json"}
        if auth_header == "x-api-key":
            headers["x-api-key"] = settings.llm_proxy_api_key
        else:
            headers["Authorization"] = f"Bearer {settings.llm_proxy_api_key}"
        return requests.post(
            self._endpoint(),
            headers=headers,
            json=payload,
            timeout=settings.llm_proxy_timeout_seconds,
        )

    def _post_anthropic_messages(self, payload: dict[str, Any]) -> requests.Response:
        from assetclaw_matting.config import settings

        headers = {
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        if settings.llm_proxy_auth_header == "x-api-key":
            headers["x-api-key"] = settings.llm_proxy_api_key
        else:
            headers["Authorization"] = f"Bearer {settings.llm_proxy_api_key}"
        return requests.post(
            self._endpoint(),
            headers=headers,
            json=payload,
            timeout=settings.llm_proxy_timeout_seconds,
        )

    def _format_http_error(self, exc: HTTPError) -> str:
        response = exc.response
        if response is None:
            return str(exc)
        detail = response.text[:300] if response.text else response.reason
        if response.status_code in {401, 403}:
            return (
                f"{response.status_code} 鉴权失败。请检查 LLM_PROXY_API_KEY、"
                f"LLM_PROXY_BASE_URL 和该 key 是否允许当前接口。详情：{redact_secrets(detail)}"
            )
        if response.status_code == 404:
            return f"404 接口不存在。请检查 base url 是否应为 /v1 或完整 /v1/chat/completions。详情：{detail}"
        return f"{response.status_code} {redact_secrets(detail)}"

    def _parse_json(self, raw: str) -> dict[str, Any]:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if not match:
                raise
            data = json.loads(match.group(0))
        if not isinstance(data.get("tool_calls", []), list):
            raise ValueError("tool_calls must be a list")
        return data

    def _summarize(self, user_text: str, results: list[dict[str, Any]]) -> str:
        return format_skill_results(results)

    def _analyze_attachments(self, message: BrainMessage) -> BrainResponse:
        if not self.is_available():
            return BrainResponse(text="我已经收到附件，但当前 LLM Proxy 没配置，暂时不能做视觉理解。", provider=self.name)
        image_paths = [
            Path(str(item["local_path"]))
            for item in message.attachments
            if item.get("local_path") and Path(str(item["local_path"])).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}
        ]
        if not image_paths:
            return BrainResponse(text="我已经收到附件。目前只支持把图片交给 LLM 分析，视频先支持保存、查看信息和发回。", provider=self.name)
        try:
            text = self._complete_multimodal(message.text or "请简要描述这张图片。", image_paths[:4])
            return BrainResponse(text=text or "没有识别到有效内容。", provider=self.name)
        except Exception as exc:
            return BrainResponse(text=f"图片分析失败：{self._format_http_error(exc) if isinstance(exc, HTTPError) else exc}", provider=self.name)

    def _complete_multimodal(self, prompt: str, image_paths: list[Path]) -> str:
        from assetclaw_matting.config import settings

        if settings.llm_proxy_openai_compatible:
            content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
            for path in image_paths:
                mime = _image_mime(path)
                data = base64.b64encode(path.read_bytes()).decode("ascii")
                content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}})
            payload = {
                "model": settings.llm_proxy_complex_model or settings.llm_proxy_model,
                "messages": [{"role": "user", "content": content}],
                "temperature": 0,
            }
            response = self._post_chat_completion(payload, auth_header="bearer")
            if response.status_code == 401:
                response = self._post_chat_completion(payload, auth_header="x-api-key")
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]

        content = [{"type": "text", "text": prompt}]
        for path in image_paths:
            mime = _image_mime(path)
            data = base64.b64encode(path.read_bytes()).decode("ascii")
            content.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}})
        payload = {
            "model": settings.llm_proxy_complex_model or settings.llm_proxy_model,
            "max_tokens": 1200,
            "temperature": 0,
            "messages": [{"role": "user", "content": content}],
        }
        response = self._post_anthropic_messages(payload)
        response.raise_for_status()
        return self._extract_anthropic_text(response.json())


def _is_empty_understanding(text: str) -> bool:
    normalized = re.sub(r"[\s。.!！]+", "", text.strip().lower())
    return normalized in {"我理解了", "理解了", "明白了", "好的", "ok", "收到"}


def _asks_for_visual_analysis(text: str) -> bool:
    return any(kw in text for kw in ("分析", "理解", "识别", "看看图", "看图", "看一下", "看到", "图里", "画面", "描述", "是什么", "具体内容", "表情包", "附件内容"))
