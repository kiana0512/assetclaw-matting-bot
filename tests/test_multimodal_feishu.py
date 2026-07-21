from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from PIL import Image

from assetclaw_matting.brain.multimodal_planner import plan_multimodal_task
from assetclaw_matting.brain.direct_image_planner import plan_direct_image_task
from assetclaw_matting.brain.direct_video_planner import plan_direct_video_task
from assetclaw_matting.brain.matting_pipeline_planner import plan_matting_pipeline_task
from assetclaw_matting.brain.speech_planner import handle_voice_message
from assetclaw_matting.brain.schemas import BrainMessage
from assetclaw_matting.brain.local_command_brain import LocalCommandBrain
from assetclaw_matting.feishu.message_adapter import from_webhook_dict
from assetclaw_matting.feishu.models import FeishuMessageEvent
from assetclaw_matting.feishu.processor import (
    _has_audio_attachment,
    _download_resource_type,
    _is_stale_event,
    _looks_like_video_file,
    _prepare_attachments,
    _processing_ack_text,
    _should_send_voice_reply,
    _try_add_progress_reaction,
)


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


def test_feishu_folder_message_is_recognized_without_fake_text() -> None:
    event = from_webhook_dict({
        "header": {"event_id": "evt_folder"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_1"}},
            "message": {
                "message_id": "om_folder",
                "chat_id": "oc_1",
                "chat_type": "p2p",
                "message_type": "folder",
                "content": json.dumps({"file_key": "folder_key_1", "file_name": "关键帧"}),
            },
        },
    })

    assert event.text == ""
    assert event.attachments == [{
        "type": "folder",
        "source_message_type": "folder",
        "resource_key": "folder_key_1",
        "file_name": "关键帧",
        "size": None,
        "mime": None,
    }]


def test_feishu_audio_message_extracts_audio_attachment() -> None:
    event = from_webhook_dict({
        "header": {"event_id": "evt_audio"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_1"}},
            "message": {
                "message_id": "om_audio",
                "chat_id": "oc_1",
                "chat_type": "p2p",
                "message_type": "audio",
                "content": json.dumps({"audio_key": "audio_key_1"}),
            },
        },
    })

    assert event.text == ""
    assert event.message_type == "audio"
    assert event.attachments[0]["type"] == "audio"
    assert event.attachments[0]["resource_key"] == "audio_key_1"
    assert event.attachments[0]["file_name"].endswith(".mp3")


def test_feishu_media_message_is_video_attachment() -> None:
    event = from_webhook_dict({
        "header": {"event_id": "evt_media_video"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_1"}},
            "message": {
                "message_id": "om_media_video",
                "chat_id": "oc_1",
                "chat_type": "p2p",
                "message_type": "media",
                "content": json.dumps({"file_key": "video_file_key_1", "image_key": "cover_image_key_1"}),
            },
        },
    })

    assert event.text == ""
    assert event.message_type == "media"
    assert event.attachments[0]["type"] == "video"
    assert event.attachments[0]["source_message_type"] == "media"
    assert event.attachments[0]["resource_key"] == "video_file_key_1"
    assert event.attachments[0]["thumbnail_key"] == "cover_image_key_1"
    assert event.attachments[0]["file_name"].endswith(".mp4")
    assert _download_resource_type("media") == "video"


def test_feishu_upload_file_type_and_mime_for_zip() -> None:
    from assetclaw_matting.feishu.client import _feishu_file_type, _guess_mime_type

    assert _feishu_file_type("result.zip") == "stream"
    assert _guess_mime_type("result.zip") == "application/zip"
    assert _feishu_file_type("clip.mp4") == "mp4"


def test_feishu_send_file_falls_back_to_drive_link_on_size_error(monkeypatch, tmp_path: Path) -> None:
    import requests

    from assetclaw_matting.feishu.client import FeishuClient

    target = tmp_path / "result.zip"
    target.write_bytes(b"zip")
    sent: dict[str, str] = {}
    grants: dict[str, str] = {}
    client = FeishuClient()

    def fail_upload_file(path: Path, file_name: str | None = None) -> str:
        response = requests.Response()
        response.status_code = 400
        response._content = b'{"code":234006,"msg":"The file size exceed the max value."}'
        raise requests.HTTPError("upload_file failed: 400", response=response)

    monkeypatch.setattr(client, "upload_file", fail_upload_file)
    monkeypatch.setattr(
        client,
        "upload_drive_file",
        lambda path, file_name=None: {"file_token": "drive_token", "url": "https://lilithgames.feishu.cn/file/token"},
    )
    monkeypatch.setattr(
        client,
        "grant_drive_file_to_chat",
        lambda file_token, chat_id, perm="full_access": grants.update({"file_token": file_token, "chat_id": chat_id, "perm": perm}),
    )
    monkeypatch.setattr(client, "send_text_to_chat", lambda chat_id, text: sent.update({"chat_id": chat_id, "text": text}))

    client.send_file_to_chat("oc_test", target, target.name)

    assert sent["chat_id"] == "oc_test"
    assert "result.zip" in sent["text"]
    assert "https://lilithgames.feishu.cn/file/token" in sent["text"]
    assert grants == {"file_token": "drive_token", "chat_id": "oc_test", "perm": "full_access"}


def test_feishu_send_large_file_uses_drive_without_message_upload(monkeypatch, tmp_path: Path) -> None:
    from assetclaw_matting.feishu.client import DIRECT_MESSAGE_UPLOAD_MAX_BYTES, FeishuClient

    target = tmp_path / "large-result.zip"
    with target.open("wb") as handle:
        handle.truncate(DIRECT_MESSAGE_UPLOAD_MAX_BYTES + 1)
    sent: dict[str, str] = {}
    client = FeishuClient()

    monkeypatch.setattr(client, "upload_file", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("message upload must not be used")))
    monkeypatch.setattr(
        client,
        "upload_drive_file",
        lambda path, file_name=None: {"file_token": "drive_token", "url": "https://lilithgames.feishu.cn/file/large"},
    )
    monkeypatch.setattr(client, "grant_drive_file_to_chat", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(client, "send_text_to_chat", lambda chat_id, text: sent.update({"chat_id": chat_id, "text": text}))

    result = client.send_file_to_chat("oc_large", target, target.name)

    assert result == {"file_token": "drive_token", "url": "https://lilithgames.feishu.cn/file/large"}
    assert sent == {"chat_id": "oc_large", "text": "文件已生成：large-result.zip\nhttps://lilithgames.feishu.cn/file/large"}


def test_feishu_drive_request_retries_write_timeout(monkeypatch) -> None:
    import requests

    from assetclaw_matting.feishu.client import FeishuClient

    attempts: list[str] = []

    class Response:
        status_code = 200

    def fake_post(url: str, **_kwargs):
        attempts.append(url)
        if len(attempts) == 1:
            raise requests.ConnectionError("write operation timed out")
        return Response()

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr("assetclaw_matting.feishu.client.time.sleep", lambda _seconds: None)

    response = FeishuClient()._post_with_retry("https://example.invalid/upload", attempts=3)

    assert response.status_code == 200
    assert len(attempts) == 2


def test_feishu_client_add_message_reaction(monkeypatch) -> None:
    from assetclaw_matting.feishu.client import FeishuClient

    calls: list[dict[str, object]] = []
    client = FeishuClient()
    monkeypatch.setattr(client, "get_tenant_access_token", lambda: "token")

    class Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"code": 0}

    def fake_post(url: str, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return Response()

    monkeypatch.setattr("assetclaw_matting.feishu.client.requests.post", fake_post)

    assert client.add_message_reaction("om_progress", "敲键盘") is True
    assert calls[0]["url"].endswith("/im/v1/messages/om_progress/reactions")
    assert calls[0]["json"] == {"reaction_type": {"emoji_type": "敲键盘"}}


def test_downloaded_video_rejects_thumbnail_jpeg(tmp_path: Path) -> None:
    fake_video = tmp_path / "clip.mp4"
    fake_video.write_bytes(b"\xff\xd8\xff\xe0JFIF fake cover image")

    assert _looks_like_video_file(fake_video) is False

    realish_video = tmp_path / "real.mp4"
    realish_video.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)

    assert _looks_like_video_file(realish_video) is True


def test_video_attachment_is_not_treated_as_audio(monkeypatch) -> None:
    from assetclaw_matting.config import settings

    monkeypatch.setattr(settings, "bot_tts_enabled", False)
    event = FeishuMessageEvent(
        trace_id="evt_video_not_audio",
        event_id="evt_video_not_audio",
        message_id="om_video_not_audio",
        chat_id="oc_1",
        chat_type="p2p",
        open_id="ou_1",
        user_id="ou_1",
        text="",
        message_type="media",
        attachments=[{"type": "video", "file_name": "clip.mp4", "local_path": "./storage/debug/clip.mp4"}],
    )

    assert _has_audio_attachment(event) is False
    assert "语音" not in _processing_ack_text(event)
    assert _should_send_voice_reply(event, "feishu:oc_1:ou_1") is False


def test_file_mp4_attachment_is_not_treated_as_audio(monkeypatch, tmp_path: Path) -> None:
    from assetclaw_matting.config import settings

    monkeypatch.setattr(settings, "bot_tts_enabled", False)
    video = tmp_path / "source.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
    event = FeishuMessageEvent(
        trace_id="evt_file_mp4",
        event_id="evt_file_mp4",
        message_id="om_file_mp4",
        chat_id="oc_1",
        chat_type="p2p",
        open_id="ou_1",
        user_id="ou_1",
        text="",
        message_type="file",
        attachments=[{"type": "file", "file_name": "source.mp4", "local_path": str(video)}],
    )

    class Provider:
        name = "test"

        def log_message(self, *args, **kwargs):
            return None

    assert _has_audio_attachment(event) is False
    assert "语音" not in _processing_ack_text(event)
    assert handle_voice_message(
        Provider(),
        BrainMessage(
            conversation_id="file-mp4-not-audio",
            text="",
            attachments=event.attachments,
        ),
    ) is None


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
    path = Path.cwd() / "storage/debug/mm_prompt.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(path)

    planned = plan_multimodal_task(
        BrainMessage(
            text="",
            attachments=[{"type": "image", "local_path": str(path), "file_name": path.name}],
        )
    )

    assert planned is not None


def test_direct_video_attachment_routes_to_confirmed_processing(tmp_path: Path) -> None:
    video = tmp_path / "sample.mp4"
    video.write_bytes(b"fake-video")

    planned = plan_direct_video_task(
        BrainMessage(
            text="动画处理",
            conversation_id="feishu:chat_video:user_video",
            user_id="user_video",
            attachments=[
                {
                    "type": "video",
                    "file_name": "sample.mp4",
                    "downloaded": True,
                    "local_path": str(video),
                }
            ],
        )
    )

    assert planned is not None
    tool_calls, reason = planned
    assert reason == "direct Feishu video attachment route"
    assert tool_calls[0].skill == "direct_video.start"
    assert tool_calls[0].arguments["video_paths"] == [str(video)]


def test_direct_video_confirmation_is_human_readable() -> None:
    from assetclaw_matting.skills.direct_video_skills import preview_start_confirmation

    text = preview_start_confirmation(
        {
            "video_paths": ["./storage/source.mp4"],
            "source_names": ["7月13日思考-1_3.mp4"],
        },
        "abc123",
    )

    assert "收到 1 个动画视频，可以开始处理。" in text
    assert "7月13日思考-1_3.mp4" in text
    assert "正方形 256x256，长方形 384x512" in text
    assert "回复“确认执行”开始。" in text
    assert "abc123" not in text
    assert "direct_video.start" not in text


def test_direct_video_rejects_feishu_media_video_for_original_quality(tmp_path: Path) -> None:
    video = tmp_path / "compressed.mp4"
    video.write_bytes(b"fake-video")

    planned = plan_direct_video_task(
        BrainMessage(
            text="动画处理",
            conversation_id="feishu:chat_video:user_video",
            user_id="user_video",
            attachments=[
                {
                    "type": "video",
                    "source_message_type": "media",
                    "file_name": "compressed.mp4",
                    "downloaded": True,
                    "local_path": str(video),
                }
            ],
        )
    )

    assert planned is not None
    tool_calls, reason = planned
    assert tool_calls is None
    assert "当作“文件”发送" in reason


def test_direct_video_status_question_routes_without_attachment() -> None:
    planned = plan_direct_video_task(
        BrainMessage(
            text="这个视频处理进度到哪了",
            conversation_id="feishu:chat_video:user_video",
            user_id="user_video",
        )
    )

    assert planned is not None
    tool_calls, _reason = planned
    assert tool_calls[0].skill == "direct_video.status"


def test_direct_video_batch_list_includes_completed_runs() -> None:
    planned = plan_direct_video_task(
        BrainMessage(text="这批六个任务进度列表", attachments=[], conversation_id="test")
    )
    assert planned is not None
    tool_calls, _reason = planned
    assert tool_calls[0].skill == "direct_video.list"
    assert tool_calls[0].arguments["include_finished"] is True


def test_current_work_keeps_all_members_of_active_repair_batch_visible() -> None:
    from assetclaw_matting.brain.result_formatter import _format_direct_media_overview

    runs = []
    for position in range(1, 7):
        runs.append(
            {
                "run_id": f"VID_BATCH_{position}",
                "status": "DONE" if position <= 4 else "RUNNING",
                "stage": "done" if position <= 4 else "repair_matting",
                "repair_batch": {"position": position, "total": 6},
                "items": [
                    {
                        "name": f"video_{position}.mp4",
                        "stage": "done" if position <= 4 else "repair_matting",
                        "total": 10,
                        "matte_done": 10 if position <= 4 else position,
                        "smooth_done": 10 if position <= 4 else 0,
                        "output_size": "384x512",
                    }
                ],
            }
        )

    lines = _format_direct_media_overview(
        {
            "direct_videos": runs,
            "direct_images": [
                {
                    "run_id": "IMG_UNRELATED",
                    "status": "DONE",
                    "items": [{"name": "unrelated.png", "stage": "done"}],
                }
            ],
            "filters": {},
        }
    )
    text = "\n".join(lines)
    for position in range(1, 7):
        assert f"video_{position}.mp4" in text
    assert "unrelated.png" not in text

    for run in runs:
        run["status"] = "DONE"
        run["stage"] = "done"
    completed_text = "\n".join(
        _format_direct_media_overview(
            {"direct_videos": runs, "direct_images": [], "filters": {}}
        )
    )
    for position in range(1, 7):
        assert f"video_{position}.mp4" in completed_text


def test_current_work_groups_image_sequence_and_does_not_truncate_rows() -> None:
    from assetclaw_matting.brain.result_formatter import _format_direct_media_overview

    image_items = [
        {
            "name": f"{index:04d}.png",
            "status": "完成",
            "total": 1,
            "matte_done": 1,
            "smooth_done": 1,
            "output_size": "256x256",
        }
        for index in range(22)
    ]
    video_runs = [
        {
            "run_id": f"VID_{index}",
            "status": "DONE",
            "stage": "done",
            "items": [{"name": f"video_{index}.mp4", "status": "完成", "total": 1}],
        }
        for index in range(13)
    ]
    text = "\n".join(_format_direct_media_overview({
        "direct_videos": video_runs,
        "direct_images": [{
            "run_id": "IMG_SEQUENCE",
            "run_label": "关键帧",
            "status": "DONE",
            "stage": "done",
            "items": image_items,
        }],
        "filters": {"date_start": "2026-07-21", "date_end": "2026-07-21"},
    }))

    assert "video_12.mp4" in text
    assert "关键帧（22张）" in text
    assert "0000.png" not in text
    assert "未显示" not in text


def test_current_work_in_feishu_only_shows_same_conversation(monkeypatch) -> None:
    from assetclaw_matting.runtime_context import reset_runtime_context, set_runtime_context
    from assetclaw_matting.skills import agent_ops_skills

    monkeypatch.setattr(agent_ops_skills, "_direct_video_runs", lambda limit=40: [
        {"run_id": "VID_OTHER", "conversation_id": "feishu:other", "items": []},
    ])
    monkeypatch.setattr(agent_ops_skills, "_direct_image_runs", lambda limit=40: [
        {"run_id": "IMG_MINE", "conversation_id": "feishu:mine", "items": []},
        {"run_id": "IMG_OTHER", "conversation_id": "feishu:other", "items": []},
    ])
    monkeypatch.setattr(agent_ops_skills, "_latest_comfyui", lambda: {})
    monkeypatch.setattr(agent_ops_skills, "_latest_cherry", lambda: {})
    monkeypatch.setattr(agent_ops_skills, "_latest_frame", lambda: {})
    monkeypatch.setattr(agent_ops_skills, "_latest_pipeline", lambda: {})
    monkeypatch.setattr(agent_ops_skills, "_pending_confirmations", lambda: [])
    monkeypatch.setattr(agent_ops_skills, "_recent_errors", lambda: [])
    token = set_runtime_context(channel="feishu", conversation_id="feishu:mine")
    try:
        payload = agent_ops_skills.current_work(include_gpu=False)
    finally:
        reset_runtime_context(token)

    assert payload["direct_videos"] == []
    assert [run["run_id"] for run in payload["direct_images"]] == ["IMG_MINE"]


def test_direct_image_attachment_routes_without_confirmation(tmp_path: Path) -> None:
    image = tmp_path / "source.png"
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(image)

    planned = plan_direct_image_task(
        BrainMessage(
            text="",
            conversation_id="feishu:chat_image:user_image",
            user_id="user_image",
            attachments=[
                {
                    "type": "image",
                    "file_name": "source.png",
                    "downloaded": True,
                    "local_path": str(image),
                }
            ],
        )
    )

    assert planned is not None
    tool_calls, reason = planned
    assert reason == "direct Feishu image attachment route"
    assert tool_calls[0].skill == "direct_image.start"
    assert tool_calls[0].arguments["image_paths"] == [str(image)]


def test_many_images_are_one_named_sequence_task(tmp_path: Path) -> None:
    attachments = []
    for index in range(22):
        image = tmp_path / f"{index:04d}.png"
        Image.new("RGBA", (8, 8), (index, 0, 0, 255)).save(image)
        attachments.append({
            "type": "image",
            "file_name": image.name,
            "downloaded": True,
            "local_path": str(image),
            "source_collection": "关键帧",
        })

    planned = plan_direct_image_task(BrainMessage(
        text="",
        conversation_id="feishu:chat_sequence:user",
        user_id="user",
        attachments=attachments,
    ))

    assert planned is not None
    tool_calls, _reason = planned
    assert len(tool_calls) == 1
    assert tool_calls[0].skill == "direct_image.start"
    assert tool_calls[0].arguments["run_label"] == "关键帧"
    assert [Path(path).name for path in tool_calls[0].arguments["image_paths"]] == [f"{index:04d}.png" for index in range(22)]


def test_direct_image_folder_attachment_routes_as_image_set(tmp_path: Path) -> None:
    folder = tmp_path / "海瑟序列帧"
    folder.mkdir()
    first = folder / "0001.png"
    second = folder / "0002.png"
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(first)
    Image.new("RGBA", (8, 12), (0, 255, 0, 255)).save(second)

    planned = plan_direct_image_task(
        BrainMessage(
            text="需要进行抠图",
            conversation_id="feishu:chat_image_set:user_image",
            user_id="user_image",
            attachments=[{"type": "file", "file_name": folder.name, "downloaded": True, "local_path": str(folder)}],
        )
    )

    assert planned is not None
    tool_calls, reason = planned
    assert reason == "direct Feishu image attachment route"
    assert tool_calls[0].skill == "direct_image.start"
    assert tool_calls[0].arguments["image_paths"] == [str(first), str(second)]
    assert tool_calls[0].arguments["run_label"] == "海瑟序列帧"


def test_direct_image_zip_attachment_routes_as_image_set(monkeypatch, tmp_path: Path) -> None:
    import zipfile
    from assetclaw_matting.config import settings

    monkeypatch.setattr(settings, "storage_dir", tmp_path / "storage")

    source = tmp_path / "src"
    source.mkdir()
    first = source / "0001.png"
    second = source / "nested" / "0002.png"
    second.parent.mkdir()
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(first)
    Image.new("RGBA", (8, 12), (0, 255, 0, 255)).save(second)
    archive = tmp_path / "海瑟序列帧.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(first, "0001.png")
        zf.write(second, "nested/0002.png")
        zf.writestr("../skip.png", b"bad")

    planned = plan_direct_image_task(
        BrainMessage(
            text="需要进行抠图",
            conversation_id="feishu:chat_image_zip:user_image",
            user_id="user_image",
            attachments=[{"type": "file", "file_name": archive.name, "downloaded": True, "local_path": str(archive)}],
        )
    )

    assert planned is not None
    tool_calls, _reason = planned
    paths = tool_calls[0].arguments["image_paths"]
    assert len(paths) == 2
    assert paths[0].endswith("0001.png")
    assert paths[1].endswith("0002.png")
    assert tool_calls[0].arguments["package_as_sequence"] is True
    assert tool_calls[0].arguments["run_label"].endswith(".zip")


def test_direct_image_zip_uses_natural_frame_order_and_stays_a_sequence(monkeypatch, tmp_path: Path) -> None:
    import zipfile
    from assetclaw_matting.config import settings

    monkeypatch.setattr(settings, "storage_dir", tmp_path / "storage")

    archive = tmp_path / "keyframes.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for name, color in (("10.png", 10), ("2.png", 2), ("1.png", 1)):
            image = tmp_path / name
            Image.new("RGBA", (8, 8), (color, 0, 0, 255)).save(image)
            zf.write(image, name)

    planned = plan_direct_image_task(BrainMessage(
        text="",
        conversation_id="feishu:chat_zip_order:user",
        user_id="user",
        attachments=[{"type": "file", "file_name": archive.name, "downloaded": True, "local_path": str(archive)}],
    ))

    assert planned is not None
    tool_calls, _reason = planned
    assert [Path(path).name for path in tool_calls[0].arguments["image_paths"]] == ["1.png", "2.png", "10.png"]
    assert tool_calls[0].arguments["package_as_sequence"] is True


def test_direct_image_followup_uses_recent_image_set(monkeypatch, tmp_path: Path) -> None:
    from assetclaw_matting.config import settings
    from assetclaw_matting.db.repos import upsert_memory_note
    from assetclaw_matting.db.schema import create_tables
    from assetclaw_matting.db.sqlite import init_db

    init_db(Path.cwd() / "data/test_assetclaw.db")
    create_tables()
    monkeypatch.setattr(settings, "storage_dir", tmp_path / "storage")
    folder = tmp_path / "海瑟序列帧"
    folder.mkdir()
    image = folder / "0001.png"
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(image)
    upsert_memory_note("feishu:chat_recent_set:user_image", "last_image_set_path", str(folder), source="test")

    planned = plan_direct_image_task(
        BrainMessage(
            text="需要进行抠图",
            conversation_id="feishu:chat_recent_set:user_image",
            user_id="user_image",
        )
    )

    assert planned is not None
    tool_calls, _reason = planned
    assert tool_calls[0].skill == "direct_image.start"
    assert tool_calls[0].arguments["image_paths"] == [str(image)]


def test_direct_image_status_question_routes_without_attachment() -> None:
    planned = plan_direct_image_task(
        BrainMessage(
            text="这张图处理进度到哪了",
            conversation_id="feishu:chat_image:user_image",
            user_id="user_image",
        )
    )

    assert planned is not None
    tool_calls, _reason = planned
    assert tool_calls[0].skill == "direct_image.status"


def test_generic_progress_question_routes_to_latest_direct_video(monkeypatch, tmp_path: Path) -> None:
    from datetime import datetime

    from assetclaw_matting.brain.local_command_brain import LocalCommandBrain
    from assetclaw_matting.config import settings
    from assetclaw_matting.db.schema import create_tables
    from assetclaw_matting.db.sqlite import init_db
    from assetclaw_matting.skills import direct_image_skills, direct_video_skills

    init_db(Path.cwd() / "data/test_assetclaw.db")
    create_tables()
    storage = tmp_path / "storage"
    video_root = storage / "direct_video_runs"
    image_root = storage / "direct_image_runs"
    monkeypatch.setattr(settings, "storage_dir", storage)
    monkeypatch.setattr(direct_video_skills, "RUNS_ROOT", video_root)
    monkeypatch.setattr(direct_image_skills, "RUNS_ROOT", image_root)
    today = datetime.now().date().isoformat()
    (video_root / "VID_NEW").mkdir(parents=True)
    (image_root / "IMG_OLD").mkdir(parents=True)
    (video_root / "VID_NEW" / "status.json").write_text(
        json.dumps(
            {
                "id": "VID_NEW",
                "status": "RUNNING",
                "stage": "matting",
                "created_at": f"{today}T11:00:00",
                "updated_at": f"{today}T11:10:00",
                "videos": [{"frame_count": 43, "aspect": "square", "cherry_profile": "half", "cherry_output_size": "256x256"}],
                "children": {"comfyui": {"completed": 6, "total": 43, "status": "RUNNING"}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (image_root / "IMG_OLD" / "status.json").write_text(
        json.dumps(
            {
                "id": "IMG_OLD",
                "status": "DONE",
                "stage": "done",
                "created_at": "2026-07-09T10:00:00",
                "updated_at": "2026-07-09T10:30:00",
                "images": [],
                "children": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    response = LocalCommandBrain().handle_message(
        BrainMessage(text="进度如何了", conversation_id="feishu:oc_progress:ou_progress", user_id="ou_progress")
    )

    assert response.tool_calls
    assert response.tool_calls[0].skill in {"direct_video.status", "agent.current_work"}
    assert "当前执行现场" not in response.text
    assert "1. " in response.text
    assert "256×256" in response.text


def test_generic_progress_question_routes_to_latest_direct_image(monkeypatch, tmp_path: Path) -> None:
    from datetime import datetime

    from assetclaw_matting.brain.local_command_brain import LocalCommandBrain
    from assetclaw_matting.config import settings
    from assetclaw_matting.db.schema import create_tables
    from assetclaw_matting.db.sqlite import init_db
    from assetclaw_matting.skills import direct_image_skills, direct_video_skills

    init_db(Path.cwd() / "data/test_assetclaw.db")
    create_tables()
    storage = tmp_path / "storage"
    video_root = storage / "direct_video_runs"
    image_root = storage / "direct_image_runs"
    monkeypatch.setattr(settings, "storage_dir", storage)
    monkeypatch.setattr(direct_video_skills, "RUNS_ROOT", video_root)
    monkeypatch.setattr(direct_image_skills, "RUNS_ROOT", image_root)
    today = datetime.now().date().isoformat()
    (video_root / "VID_OLD").mkdir(parents=True)
    (image_root / "IMG_NEW").mkdir(parents=True)
    (video_root / "VID_OLD" / "status.json").write_text(
        json.dumps(
            {
                "id": "VID_OLD",
                "status": "DONE",
                "stage": "done",
                "created_at": "2026-07-09T10:00:00",
                "updated_at": "2026-07-09T10:30:00",
                "videos": [],
                "children": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (image_root / "IMG_NEW" / "status.json").write_text(
        json.dumps(
            {
                "id": "IMG_NEW",
                "status": "RUNNING",
                "stage": "postprocess",
                "created_at": f"{today}T11:00:00",
                "updated_at": f"{today}T11:15:00",
                "images": [{"aspect": "portrait", "cherry_profile": "full", "cherry_output_size": "384x512"}],
                "children": {"cherry": {"completed": 0, "total": 1, "status": "RUNNING"}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    response = LocalCommandBrain().handle_message(
        BrainMessage(text="进度如何了", conversation_id="feishu:oc_progress_img:ou_progress", user_id="ou_progress")
    )

    assert response.tool_calls
    assert response.tool_calls[0].skill in {"direct_image.status", "agent.current_work"}
    assert "1. " in response.text
    assert "384×512" in response.text


def test_direct_media_natural_language_pressure_samples(monkeypatch, tmp_path: Path) -> None:
    from datetime import datetime

    from assetclaw_matting.brain.local_command_brain import LocalCommandBrain
    from assetclaw_matting.config import settings
    from assetclaw_matting.db.schema import create_tables
    from assetclaw_matting.db.sqlite import init_db
    from assetclaw_matting.skills import direct_image_skills, direct_video_skills

    init_db(Path.cwd() / "data/test_assetclaw.db")
    create_tables()
    storage = tmp_path / "storage"
    video_root = storage / "direct_video_runs"
    image_root = storage / "direct_image_runs"
    monkeypatch.setattr(settings, "storage_dir", storage)
    monkeypatch.setattr(direct_video_skills, "RUNS_ROOT", video_root)
    monkeypatch.setattr(direct_image_skills, "RUNS_ROOT", image_root)
    today = datetime.now().date().isoformat()
    samples = [
        ("VID_THINK", f"{today}T10:00:00", "7月13日思考-1_5.mp4", "384x512"),
        ("VID_IDLE_TODAY", f"{today}T10:05:00", "今天待机-1.mp4", "256x256"),
        ("VID_IDLE_711", "2026-07-11T10:00:00", "7月11日待机-1.mp4", "256x256"),
        ("VID_SRC", "2026-07-13T10:00:00", "source_2.mp4", "256x256"),
    ]
    for run_id, ts, name, size in samples:
        status_path = video_root / run_id / "status.json"
        (status_path.parent / "matte" / "video_01").mkdir(parents=True)
        (status_path.parent / "matte" / "video_01" / "0001.png").write_bytes(b"x")
        status_path.write_text(
            json.dumps(
                {
                    "id": run_id,
                    "status": "RUNNING",
                    "stage": "matting",
                    "run_label": name,
                    "created_at": ts,
                    "updated_at": ts,
                    "videos": [{"name": name, "frame_count": 43, "matte_dir": str(status_path.parent / "matte" / "video_01"), "smooth_dir": str(status_path.parent / "smooth" / "video_01"), "cherry_output_size": size}],
                    "children": {"comfyui_run_id": f"COMFY_{run_id[-5:]}"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    brain = LocalCommandBrain()
    today_response = brain.handle_message(BrainMessage(text="现在进度怎么样了"))
    range_response = brain.handle_message(BrainMessage(text="711 713任务汇总"))
    detail_response = brain.handle_message(BrainMessage(text="source_2.mp4 这个视频的任务具体信息"))
    idle_response = brain.handle_message(BrainMessage(text="待机"))
    dated_idle_response = brain.handle_message(BrainMessage(text="711 待机"))
    think_response = brain.handle_message(BrainMessage(text="思考"))

    assert "7月13日思考-1_5.mp4" in today_response.text
    assert "7月11日待机-1.mp4" not in today_response.text
    assert "7月11日待机-1.mp4" in range_response.text
    assert "source_2.mp4" in range_response.text
    assert "视频任务详情：source_2.mp4" in detail_response.text
    assert "今天待机-1.mp4" in idle_response.text
    assert "7月11日待机-1.mp4" in dated_idle_response.text
    assert "7月13日思考-1_5.mp4" in think_response.text
    for response in (today_response, range_response, detail_response, idle_response, dated_idle_response, think_response):
        assert "RUNNING/matting" not in response.text


def test_matting_pipeline_questions_route_to_pipeline_skills() -> None:
    status = plan_matting_pipeline_task(BrainMessage(conversation_id="conv-pipeline", text="现在用的抠图管线是什么"))
    verify = plan_matting_pipeline_task(BrainMessage(conversation_id="conv-pipeline", text="验证抠图管线有没有问题"))
    update = plan_matting_pipeline_task(BrainMessage(conversation_id="conv-pipeline", text="更新抠图管线"))

    assert status is not None
    assert verify is not None
    assert update is not None
    assert status[0][0].skill == "matting_pipeline.status"
    assert verify[0][0].skill == "matting_pipeline.verify"
    assert update[0][0].skill == "matting_pipeline.update"


def test_direct_video_cherry_runs_per_video_dir(monkeypatch, tmp_path: Path) -> None:
    from assetclaw_matting.skills import direct_video_skills

    matte_one = tmp_path / "run" / "matte" / "video_01"
    matte_two = tmp_path / "run" / "matte" / "video_02"
    matte_one.mkdir(parents=True, exist_ok=True)
    matte_two.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(matte_one / "0000.png")
    Image.new("RGBA", (4, 4), (0, 255, 0, 255)).save(matte_two / "0000.png")
    calls: list[dict[str, object]] = []

    def fake_run_start(**kwargs):
        calls.append(kwargs)
        size = (256, 256) if kwargs.get("profile") == "half" else (384, 512)
        output_dir = Path(str(kwargs["output_dir"]))
        output_dir.mkdir(parents=True, exist_ok=True)
        for source in Path(str(kwargs["input_dir"])).glob("*.png"):
            shutil.copy2(source, output_dir / source.name)
        return {
            "run_id": f"CHERRY_TEST_{len(calls)}",
            "options": {"resize_width": size[0], "resize_height": size[1]},
        }

    def fake_run_status(run_id: str, include_gpu: bool = False):
        return {"ok": True, "run_id": run_id, "status": "DONE", "completed": 1, "total": 1}

    monkeypatch.setattr(direct_video_skills, "RUNS_ROOT", tmp_path)
    monkeypatch.setattr("assetclaw_matting.skills.cherry_skills.run_start", fake_run_start)
    monkeypatch.setattr("assetclaw_matting.skills.cherry_skills.run_status", fake_run_status)

    run = {
        "id": "VID_TEST_CHERRY",
        "status": "RUNNING",
        "stage": "postprocess",
        "updated_at": "",
        "children": {},
        "notify_interval_seconds": 60,
        "last_notification_at": 0,
        "chat_id": "",
        "videos": [
            {
                "index": 1,
                "matte_dir": str(matte_one),
                "smooth_dir": str(tmp_path / "run" / "smooth" / "video_01"),
                "cherry_profile": "half",
            },
            {
                "index": 2,
                "matte_dir": str(matte_two),
                "smooth_dir": str(tmp_path / "run" / "smooth" / "video_02"),
                "cherry_profile": "full",
            },
        ],
        "log": [],
    }

    direct_video_skills._run_cherry(run)

    assert [Path(str(call["input_dir"])).name for call in calls] == ["video_01", "video_02"]
    assert [call["profile"] for call in calls] == ["half", "full"]
    assert [item["cherry_output_size"] for item in run["videos"]] == ["256x256", "384x512"]


def test_direct_image_cherry_profile_and_size_are_recorded(monkeypatch, tmp_path: Path) -> None:
    from assetclaw_matting.skills import direct_image_skills

    matte_one = tmp_path / "run" / "matte" / "image_01"
    matte_two = tmp_path / "run" / "matte" / "image_02"
    matte_one.mkdir(parents=True, exist_ok=True)
    matte_two.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(matte_one / "0000.png")
    Image.new("RGBA", (4, 4), (0, 255, 0, 255)).save(matte_two / "0000.png")
    calls: list[dict[str, object]] = []

    def fake_run_start(**kwargs):
        calls.append(kwargs)
        size = (256, 256) if kwargs.get("profile") == "half" else (384, 512)
        return {
            "run_id": f"CHERRY_IMG_{len(calls)}",
            "options": {"resize_width": size[0], "resize_height": size[1]},
        }

    def fake_run_status(run_id: str, include_gpu: bool = False):
        return {"ok": True, "run_id": run_id, "status": "DONE", "completed": 1, "total": 1}

    monkeypatch.setattr(direct_image_skills, "RUNS_ROOT", tmp_path)
    monkeypatch.setattr("assetclaw_matting.skills.cherry_skills.run_start", fake_run_start)
    monkeypatch.setattr("assetclaw_matting.skills.cherry_skills.run_status", fake_run_status)

    run = {
        "id": "IMG_TEST_CHERRY",
        "status": "RUNNING",
        "stage": "postprocess",
        "updated_at": "",
        "children": {},
        "notify_interval_seconds": 60,
        "chat_id": "",
        "images": [
            {
                "index": 1,
                "matte_dir": str(matte_one),
                "smooth_dir": str(tmp_path / "run" / "smooth" / "image_01"),
                "aspect": "square",
                "cherry_profile": "half",
            },
            {
                "index": 2,
                "matte_dir": str(matte_two),
                "smooth_dir": str(tmp_path / "run" / "smooth" / "image_02"),
                "aspect": "portrait",
                "cherry_profile": "full",
            },
        ],
        "log": [],
    }

    direct_image_skills._run_cherry(run)

    assert [call["profile"] for call in calls] == ["half", "full"]
    assert [item["cherry_output_size"] for item in run["images"]] == ["256x256", "384x512"]


def test_direct_image_start_uses_exact_square_rule(monkeypatch) -> None:
    from assetclaw_matting.skills import direct_image_skills

    root = Path.cwd() / "storage/debug/test_direct_image_exact"
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    square = src / "square.png"
    near_square = src / "near_square.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(square)
    Image.new("RGBA", (100, 108), (0, 255, 0, 255)).save(near_square)

    monkeypatch.setattr(direct_image_skills, "RUNS_ROOT", root / "runs")
    monkeypatch.setattr(direct_image_skills, "_start_worker", lambda _run_id: None)

    result = direct_image_skills.start(
        [str(square), str(near_square)],
        source_names=[square.name, near_square.name],
        workflow_path="./workflows/test.json",
    )

    assert [item["aspect"] for item in result["images"]] == ["square", "portrait"]
    assert [item["cherry_output_size"] for item in result["images"]] == ["256x256", "384x512"]


def test_direct_image_send_results_returns_matte_processed_and_comparison(monkeypatch, tmp_path: Path) -> None:
    from assetclaw_matting.feishu.client import feishu_client
    from assetclaw_matting.skills import direct_image_skills

    original = tmp_path / "original_images" / "image_01" / "source.png"
    matte = tmp_path / "matte" / "image_01" / "0000.png"
    smooth = tmp_path / "smooth" / "image_01"
    original.parent.mkdir(parents=True, exist_ok=True)
    matte.parent.mkdir(parents=True, exist_ok=True)
    result_image = smooth / "0000.png"
    smooth.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (320, 240), (255, 255, 255)).save(original)
    Image.new("RGBA", (320, 240), (255, 0, 0, 128)).save(matte)
    Image.new("RGBA", (256, 256), (255, 0, 0, 255)).save(result_image)
    calls: list[tuple[str, Path, str]] = []
    inline_calls: list[tuple[str, Path]] = []

    def fake_send_file(chat_id: str, path: Path, file_name: str) -> dict[str, object]:
        calls.append((chat_id, Path(path), file_name))
        return {"ok": True}

    def fake_send_image(chat_id: str, path: Path) -> None:
        inline_calls.append((chat_id, Path(path)))

    monkeypatch.setattr(direct_image_skills, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(feishu_client, "send_file_to_chat", fake_send_file)
    monkeypatch.setattr(feishu_client, "send_image_to_chat", fake_send_image)
    run = {
        "id": "IMG_SEND_TEST",
        "chat_id": "oc_test",
        "updated_at": "",
        "images": [
            {
                "original_path": str(original),
                "matte_dir": str(matte.parent),
                "smooth_dir": str(smooth),
                "name": "source.png",
            }
        ],
        "log": [],
    }

    sent = direct_image_skills._send_results(run)

    comparison = tmp_path / "runs" / "IMG_SEND_TEST" / "comparison" / "source_comparison.png"
    assert sent == [str(matte), str(result_image), str(comparison)]
    assert calls == [
        ("oc_test", matte, "source_matte.png"),
        ("oc_test", result_image, "source_processed.png"),
    ]
    assert inline_calls == [("oc_test", comparison)]
    assert comparison.is_file()
    with Image.open(comparison) as triptych:
        assert triptych.size == (1524, 632)
        assert triptych.mode == "RGB"
    assert run["images"][0]["matte_result_path"] == str(matte)
    assert run["images"][0]["postprocessed_result_path"] == str(result_image)
    assert run["images"][0]["comparison_path"] == str(comparison)
    assert run["images"][0]["result_path"] == str(result_image)


def test_direct_image_sequence_sends_one_ordered_zip(monkeypatch, tmp_path: Path) -> None:
    import zipfile

    from assetclaw_matting.feishu.client import feishu_client
    from assetclaw_matting.skills import direct_image_skills

    monkeypatch.setattr(direct_image_skills, "RUNS_ROOT", tmp_path / "runs")
    sent_files: list[str] = []
    monkeypatch.setattr(
        feishu_client,
        "send_file_to_chat",
        lambda _chat_id, path, file_name: sent_files.append(file_name) or {"ok": True},
    )
    monkeypatch.setattr(
        feishu_client,
        "send_image_to_chat",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("sequence must not send individual images")),
    )
    images = []
    for index in range(2):
        original = tmp_path / "original" / f"{index}.png"
        matte = tmp_path / "matte" / str(index) / "0000.png"
        smooth = tmp_path / "smooth" / str(index) / "0000.png"
        original.parent.mkdir(parents=True, exist_ok=True)
        matte.parent.mkdir(parents=True, exist_ok=True)
        smooth.parent.mkdir(parents=True, exist_ok=True)
        for path in (original, matte, smooth):
            Image.new("RGBA", (8, 8), (index, 0, 0, 255)).save(path)
        images.append({
            "index": index + 1,
            "source_name": f"{index:04d}.png",
            "original_path": str(original),
            "matte_dir": str(matte.parent),
            "smooth_dir": str(smooth.parent),
        })
    run = {
        "id": "IMG_SEQUENCE_TEST",
        "run_label": "关键帧",
        "chat_id": "oc_test",
        "updated_at": "",
        "images": images,
        "log": [],
    }

    sent = direct_image_skills._send_results(run)

    assert len(sent) == 1
    assert sent_files == ["关键帧_animation_processed.zip"]
    with zipfile.ZipFile(sent[0]) as archive:
        names = set(archive.namelist())
    for index in range(2):
        for section in ("frames", "matte", "smooth", "comparison"):
            assert f"{section}/{index:04d}.png" in names
    assert "manifest.json" in names


def test_direct_video_zip_contains_required_sections(monkeypatch, tmp_path: Path) -> None:
    import zipfile

    from assetclaw_matting.skills import direct_video_skills

    monkeypatch.setattr(direct_video_skills, "RUNS_ROOT", tmp_path / "runs")
    run = {
        "id": "VID_ZIP_TEST",
        "status": "RUNNING",
        "stage": "zip",
        "videos": [],
        "children": {},
        "log": [],
    }
    run_dir = direct_video_skills._run_dir(run)
    for folder, name in [
        ("original_videos", "source.mp4"),
        ("frames/video_01", "0000.png"),
        ("matte/video_01", "0000.png"),
        ("smooth/video_01", "0000.png"),
    ]:
        path = run_dir / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")

    zip_path = direct_video_skills._make_zip(run)

    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
    assert "manifest.json" in names
    assert "original_videos/source.mp4" in names
    assert "frames/video_01/0000.png" in names
    assert "matte/video_01/0000.png" in names
    assert "smooth/video_01/0000.png" in names


def test_direct_video_zip_uses_original_video_name() -> None:
    from assetclaw_matting.skills import direct_video_skills

    run = {
        "id": "VID_INTERNAL_ID",
        "run_label": "客户动画-走路.mp4",
        "videos": [{"source_name": "客户动画-走路.mp4", "name": "01_客户动画-走路.mp4"}],
    }

    assert direct_video_skills._zip_filename(run) == "客户动画-走路_animation_processed.zip"


def test_direct_video_recovery_resumes_dead_worker_and_keeps_live_worker(monkeypatch, tmp_path: Path) -> None:
    from assetclaw_matting.skills import direct_video_skills

    monkeypatch.setattr(direct_video_skills, "RUNS_ROOT", tmp_path / "runs")
    started: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        direct_video_skills,
        "_start_worker",
        lambda run_id, recover=False: started.append((run_id, recover)),
    )
    dead = {"id": "VID_DEAD", "status": "RUNNING", "stage": "matting", "worker_pid": 0, "videos": [], "log": []}
    live = {"id": "VID_LIVE", "status": "RUNNING", "stage": "matting", "worker_pid": os.getpid(), "videos": [], "log": []}
    direct_video_skills._save(dead)
    direct_video_skills._save(live)

    result = direct_video_skills.recover_incomplete_runs()

    assert result["recovered"] == ["VID_DEAD"]
    assert result["still_running"] == ["VID_LIVE"]
    assert started == [("VID_DEAD", True)]
    assert direct_video_skills._load("VID_DEAD")["stage"] == "recovery_queued"


def test_direct_video_comfyui_enables_strict_frame_identity(monkeypatch, tmp_path: Path) -> None:
    from assetclaw_matting.skills import direct_video_skills

    frame_dir = tmp_path / "frames" / "video_01"
    matte_dir = tmp_path / "matte" / "video_01"
    frame_dir.mkdir(parents=True)
    matte_dir.mkdir(parents=True)
    Image.new("RGB", (16, 16), (200, 20, 10)).save(frame_dir / "0000.png")
    Image.new("RGBA", (16, 16), (200, 20, 10, 255)).save(matte_dir / "0000.png")
    calls: list[dict[str, object]] = []

    def fake_start(**kwargs):
        calls.append(kwargs)
        return {"run_id": "COMFY_STRICT"}

    monkeypatch.setattr(direct_video_skills, "RUNS_ROOT", tmp_path)
    monkeypatch.setattr("assetclaw_matting.skills.comfyui_skills.run_start", fake_start)
    monkeypatch.setattr(
        "assetclaw_matting.skills.comfyui_skills.run_status",
        lambda *_args, **_kwargs: {"status": "DONE", "completed": 1, "total": 1},
    )
    run = {
        "id": "VID_STRICT",
        "status": "RUNNING",
        "children": {},
        "videos": [{"index": 1, "frame_dir": str(frame_dir), "matte_dir": str(matte_dir)}],
        "workflow_path": "workflow.json",
        "notify_interval_seconds": 60,
        "log": [],
    }

    direct_video_skills._run_comfyui_unlocked(run)

    assert calls[0]["strict_frame_identity"] is True
    assert run["integrity"]["matte"]["video_01"]["identity_verified"] == 1


def test_direct_video_resend_zip_reuses_saved_result(monkeypatch, tmp_path: Path) -> None:
    import zipfile

    from assetclaw_matting.skills import direct_video_skills

    monkeypatch.setattr(direct_video_skills, "RUNS_ROOT", tmp_path / "runs")
    run = {
        "id": "VID_RESEND_TEST",
        "status": "DONE_WITH_ERRORS",
        "stage": "delivery",
        "chat_id": "oc_original",
        "zip_path": "",
        "error": "timeout",
        "videos": [],
        "children": {},
        "log": [],
    }
    run_dir = direct_video_skills._run_dir(run)
    run_dir.mkdir(parents=True, exist_ok=True)
    zip_path = run_dir / "result.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("manifest.json", "{}")
    run["zip_path"] = str(zip_path)
    direct_video_skills._save(run)
    sent: list[tuple[str, Path]] = []
    monkeypatch.setattr(direct_video_skills, "_send_zip_with_retries", lambda payload, path: sent.append((payload["chat_id"], path)))

    result = direct_video_skills.resend_zip("VID_RESEND_TEST")

    assert result["ok"] is True
    assert sent == [("oc_original", zip_path)]
    saved = direct_video_skills._load("VID_RESEND_TEST")
    assert saved is not None
    assert saved["status"] == "DONE"
    assert saved["stage"] == "done"
    assert saved["error"] == ""


def test_direct_video_delivery_failure_keeps_completed_zip_for_resend(monkeypatch, tmp_path: Path) -> None:
    import zipfile

    from assetclaw_matting.skills import direct_video_skills

    monkeypatch.setattr(direct_video_skills, "RUNS_ROOT", tmp_path / "runs")
    run = {
        "id": "VID_DELIVERY_TEST",
        "status": "RUNNING",
        "stage": "queued",
        "chat_id": "oc_original",
        "videos": [],
        "children": {},
        "zip_path": "",
        "error": "",
        "log": [],
    }
    direct_video_skills._save(run)
    monkeypatch.setattr(direct_video_skills, "_extract_all", lambda _run: None)
    monkeypatch.setattr(direct_video_skills, "_run_comfyui", lambda _run: None)
    monkeypatch.setattr(direct_video_skills, "_run_cherry", lambda _run: None)

    def fake_make_zip(payload):
        path = direct_video_skills._run_dir(payload) / "result.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("manifest.json", "{}")
        return path

    notices: list[str] = []
    monkeypatch.setattr(direct_video_skills, "_make_zip", fake_make_zip)
    monkeypatch.setattr(direct_video_skills, "_send_zip_with_retries", lambda *_args: (_ for _ in ()).throw(TimeoutError("write timeout")))
    monkeypatch.setattr(direct_video_skills, "_notify", lambda _run, text: notices.append(text))

    direct_video_skills._worker("VID_DELIVERY_TEST")

    saved = direct_video_skills._load("VID_DELIVERY_TEST")
    assert saved is not None
    assert saved["status"] == "DONE_WITH_ERRORS"
    assert saved["stage"] == "delivery"
    assert Path(saved["zip_path"]).is_file()
    assert "无需重新抽帧、抠图和后处理" in notices[-1]


def test_direct_video_cancel_cancels_child_comfyui_and_cherry(monkeypatch, tmp_path: Path) -> None:
    from assetclaw_matting.skills import cherry_skills, comfyui_skills, direct_video_skills

    calls: list[tuple[str, str]] = []
    notifications: list[str] = []
    monkeypatch.setattr(direct_video_skills, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(direct_video_skills, "_notify", lambda _run, text: notifications.append(text))
    monkeypatch.setattr(comfyui_skills, "run_cancel", lambda run_id, interrupt_current=True, notify=True: calls.append(("comfyui", run_id)) or {"ok": True, "run_id": run_id, "status": "CANCELED"})
    monkeypatch.setattr(cherry_skills, "run_cancel", lambda run_id, notify=True: calls.append(("cherry", run_id)) or {"ok": True, "run_id": run_id, "status": "CANCELED"})

    run = {
        "id": "VID_CANCEL",
        "status": "RUNNING",
        "stage": "matting",
        "run_label": "source.mp4",
        "videos": [{"name": "source.mp4"}],
        "children": {"comfyui_run_id": "COMFY_CHILD", "cherry_run_ids": ["CHERRY_CHILD"]},
        "log": [],
    }
    direct_video_skills._save(run)

    result = direct_video_skills.cancel("VID_CANCEL")

    assert result["ok"] is True
    assert result["status"] == "CANCELED"
    assert calls == [("comfyui", "COMFY_CHILD"), ("cherry", "CHERRY_CHILD")]
    assert notifications == []
    saved = direct_video_skills._load("VID_CANCEL")
    assert saved["children"]["cancel_results"][0]["run_id"] == "COMFY_CHILD"


def test_direct_video_cancel_can_match_file_name(monkeypatch, tmp_path: Path) -> None:
    from assetclaw_matting.brain.local_command_brain import LocalCommandBrain
    from assetclaw_matting.skills import comfyui_skills, direct_video_skills

    monkeypatch.setattr(direct_video_skills, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(comfyui_skills, "run_cancel", lambda run_id, interrupt_current=True, notify=True: {"ok": True, "run_id": run_id, "status": "CANCELED"})
    run = {
        "id": "VID_BY_NAME",
        "status": "RUNNING",
        "stage": "matting",
        "run_label": "source_2.mp4",
        "videos": [{"name": "source_2.mp4"}],
        "children": {"comfyui_run_id": "COMFY_BY_NAME"},
        "log": [],
    }
    direct_video_skills._save(run)

    response = LocalCommandBrain().handle_message(BrainMessage(text="终止这个任务 source_2.mp4"))

    assert response.tool_calls[0].skill == "direct_video.cancel"
    assert response.tool_calls[0].arguments["run_id"] == "source_2.mp4"
    assert "已取消动画任务：source_2.mp4" in response.text
    assert direct_video_skills._load("VID_BY_NAME")["status"] == "CANCELED"


def test_direct_status_formatter_uses_media_table() -> None:
    from assetclaw_matting.brain.result_formatter import format_skill_results

    result = {
        "ok": True,
        "skill": "direct_video.status",
        "result": {
            "ok": True,
            "run_id": "VID_TEST",
            "status": "RUNNING",
            "stage": "matting",
            "videos": [
                {"name": "思考.mp4", "frame_count": 10, "aspect": "square", "cherry_profile": "half", "cherry_output_size": "256x256"},
                {"name": "待机.mp4", "frame_count": 12, "aspect": "portrait", "cherry_profile": "full", "cherry_output_size": "384x512"},
            ],
            "children": {"comfyui": {"completed": 1, "total": 22, "status": "RUNNING"}},
        },
    }

    text = format_skill_results([result])

    assert "视频任务：" in text
    assert "1. " in text
    assert "2. " in text
    assert "思考.mp4" in text
    assert "待机.mp4" in text
    assert "VID_TEST" not in text


def test_progress_question_adds_message_reaction(monkeypatch) -> None:
    from assetclaw_matting.config import settings
    from assetclaw_matting.feishu import client as feishu_client_module

    calls: list[tuple[str, str]] = []

    class Client:
        def add_message_reaction(self, message_id: str, emoji_type: str) -> bool:
            calls.append((message_id, emoji_type))
            return True

    monkeypatch.setattr(settings, "feishu_progress_reaction_enabled", True)
    monkeypatch.setattr(settings, "feishu_progress_reaction_emoji_types", "敲键盘;keyboard")
    monkeypatch.setattr(feishu_client_module, "feishu_client", Client())
    event = FeishuMessageEvent(
        trace_id="trace-progress-reaction",
        event_id="evt-progress-reaction",
        message_id="om-progress-reaction",
        chat_id="oc-progress-reaction",
        chat_type="p2p",
        open_id="ou-progress-reaction",
        user_id="ou-progress-reaction",
        text="进度如何",
    )

    _try_add_progress_reaction(event)

    assert calls == [("om-progress-reaction", "敲键盘")]


def test_progress_question_does_not_send_processing_ack() -> None:
    from assetclaw_matting.feishu.processor import _should_send_processing_ack

    event = FeishuMessageEvent(
        trace_id="trace-progress",
        event_id="evt-progress",
        message_id="om-progress",
        chat_id="oc-progress",
        chat_type="p2p",
        open_id="ou-progress",
        user_id="ou-progress",
        text="进度如何",
    )

    assert _should_send_processing_ack(event) is False


def test_cancel_and_detail_questions_do_not_send_processing_ack() -> None:
    from assetclaw_matting.feishu.processor import _should_send_processing_ack

    cancel_event = FeishuMessageEvent(
        trace_id="trace-cancel-ack",
        event_id="evt-cancel-ack",
        message_id="om-cancel-ack",
        chat_id="oc-cancel-ack",
        chat_type="p2p",
        open_id="ou-cancel-ack",
        user_id="ou-cancel-ack",
        text="终止这个任务 source_2.mp4",
    )
    detail_event = FeishuMessageEvent(
        trace_id="trace-detail-ack",
        event_id="evt-detail-ack",
        message_id="om-detail-ack",
        chat_id="oc-detail-ack",
        chat_type="p2p",
        open_id="ou-detail-ack",
        user_id="ou-detail-ack",
        text="source_2.mp4 这个视频的任务具体信息",
    )

    assert _should_send_processing_ack(cancel_event) is False
    assert _should_send_processing_ack(detail_event) is False


def test_direct_media_attachment_does_not_send_default_ack() -> None:
    from assetclaw_matting.feishu.processor import _should_send_processing_ack

    event = FeishuMessageEvent(
        trace_id="trace-image-ack",
        event_id="evt-image-ack",
        message_id="om-image-ack",
        chat_id="oc-image-ack",
        chat_type="p2p",
        open_id="ou-image-ack",
        user_id="ou-image-ack",
        text="",
        attachments=[{"type": "image", "file_name": "source.png", "local_path": "E:/tmp/source.png"}],
    )

    assert _should_send_processing_ack(event) is False


def test_direct_image_start_formatter_mentions_postprocess_preset() -> None:
    from assetclaw_matting.brain.result_formatter import format_skill_results

    text = format_skill_results(
        [
            {
                "ok": True,
                "skill": "direct_image.start",
                "result": {
                    "ok": True,
                    "run_id": "IMG_TEST",
                    "images": [
                        {"aspect": "portrait", "cherry_profile": "full", "cherry_output_size": "384x512"},
                    ],
                    "pipeline_notice": "已确认最新",
                },
            }
        ]
    )

    assert "已启动 IMG_TEST" in text
    assert "后处理：长方形 384x512×1" in text


def test_multimodal_planner_previews_image() -> None:
    path = Path.cwd() / "storage/debug/mm_preview.png"
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


def test_multimodal_planner_analyzes_image_content() -> None:
    path = Path.cwd() / "storage/debug/mm_analyze.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (4, 4), (0, 255, 255, 255)).save(path)

    planned = plan_multimodal_task(
        BrainMessage(
            text="分析内容",
            attachments=[{"type": "image", "local_path": str(path), "file_name": path.name}],
        )
    )

    assert planned is not None
    tool_calls, _ = planned
    assert tool_calls[0].skill == "image.describe"
    assert tool_calls[0].arguments["image_path"] == str(path)


def test_multimodal_planner_analyzes_recent_image_without_attachment() -> None:
    from assetclaw_matting.db.repos import upsert_memory_note
    from assetclaw_matting.db.schema import create_tables
    from assetclaw_matting.db.sqlite import init_db

    init_db(Path.cwd() / "data/test_assetclaw.db")
    create_tables()
    path = Path.cwd() / "storage/debug/mm_recent_analyze.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (4, 4), (0, 255, 255, 255)).save(path)
    upsert_memory_note("recent-analyze", "last_image_path", str(path), source="test")

    planned = plan_multimodal_task(BrainMessage(conversation_id="recent-analyze", text="分析内容"))

    assert planned is not None
    tool_calls, _ = planned
    assert tool_calls[0].skill == "image.describe"
    assert tool_calls[0].arguments["image_path"] == str(path)


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


def test_voice_attachment_transcribes_and_routes_to_tools(monkeypatch, tmp_path) -> None:
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"fake-audio")

    def fake_transcribe(audio_path: str, language: str = "zh", prompt: str = "") -> dict:
        return {"ok": True, "audio_path": audio_path, "text": "查看 GPU 状态和当前任务", "engine": "test"}

    monkeypatch.setattr("assetclaw_matting.brain.speech_planner.transcribe", fake_transcribe)
    monkeypatch.setattr(
        LocalCommandBrain,
        "execute_tool_calls",
        lambda self, tool_calls, conversation_id="", user_id="": [{"ok": True, "skill": call.skill, "result": {"ok": True}} for call in tool_calls],
    )
    response = LocalCommandBrain().handle_message(
        BrainMessage(
            conversation_id="voice-route",
            text="",
            attachments=[{"type": "audio", "local_path": str(audio), "file_name": audio.name}],
        )
    )

    assert "语音识别：查看 GPU 状态和当前任务" in response.text
    assert len(response.tool_calls) == 2
    assert response.tool_calls[0].skill == "system.gpu_status"
    assert response.tool_calls[1].skill == "agent.current_work"


def test_audio_attachment_without_local_path_does_not_go_to_ocr() -> None:
    response = LocalCommandBrain().handle_message(
        BrainMessage(
            conversation_id="voice-no-local",
            text="[语音]",
            attachments=[{"type": "audio", "file_name": "voice.mp3", "downloaded": False}],
        )
    )

    assert "语音还没下载到本地" in response.text
    assert "图片没下到本地" not in response.text
    assert "OCR" not in response.text


def test_voice_reply_mode_toggle() -> None:
    from assetclaw_matting.brain.speech_planner import voice_reply_enabled
    from assetclaw_matting.db.schema import create_tables
    from assetclaw_matting.db.sqlite import init_db

    init_db(Path.cwd() / "data/test_assetclaw.db")
    create_tables()
    brain = LocalCommandBrain()

    on_response = brain.handle_message(BrainMessage(conversation_id="voice-mode-test", text="开启语音回复"))
    assert "已开启语音回复" in on_response.text
    assert voice_reply_enabled("voice-mode-test") is True

    off_response = brain.handle_message(BrainMessage(conversation_id="voice-mode-test", text="关闭语音回复"))
    assert "已关闭语音回复" in off_response.text
    assert voice_reply_enabled("voice-mode-test") is False


def test_voice_capability_question_is_not_swallowed_by_smalltalk() -> None:
    response = LocalCommandBrain().handle_message(BrainMessage(conversation_id="voice-capability", text="晚上好 你可以听我的语音吗"))

    assert "可以听你的语音" in response.text
    assert "本地 ASR" in response.text
    assert "我接原创下一句" not in response.text


def test_tts_synthesize_writes_file(monkeypatch, tmp_path) -> None:
    from assetclaw_matting.config import settings
    from assetclaw_matting.skills import speech_skills

    monkeypatch.setattr(settings, "storage_dir", tmp_path)
    monkeypatch.setattr(settings, "tts_engine", "edge_tts")

    def fake_edge(text: str, target: Path, voice: str, rate: str) -> bool:
        target.write_bytes(b"fake-mp3")
        return True

    monkeypatch.setattr(speech_skills, "_synthesize_edge_tts", fake_edge)
    payload = speech_skills.synthesize("你好，语音框架测试")

    assert payload["ok"] is True
    assert payload["engine"] == "edge_tts"
    assert Path(payload["output_path"]).exists()


def test_indextts_synthesize_writes_wav(monkeypatch, tmp_path) -> None:
    from assetclaw_matting.config import settings
    from assetclaw_matting.skills import speech_skills

    monkeypatch.setattr(settings, "storage_dir", tmp_path)
    monkeypatch.setattr(settings, "tts_engine", "indextts")

    def fake_indextts(text: str, target: Path, prompt_audio: str | None = None) -> bool:
        target.write_bytes(b"fake-wav")
        return True

    monkeypatch.setattr(speech_skills, "_synthesize_indextts", fake_indextts)
    payload = speech_skills.synthesize("你好，IndexTTS 测试")

    assert payload["ok"] is True
    assert payload["engine"] == "indextts2"
    assert Path(payload["output_path"]).suffix == ".wav"
    assert Path(payload["output_path"]).exists()


def test_openai_whisper_model_name_maps_turbo() -> None:
    from assetclaw_matting.skills.speech_skills import _openai_whisper_model_name

    assert _openai_whisper_model_name("large-v3-turbo") == "turbo"
    assert _openai_whisper_model_name("small") == "small"


def test_voice_reply_detection(monkeypatch) -> None:
    from assetclaw_matting.config import settings

    monkeypatch.setattr(settings, "bot_tts_enabled", True)
    monkeypatch.setattr(settings, "voice_reply_on_audio", True)
    audio_event = FeishuMessageEvent(
        trace_id="trace_voice",
        event_id="evt_voice",
        message_id="om_voice",
        chat_id="oc_voice",
        chat_type="p2p",
        open_id="ou_voice",
        user_id="ou_voice",
        text="",
        message_type="audio",
        attachments=[{"type": "file", "file_name": "voice.wav"}],
    )

    assert _has_audio_attachment(audio_event) is True
    assert _should_send_voice_reply(audio_event, "voice-reply-detect") is True


def test_processing_ack_text_for_voice_and_deepseek(monkeypatch) -> None:
    from assetclaw_matting.config import settings

    voice_event = FeishuMessageEvent(
        trace_id="trace_voice_ack",
        event_id="evt_voice_ack",
        message_id="om_voice_ack",
        chat_id="oc_voice_ack",
        chat_type="p2p",
        open_id="ou_voice_ack",
        user_id="ou_voice_ack",
        text="",
        message_type="audio",
        attachments=[{"type": "audio", "file_name": "voice.wav"}],
    )
    monkeypatch.setattr(settings, "brain_provider", "local_command")
    assert "转文字中" in _processing_ack_text(voice_event)
    assert "先发文字结果" in _processing_ack_text(voice_event)

    text_event = FeishuMessageEvent(
        trace_id="trace_thinking_ack",
        event_id="evt_thinking_ack",
        message_id="om_thinking_ack",
        chat_id="oc_thinking_ack",
        chat_type="p2p",
        open_id="ou_thinking_ack",
        user_id="ou_thinking_ack",
        text="帮我深入分析一下",
        message_type="text",
    )
    monkeypatch.setattr(settings, "brain_provider", "deepseek")
    monkeypatch.setattr(settings, "deepseek_thinking_type", "enabled")
    assert _processing_ack_text(text_event) == "收到，思考中。"
