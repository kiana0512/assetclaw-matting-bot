from __future__ import annotations

import json
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
        attachments=[{"type": "video", "file_name": "clip.mp4", "local_path": "E:/assetclaw-matting-bot/storage/debug/clip.mp4"}],
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
            "video_paths": ["E:/assetclaw-matting-bot/storage/source.mp4"],
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
    from assetclaw_matting.brain.local_command_brain import LocalCommandBrain
    from assetclaw_matting.db.schema import create_tables
    from assetclaw_matting.db.sqlite import init_db
    from assetclaw_matting.skills import direct_image_skills, direct_video_skills

    init_db(Path("E:/assetclaw-matting-bot/data/test_assetclaw.db"))
    create_tables()
    video_root = tmp_path / "video_runs"
    image_root = tmp_path / "image_runs"
    monkeypatch.setattr(direct_video_skills, "RUNS_ROOT", video_root)
    monkeypatch.setattr(direct_image_skills, "RUNS_ROOT", image_root)
    (video_root / "VID_NEW").mkdir(parents=True)
    (image_root / "IMG_OLD").mkdir(parents=True)
    (video_root / "VID_NEW" / "status.json").write_text(
        json.dumps(
            {
                "id": "VID_NEW",
                "status": "RUNNING",
                "stage": "matting",
                "created_at": "2026-07-09T11:00:00",
                "updated_at": "2026-07-09T11:10:00",
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
    assert response.tool_calls[0].skill == "direct_video.status"
    assert response.tool_calls[0].arguments["run_id"] == "VID_NEW"
    assert "当前执行现场" not in response.text
    assert "VID_NEW" in response.text
    assert "正方形 256x256×1" in response.text


def test_generic_progress_question_routes_to_latest_direct_image(monkeypatch, tmp_path: Path) -> None:
    from assetclaw_matting.brain.local_command_brain import LocalCommandBrain
    from assetclaw_matting.db.schema import create_tables
    from assetclaw_matting.db.sqlite import init_db
    from assetclaw_matting.skills import direct_image_skills, direct_video_skills

    init_db(Path("E:/assetclaw-matting-bot/data/test_assetclaw.db"))
    create_tables()
    video_root = tmp_path / "video_runs"
    image_root = tmp_path / "image_runs"
    monkeypatch.setattr(direct_video_skills, "RUNS_ROOT", video_root)
    monkeypatch.setattr(direct_image_skills, "RUNS_ROOT", image_root)
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
                "created_at": "2026-07-09T11:00:00",
                "updated_at": "2026-07-09T11:15:00",
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
    assert response.tool_calls[0].skill == "direct_image.status"
    assert response.tool_calls[0].arguments["run_id"] == "IMG_NEW"
    assert "IMG_NEW" in response.text
    assert "长方形 384x512×1" in response.text


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

    root = Path("E:/assetclaw-matting-bot/storage/debug/test_direct_image_exact")
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
        workflow_path="E:/assetclaw-matting-bot/workflows/test.json",
    )

    assert [item["aspect"] for item in result["images"]] == ["square", "portrait"]
    assert [item["cherry_output_size"] for item in result["images"]] == ["256x256", "384x512"]


def test_direct_image_send_results_uses_file_attachment(monkeypatch, tmp_path: Path) -> None:
    from assetclaw_matting.feishu.client import feishu_client
    from assetclaw_matting.skills import direct_image_skills

    smooth = tmp_path / "smooth" / "image_01"
    smooth.mkdir(parents=True, exist_ok=True)
    result_image = smooth / "0000.png"
    Image.new("RGBA", (256, 256), (255, 0, 0, 255)).save(result_image)
    calls: list[tuple[str, Path, str]] = []

    def fake_send_file(chat_id: str, path: Path, file_name: str) -> dict[str, object]:
        calls.append((chat_id, Path(path), file_name))
        return {"ok": True}

    monkeypatch.setattr(direct_image_skills, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(feishu_client, "send_file_to_chat", fake_send_file)
    run = {
        "id": "IMG_SEND_TEST",
        "chat_id": "oc_test",
        "updated_at": "",
        "images": [{"smooth_dir": str(smooth), "name": "source.png"}],
        "log": [],
    }

    sent = direct_image_skills._send_results(run)

    assert sent == [str(result_image)]
    assert calls == [("oc_test", result_image, "source_processed.png")]
    assert run["images"][0]["result_path"] == str(result_image)


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


def test_direct_status_formatter_includes_cherry_plan() -> None:
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
                {"frame_count": 10, "aspect": "square", "cherry_profile": "half", "cherry_output_size": "256x256"},
                {"frame_count": 12, "aspect": "portrait", "cherry_profile": "full", "cherry_output_size": "384x512"},
            ],
            "children": {"comfyui": {"completed": 1, "total": 22, "status": "RUNNING"}},
        },
    }

    text = format_skill_results([result])

    assert "⌨️ VID_TEST" in text
    assert "正方形 256x256×1，长方形 384x512×1" in text


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


def test_multimodal_planner_analyzes_image_content() -> None:
    path = Path("E:/assetclaw-matting-bot/storage/debug/mm_analyze.png")
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

    init_db(Path("E:/assetclaw-matting-bot/data/test_assetclaw.db"))
    create_tables()
    path = Path("E:/assetclaw-matting-bot/storage/debug/mm_recent_analyze.png")
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

    init_db(Path("E:/assetclaw-matting-bot/data/test_assetclaw.db"))
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
