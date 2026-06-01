from __future__ import annotations

from assetclaw_matting.brain.base import BrainProvider
from assetclaw_matting.brain.schemas import BrainMessage, BrainResponse


class ReservedProvider(BrainProvider):
    def __init__(self, name: str) -> None:
        self.name = name

    def is_available(self) -> bool:
        return False

    def handle_message(self, message: BrainMessage) -> BrainResponse:
        return BrainResponse(
            text=f"{self.name} provider 是未来兼容预留，当前主线使用 LLM Proxy + Brain Router。",
            provider=self.name,
        )
