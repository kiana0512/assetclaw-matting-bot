"""LLM Proxy Brain — OpenAI-compatible HTTP API.

Recommended default brain when you have a company LLM Proxy API key.
Converts natural language to skill calls via structured JSON output.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from assetclaw_matting.brain.base import BrainProvider
from assetclaw_matting.brain.schemas import BrainContext, BrainMessage, BrainResponse, BrainToolCall

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are the cloud brain for AssetClaw Win3090 Skill Node.

ROLE:
- Understand natural language from users and convert to skill calls
- You ONLY interact with the Win3090 machine through registered skills
- You CANNOT execute shell commands
- You CANNOT read .env, .ssh, secrets, system directories
- You CANNOT delete files
- You are running in the cloud, NOT on the GPU machine
- The 3090 GPU is reserved exclusively for ComfyUI tasks

CURRENT MACHINE STATE:
{machine_state}

{skills_block}

{sop_summary}

{security_summary}

OUTPUT FORMAT (JSON only, no markdown fences):
{{
  "reply": "user-facing reply in the same language as the user",
  "tool_calls": [
    {{
      "skill": "skill.name",
      "arguments": {{"key": "value"}},
      "requires_confirmation": false
    }}
  ]
}}

If no skill is needed, return {{"reply": "...", "tool_calls": []}}.
Always reply in the same language the user used.
"""


class LLMProxyBrain(BrainProvider):
    name = "llm_proxy"

    def is_available(self) -> bool:
        from assetclaw_matting.config import settings
        return (
            settings.llm_proxy_enabled
            and bool(settings.llm_proxy_base_url)
            and bool(settings.llm_proxy_api_key)
        )

    def handle_message(
        self, message: BrainMessage, context: BrainContext
    ) -> BrainResponse:
        from assetclaw_matting.config import settings
        from assetclaw_matting.brain.context_builder import (
            build_skills_prompt_block, build_sop_summary
        )

        system_prompt = _SYSTEM_PROMPT.format(
            machine_state=(
                f"queue: {context.queue_summary}\n"
                f"comfyui: {context.comfyui_status}\n"
                f"worker: {context.worker_status}"
            ),
            skills_block=build_skills_prompt_block(),
            sop_summary=build_sop_summary(),
            security_summary=context.security_policy_summary,
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message.text},
        ]

        try:
            raw_text = self._call_llm(messages)
            parsed = self._parse_response(raw_text)
        except Exception as exc:
            log.error("LLM Proxy call failed: %s", exc)
            # Fallback to local commands
            from assetclaw_matting.brain.local_command_brain import LocalCommandBrain
            return LocalCommandBrain().handle_message(message, context)

        # Execute tool calls
        tool_calls = [BrainToolCall(**tc) for tc in parsed.get("tool_calls", [])]
        skill_results: list[dict[str, Any]] = []
        if tool_calls:
            skill_results = self._execute_tool_calls(tool_calls)

        # Build final reply
        reply = parsed.get("reply", "")
        if skill_results and not reply:
            reply = self._format_skill_results(skill_results)
        elif skill_results:
            result_summary = self._format_skill_results(skill_results)
            reply = f"{reply}\n\n{result_summary}" if result_summary else reply

        response = BrainResponse(
            text=reply or "完成。",
            tool_calls=tool_calls,
            raw={"llm_raw": raw_text[:500]},
            provider=self.name,
        )
        self._log_message(message, response, skill_results)
        return response

    def _call_llm(self, messages: list[dict[str, Any]]) -> str:
        import requests
        from assetclaw_matting.config import settings

        url = f"{settings.llm_proxy_base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {settings.llm_proxy_api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": settings.llm_proxy_model or "gpt-4o-mini",
            "messages": messages,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        resp = requests.post(
            url, json=payload, headers=headers,
            timeout=settings.llm_proxy_timeout_seconds,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def _parse_response(self, raw: str) -> dict[str, Any]:
        """Parse LLM JSON response with fallback."""
        # Try direct parse
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # Try extracting JSON block
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        # Last resort: treat whole response as reply
        log.warning("LLM response was not valid JSON, using as plain text")
        return {"reply": raw.strip(), "tool_calls": []}
