from __future__ import annotations

from typing import Any


def info(**_: Any) -> dict[str, Any]:
    from assetclaw_matting.services.sticker_service import sticker_status

    return sticker_status()


def send_random(**_: Any) -> dict[str, Any]:
    from assetclaw_matting.runtime_context import get_runtime_context
    from assetclaw_matting.services.sticker_service import send_sticker_to_chat

    ctx = get_runtime_context()
    chat_id = ctx.get("chat_id") or ""
    if not chat_id:
        raise RuntimeError("sticker.send_random requires a Feishu chat context")
    return send_sticker_to_chat(chat_id, force=True)
