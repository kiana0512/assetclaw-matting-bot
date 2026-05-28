"""External LLM client (OpenAI-compatible API).

Does NOT run any model locally. All inference is done via HTTP to an external API.
The local 3090 GPU is never used by this module.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger(__name__)


class LLMClient:
    """Thin wrapper for OpenAI-compatible chat completions API."""

    def __init__(
        self,
        provider: str = "custom",
        base_url: str = "",
        api_key: str = "",
        model: str = "",
    ) -> None:
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict]] = None,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        """Call the external LLM chat endpoint.

        Returns the raw API response dict.
        Raises NotImplementedError if no base_url is configured.
        """
        if not self.base_url:
            raise NotImplementedError(
                "AGENT_LLM_BASE_URL is not configured. "
                "Set it in .env to enable LLM-powered agent mode."
            )

        import requests

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = [
                {"type": "function", "function": t} for t in tools
            ]

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        resp = requests.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            headers=headers,
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()


def _make_client() -> LLMClient:
    from assetclaw_matting.config import settings
    return LLMClient(
        provider=settings.agent_llm_provider,
        base_url=settings.agent_llm_base_url,
        api_key=settings.agent_llm_api_key,
        model=settings.agent_llm_model,
    )


llm_client = _make_client()
