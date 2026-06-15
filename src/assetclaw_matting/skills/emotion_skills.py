from __future__ import annotations

from typing import Any

from assetclaw_matting.brain.emotion_planner import plan_emotional_reply


def respond(text: str = "", **_: Any) -> dict[str, Any]:
    reply = plan_emotional_reply(text) or "我在。你不用把话说得很工整，碎一点也行，我会尽量先听懂你真正想要的东西。"
    return {"ok": True, "text": reply}
