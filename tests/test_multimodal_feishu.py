from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from assetclaw_matting.brain.multimodal_planner import plan_multimodal_task
from assetclaw_matting.brain.direct_video_planner import plan_direct_video_task
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
    client = FeishuClient()

    def fail_upload_file(path: Path, file_name: str | None = None) -> str:
        response = requests.Response()
        response.status_code = 400
        response._content = b'{"code":234006,"msg":"The file size exceed the max value."}'
        raise requests.HTTPError("upload_file failed: 400", response=response)

    monkeypatch.setattr(client, "upload_file", fail_upload_file)
    monkeypatch.setattr(client, "upload_drive_file", lambda path, file_name=None: {"url": "https://lilithgames.feishu.cn/file/token"})
    monkeypatch.setattr(client, "send_text_to_chat", lambda chat_id, text: sent.update({"chat_id": chat_id, "text": text}))

    client.send_file_to_chat("oc_test", target, target.name)

    assert sent["chat_id"] == "oc_test"
    assert "result.zip" in sent["text"]
    assert "https://lilithgames.feishu.cn/file/token" in sent["text"]


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
        return {"run_id": f"CHERRY_TEST_{len(calls)}"}

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
    assert "本地 ASR" in _processing_ack_text(voice_event)
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
    assert "DeepSeek 深度思考" in _processing_ack_text(text_event)
