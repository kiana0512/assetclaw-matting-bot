from __future__ import annotations

from pathlib import Path
import uuid

from assetclaw_matting.db.repos import create_pending_confirmation
from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import init_db
from assetclaw_matting.feishu.models import FeishuMessageEvent
from assetclaw_matting.feishu.processor import process_feishu_message


def setup_module() -> None:
    init_db(Path("E:/assetclaw-matting-bot/data/test_assetclaw.db"))
    create_tables()


def _event(text: str, event_id: str) -> FeishuMessageEvent:
    return FeishuMessageEvent(
        trace_id=event_id,
        event_id=event_id,
        message_id=f"msg_{event_id}",
        chat_id="chat_confirm",
        chat_type="p2p",
        open_id="user_confirm",
        user_id="user_confirm",
        text=text,
    )


def test_multiple_confirmations_are_executed_once(monkeypatch) -> None:
    replies: list[str] = []
    calls: list[tuple[str, dict]] = []

    monkeypatch.setattr("assetclaw_matting.feishu.processor._try_reply", lambda _mid, _cid, text: replies.append(text))
    monkeypatch.setattr("assetclaw_matting.feishu.processor._try_send_chat", lambda _cid, text: replies.append(text))

    def fake_call_skill(skill: str, arguments: dict, requested_by: str = "brain") -> dict:
        calls.append((skill, arguments))
        return {"ok": True, "skill": skill, "result": {"run_id": arguments["run_id"], "status": "CANCELED"}}

    monkeypatch.setattr("assetclaw_matting.skills.registry.call_skill", fake_call_skill)
    first = create_pending_confirmation(
        "feishu:chat_confirm:user_confirm",
        "user_confirm",
        "comfyui.run_cancel",
        {"run_id": "COMFY_111111111111"},
    )
    second = create_pending_confirmation(
        "feishu:chat_confirm:user_confirm",
        "user_confirm",
        "comfyui.run_cancel",
        {"run_id": "COMFY_222222222222"},
    )

    result = process_feishu_message(_event(f"确认执行 {first}  确认执行 {second}", f"evt_multi_confirm_{uuid.uuid4().hex}"))

    assert result.ok is True
    assert calls == [
        ("comfyui.run_cancel", {"run_id": "COMFY_111111111111"}),
        ("comfyui.run_cancel", {"run_id": "COMFY_222222222222"}),
    ]
    assert "需要确认" not in "\n".join(replies)
