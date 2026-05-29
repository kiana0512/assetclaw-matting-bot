"""Brain Router — selects the active brain provider based on config.

Priority:
1. BRAIN_PROVIDER setting
2. If provider is unavailable → BRAIN_FALLBACK_PROVIDER
3. Ultimate fallback: local_command (always available)
"""
from __future__ import annotations

import logging

from assetclaw_matting.brain.base import BrainProvider
from assetclaw_matting.brain.schemas import BrainContext, BrainMessage, BrainResponse

log = logging.getLogger(__name__)


def _make_provider(name: str) -> BrainProvider | None:
    """Instantiate a brain provider by name."""
    from assetclaw_matting.brain.local_command_brain import LocalCommandBrain
    from assetclaw_matting.brain.llm_proxy_brain import LLMProxyBrain
    from assetclaw_matting.brain.arkclaw_brain import ArkClawBrain
    from assetclaw_matting.brain.claude_brain import ClaudeBrain
    from assetclaw_matting.brain.openai_agents_brain import OpenAIAgentsBrain
    from assetclaw_matting.brain.langgraph_brain import LangGraphBrain

    mapping = {
        "local_command": LocalCommandBrain,
        "llm_proxy": LLMProxyBrain,
        "arkclaw": ArkClawBrain,
        "claude": ClaudeBrain,
        "openai_agents": OpenAIAgentsBrain,
        "langgraph": LangGraphBrain,
    }
    cls = mapping.get(name)
    return cls() if cls else None


def get_provider() -> BrainProvider:
    """Return the active brain provider, with fallback chain."""
    from assetclaw_matting.config import settings
    from assetclaw_matting.brain.local_command_brain import LocalCommandBrain

    requested = settings.brain_provider
    fallback_name = settings.brain_fallback_provider

    # Try primary
    provider = _make_provider(requested)
    if provider and provider.is_available():
        log.debug("Brain provider: %s", requested)
        return provider

    if provider and not provider.is_available():
        log.warning(
            "Brain provider %r is configured but not available "
            "(check API keys/enabled flags), falling back to %r",
            requested, fallback_name,
        )

    # Try fallback
    if fallback_name and fallback_name != requested:
        fb = _make_provider(fallback_name)
        if fb and fb.is_available():
            log.info("Using fallback brain: %s", fallback_name)
            return fb

    # Ultimate fallback
    log.info("Using local_command brain (ultimate fallback)")
    return LocalCommandBrain()


def handle_message(
    message: BrainMessage,
    context: BrainContext | None = None,
) -> BrainResponse:
    """Route a message through the active brain provider."""
    if context is None:
        from assetclaw_matting.brain.context_builder import build_context
        try:
            context = build_context()
        except Exception as exc:
            log.warning("Failed to build brain context: %s", exc)
            context = BrainContext()

    provider = get_provider()
    try:
        return provider.handle_message(message, context)
    except Exception as exc:
        log.exception("Brain provider %s crashed", provider.name)
        # Emergency fallback
        from assetclaw_matting.brain.local_command_brain import LocalCommandBrain
        try:
            return LocalCommandBrain().handle_message(message, context)
        except Exception:
            return BrainResponse(
                text=f"系统错误，请稍后重试。（{exc}）",
                provider="emergency_fallback",
            )
