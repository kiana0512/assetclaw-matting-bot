from __future__ import annotations

import logging

from assetclaw_matting.brain.base import BrainProvider
from assetclaw_matting.brain.schemas import BrainMessage, BrainResponse

log = logging.getLogger(__name__)


def _make_provider(name: str) -> BrainProvider | None:
    normalized = (name or "").strip().lower()
    if normalized == "llm_proxy":
        from assetclaw_matting.brain.llm_proxy_brain import LLMProxyBrain

        return LLMProxyBrain()
    if normalized == "local_command":
        from assetclaw_matting.brain.local_command_brain import LocalCommandBrain

        return LocalCommandBrain()
    if normalized in {"arkclaw", "claude_sdk", "openai_agents", "langgraph"}:
        from assetclaw_matting.brain.providers_reserved import ReservedProvider

        return ReservedProvider(normalized)
    return None


def get_provider() -> BrainProvider:
    from assetclaw_matting.config import settings
    from assetclaw_matting.brain.local_command_brain import LocalCommandBrain

    for provider_name in (settings.brain_provider, settings.brain_fallback_provider):
        provider = _make_provider(provider_name)
        if provider and provider.is_available():
            return provider
        if provider:
            log.warning("brain provider %s unavailable, trying fallback", provider_name)
    return LocalCommandBrain()


def handle_message(message: BrainMessage) -> BrainResponse:
    provider = get_provider()
    try:
        return provider.handle_message(message)
    except Exception as exc:
        log.exception("brain provider %s crashed", provider.name)
        from assetclaw_matting.brain.local_command_brain import LocalCommandBrain

        fallback = LocalCommandBrain()
        response = BrainResponse(
            text=f"大脑服务暂时不可用，已切到本地兜底模式。错误：{exc}",
            provider=fallback.name,
        )
        fallback.log_message(message, response, {"error": str(exc)})
        return response
