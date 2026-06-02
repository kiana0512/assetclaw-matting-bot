from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from assetclaw_matting.brain.multimodal_planner import plan_multimodal_task
from assetclaw_matting.brain.schemas import BrainMessage
from assetclaw_matting.feishu.message_adapter import from_webhook_dict
from assetclaw_matting.feishu.models import FeishuMessageEvent
from assetclaw_matting.feishu.processor import _is_stale_event, _prepare_attachments


def test_feishu_image_message_extracts_attachment() -> None:
    event = from_webhook_dict({
        "header": {"event_id": "evt_1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_1"}},
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_1",
                "chat_type": "p2p",
                "message_type": "image",
                "content": json.dumps({"image_key": "img_key_1"}),
            },
        },
    })

    assert event.text == ""
    assert event.message_type == "image"
    assert event.attachments[0]["type"] == "image"
    assert event.attachments[0]["resource_key"] == "img_key_1"


def test_feishu_post_message_extracts_text_and_image() -> None:
    event = from_webhook_dict({
        "header": {"event_id": "evt_post_img"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_1"}},
            "message": {
                "message_id": "om_post_img",
                "chat_id": "oc_1",
                "chat_type": "p2p",
                "message_type": "post",
                "content": json.dumps({
                    "content": [[
                        {"tag": "img", "image_key": "img_key_post"},
                        {"tag": "text", "text": "提取并且翻译这个图片中的文字为日语ok"},
                    ]]
                }),
            },
        },
    })

    assert "提取并且翻译" in event.text
    assert event.attachments[0]["type"] == "image"
    assert event.attachments[0]["resource_key"] == "img_key_post"


def test_stale_feishu_event_is_detected(monkeypatch) -> None:
    import time
    from assetclaw_matting.config import settings

    monkeypatch.setattr(settings, "feishu_ignore_events_older_than_seconds", 600)
    event = from_webhook_dict({
        "header": {"event_id": "evt_old"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_1"}},
            "message": {
                "message_id": "om_old",
                "chat_id": "oc_1",
                "chat_type": "p2p",
                "message_type": "text",
                "create_time": str(int((time.time() - 3600) * 1000)),
                "content": json.dumps({"text": "你好"}),
            },
        },
    })

    assert _is_stale_event(event) is True


def test_multimodal_planner_prompts_for_next_action() -> None:
    path = Path("E:/assetclaw-matting-bot/storage/debug/mm_prompt.png")
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(path)

    planned = plan_multimodal_task(
        BrainMessage(
            text="",
            attachments=[{"type": "image", "local_path": str(path), "file_name": path.name}],
        )
    )

    assert planned is not None
    tool_calls, text = planned
    assert tool_calls == []
    assert "已保存到" in text


def test_multimodal_planner_previews_image() -> None:
    path = Path("E:/assetclaw-matting-bot/storage/debug/mm_preview.png")
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(path)

    planned = plan_multimodal_task(
        BrainMessage(
            text="预览发回给我",
            attachments=[{"type": "image", "local_path": str(path), "file_name": path.name}],
        )
    )

    assert planned is not None
    tool_calls, _ = planned
    assert tool_calls[0].skill == "feishu.send_image"
    assert tool_calls[0].arguments["path"] == str(path)


def test_prepare_image_attachment_keeps_download_error(monkeypatch, tmp_path) -> None:
    from assetclaw_matting.config import settings
    from assetclaw_matting.feishu import client as feishu_client_module

    monkeypatch.setattr(settings, "storage_dir", tmp_path)

    def fail_message_resource(*_: object, **__: object) -> None:
        raise RuntimeError("99991672 Access denied")

    monkeypatch.setattr(feishu_client_module.feishu_client, "download_message_resource", fail_message_resource)

    event = FeishuMessageEvent(
        trace_id="trace_img",
        event_id="evt_img",
        message_id="om_img",
        chat_id="oc_img",
        chat_type="p2p",
        open_id="ou_img",
        user_id="ou_img",
        text="",
        message_type="image",
        attachments=[{"type": "image", "resource_key": "img_key_1", "file_name": "feishu_image.png"}],
    )

    prepared = _prepare_attachments(event, "feishu:oc_img:ou_img")

    assert prepared[0]["downloaded"] is False
    assert "99991672" in str(prepared[0]["error"])
