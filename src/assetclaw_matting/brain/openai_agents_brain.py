"""OpenAI Agents Brain adapter.

Recommended for: self-hosted long-term platform brain,
autonomous multi-step workflows, MCP-native tool orchestration.

IMPLEMENTATION STATUS: Adapter stub — configure OPENAI_BRAIN_ENABLED=true
and set OPENAI_API_KEY to activate.

Future integration points:
- OpenAI Assistants API with function calling
- OpenAI Agents SDK for stateful multi-turn agents
- MCP server as native tool source (OpenAI supports MCP via Tool Calls)
- Persistent thread IDs for conversation history
"""
from __future__ import annotations

import logging

from assetclaw_matting.brain.base import BrainProvider
from assetclaw_matting.brain.schemas import BrainContext, BrainMessage, BrainResponse

log = logging.getLogger(__name__)


class OpenAIAgentsBrain(BrainProvider):
    name = "openai_agents"

    def is_available(self) -> bool:
        from assetclaw_matting.config import settings
        return settings.openai_brain_enabled and bool(settings.openai_api_key)

    def handle_message(
        self, message: BrainMessage, context: BrainContext
    ) -> BrainResponse:
        from assetclaw_matting.config import settings

        if not settings.openai_brain_enabled:
            return BrainResponse(
                text=(
                    "OpenAI Agents Brain 未启用（OPENAI_BRAIN_ENABLED=false）。\n"
                    "设置 OPENAI_BRAIN_ENABLED=true 并填入 OPENAI_API_KEY 后可用。\n"
                    "OpenAI Agents 适合：自研长期平台化主脑、MCP-native 工具编排。"
                ),
                provider=self.name,
            )

        # TODO: Implement OpenAI Agents SDK
        # from openai import OpenAI
        # client = OpenAI(api_key=settings.openai_api_key)
        # tools = _build_openai_tools(context.skills_manifest)
        # response = client.chat.completions.create(
        #     model=settings.openai_agent_model or "gpt-4o",
        #     messages=[...],
        #     tools=tools,
        # )

        return BrainResponse(
            text="OpenAI Agents Brain 已配置但尚未完全实现。请使用 llm_proxy 或 local_command。",
            provider=self.name,
        )
