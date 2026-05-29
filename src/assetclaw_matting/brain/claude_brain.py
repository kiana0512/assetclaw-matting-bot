"""Claude Brain adapter (Anthropic API).

Recommended for: engineering maintenance, log analysis, code fixes,
complex document understanding, and MCP-based tool use.

IMPLEMENTATION STATUS: Adapter stub — configure CLAUDE_BRAIN_ENABLED=true
and set ANTHROPIC_API_KEY to activate.

Future integration points:
- Anthropic Messages API with tool_use content blocks
- Claude Agent SDK for multi-turn agentic workflows
- MCP server integration (Claude can call /mcp/tools/* directly)
- Claude Code for repository-level code understanding
"""
from __future__ import annotations

import logging

from assetclaw_matting.brain.base import BrainProvider
from assetclaw_matting.brain.schemas import BrainContext, BrainMessage, BrainResponse, BrainToolCall

log = logging.getLogger(__name__)


class ClaudeBrain(BrainProvider):
    name = "claude"

    def is_available(self) -> bool:
        from assetclaw_matting.config import settings
        return settings.claude_brain_enabled and bool(settings.anthropic_api_key)

    def handle_message(
        self, message: BrainMessage, context: BrainContext
    ) -> BrainResponse:
        from assetclaw_matting.config import settings

        if not settings.claude_brain_enabled:
            return BrainResponse(
                text=(
                    "Claude Brain 未启用（CLAUDE_BRAIN_ENABLED=false）。\n"
                    "设置 CLAUDE_BRAIN_ENABLED=true 并填入 ANTHROPIC_API_KEY 后可用。\n"
                    "Claude 适合：日志分析、代码修复、复杂文档理解、工程维护。"
                ),
                provider=self.name,
            )

        # TODO: Implement Anthropic Messages API with tool_use
        # from anthropic import Anthropic
        # client = Anthropic(api_key=settings.anthropic_api_key)
        # tools = _build_anthropic_tools(context.skills_manifest)
        # response = client.messages.create(
        #     model=settings.claude_model,
        #     max_tokens=1024,
        #     system=_build_system_prompt(context),
        #     tools=tools,
        #     messages=[{"role": "user", "content": message.text}],
        # )
        # return _parse_anthropic_response(response)

        return BrainResponse(
            text="Claude Brain 已配置但尚未完全实现。请使用 llm_proxy 或 local_command。",
            provider=self.name,
        )
