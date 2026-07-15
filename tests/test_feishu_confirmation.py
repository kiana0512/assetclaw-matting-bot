from __future__ import annotations

from pathlib import Path
import uuid

import pytest

from assetclaw_matting.config import settings
from assetclaw_matting.db.repos import create_pending_confirmation
from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import init_db
from assetclaw_matting.feishu.models import FeishuMessageEvent
from assetclaw_matting.feishu.processor import process_feishu_message


def setup_module() -> None:
    init_db(Path("E:/assetclaw-matting-bot/data/test_assetclaw.db"))
    create_tables()


@pytest.fixture(autouse=True)
def _sync_feishu_processing(monkeypatch) -> None:
    monkeypatch.setattr(settings, "agent_queue_enabled", False)


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


def test_bare_confirmation_code_executes_animation_flow_with_normalized_args(monkeypatch) -> None:
    replies: list[str] = []
    calls: list[tuple[str, dict]] = []

    monkeypatch.setattr("assetclaw_matting.feishu.processor._try_reply", lambda _mid, _cid, text: replies.append(text))
    monkeypatch.setattr("assetclaw_matting.feishu.processor._try_send_chat", lambda _cid, text: replies.append(text))

    def fake_call_skill(skill: str, arguments: dict, requested_by: str = "brain") -> dict:
        calls.append((skill, arguments))
        return {"ok": True, "skill": skill, "result": {"run_id": "AFLOW_TEST", "status": "RUNNING"}}

    monkeypatch.setattr("assetclaw_matting.skills.registry.call_skill", fake_call_skill)
    confirmation_id = create_pending_confirmation(
        "feishu:chat_confirm:user_confirm",
        "user_confirm",
        "animation_flow.start",
        {
            "date_root": r"E:\animation_automation\2026-06-12",
            "unity_import_mode": "替换",
            "priority_characters": "casualheather",
        },
    )

    result = process_feishu_message(_event(confirmation_id, f"evt_bare_confirm_{uuid.uuid4().hex}"))

    assert result.ok is True
    assert calls == [
        (
            "animation_flow.start",
            {
                "date_root": r"E:\animation_automation\2026-06-12",
                "unity_import_mode": "iteration",
                "priority_characters": ["casualheather"],
            },
        )
    ]
    assert "需要确认" not in "\n".join(replies)


def test_duplicate_direct_video_confirmations_start_once(monkeypatch) -> None:
    replies: list[str] = []
    calls: list[tuple[str, dict]] = []
    conversation_id = "feishu:chat_confirm:user_confirm"
    args = {
        "video_paths": [r"E:\assetclaw-matting-bot\storage\feishu_inbox\clip.mp4"],
        "source_names": ["clip.mp4"],
        "run_label": "clip.mp4",
    }

    monkeypatch.setattr("assetclaw_matting.feishu.processor._try_reply", lambda _mid, _cid, text: replies.append(text))
    monkeypatch.setattr("assetclaw_matting.feishu.processor._try_send_chat", lambda _cid, text: replies.append(text))

    def fake_call_skill(skill: str, arguments: dict, requested_by: str = "brain") -> dict:
        calls.append((skill, arguments))
        return {"ok": True, "skill": skill, "result": {"run_id": "VID_ONCE", "videos": [{"name": "clip.mp4"}]}}

    monkeypatch.setattr("assetclaw_matting.skills.registry.call_skill", fake_call_skill)
    create_pending_confirmation(conversation_id, "user_confirm", "direct_video.start", args)
    create_pending_confirmation(conversation_id, "user_confirm", "direct_video.start", args)

    first = process_feishu_message(_event("确认执行", f"evt_duplicate_confirm_first_{uuid.uuid4().hex}"))
    second = process_feishu_message(_event("确认执行", f"evt_duplicate_confirm_second_{uuid.uuid4().hex}"))

    assert first.ok is True
    assert second.ok is True
    assert calls == [("direct_video.start", args)]
    assert replies.count("收到，开始处理。") == 1
    assert "已启动 VID_ONCE（1 个视频）" in replies
    assert "当前没有等待你确认的操作。" in replies


def test_cancel_named_comfy_run_is_not_treated_as_confirmation_cancel(monkeypatch) -> None:
    replies: list[str] = []
    seen: list[str] = []

    monkeypatch.setattr("assetclaw_matting.feishu.processor._try_reply", lambda _mid, _cid, text: replies.append(text))
    monkeypatch.setattr("assetclaw_matting.feishu.processor._try_send_chat", lambda _cid, text: replies.append(text))

    def fake_handle(message):
        seen.append(message.text)
        from assetclaw_matting.brain.schemas import BrainResponse

        return BrainResponse(text="已终止：COMFY_ABCDEF123456", provider="test")

    monkeypatch.setattr("assetclaw_matting.brain.router.handle_message", fake_handle)

    result = process_feishu_message(_event("取消终止这个任务 COMFY_ABCDEF123456", f"evt_cancel_comfy_{uuid.uuid4().hex}"))

    assert result.ok is True
    assert seen == ["取消终止这个任务 COMFY_ABCDEF123456"]
    assert "当前没有等待你确认" not in "\n".join(replies)


def test_greeting_reply_is_warm(monkeypatch) -> None:
    replies: list[str] = []
    monkeypatch.setattr("assetclaw_matting.feishu.processor._try_reply", lambda _mid, _cid, text: replies.append(text))

    result = process_feishu_message(_event("你好", f"evt_greeting_{uuid.uuid4().hex}"))

    assert result.ok is True
    assert replies == ["初音在。今天想让我陪你聊一会儿，还是一起把某个任务往前推一点？"]


def test_conversational_message_skips_processing_ack(monkeypatch) -> None:
    replies: list[str] = []
    monkeypatch.setattr("assetclaw_matting.feishu.processor._try_reply", lambda _mid, _cid, text: replies.append(text))

    def fake_handle(message):
        from assetclaw_matting.brain.schemas import BrainResponse

        return BrainResponse(text="唱歌和接歌词功能已经关闭。", provider="test")

    monkeypatch.setattr("assetclaw_matting.brain.router.handle_message", fake_handle)

    result = process_feishu_message(_event("陪我唱歌", f"evt_sing_{uuid.uuid4().hex}"))

    assert result.ok is True
    assert replies == ["唱歌和接歌词功能已经关闭。"]


def test_task_message_keeps_processing_ack(monkeypatch) -> None:
    replies: list[str] = []
    monkeypatch.setattr("assetclaw_matting.feishu.processor._try_reply", lambda _mid, _cid, text: replies.append(text))

    def fake_handle(message):
        from assetclaw_matting.brain.schemas import BrainResponse

        return BrainResponse(text="上海现在：Clear。", provider="test")

    monkeypatch.setattr("assetclaw_matting.brain.router.handle_message", fake_handle)

    result = process_feishu_message(_event("今天天气怎么样", f"evt_weather_{uuid.uuid4().hex}"))

    assert result.ok is True
    assert replies == ["收到，处理中。", "上海现在：Clear。"]
