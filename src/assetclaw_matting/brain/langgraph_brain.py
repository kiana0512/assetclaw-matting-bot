"""LangGraph Brain adapter (reserved for future use).

Use case: complex multi-step agentic workflows with explicit state graphs,
human-in-the-loop checkpoints, and long-running orchestration.

IMPLEMENTATION STATUS: Stub — not yet implemented.
"""
from __future__ import annotations

from assetclaw_matting.brain.base import BrainProvider
from assetclaw_matting.brain.schemas import BrainContext, BrainMessage, BrainResponse


class LangGraphBrain(BrainProvider):
    name = "langgraph"

    def is_available(self) -> bool:
        from assetclaw_matting.config import settings
        return settings.langgraph_brain_enabled

    def handle_message(
        self, message: BrainMessage, context: BrainContext
    ) -> BrainResponse:
        return BrainResponse(
            text=(
                "LangGraph Brain 暂未实现（LANGGRAPH_BRAIN_ENABLED 预留）。\n"
                "适合：复杂多步骤工作流、人机协同检查点、长期编排。\n"
                "当前请使用 llm_proxy 或 local_command。"
            ),
            provider=self.name,
        )
