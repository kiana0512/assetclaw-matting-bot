from __future__ import annotations

from pathlib import Path

from assetclaw_matting.brain.router import handle_message
from assetclaw_matting.brain.schemas import BrainMessage
from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import init_db


def setup_module() -> None:
    init_db(Path("E:/assetclaw-matting-bot/data/test_assetclaw.db"))
    create_tables()


def test_local_command_fallback_available(monkeypatch) -> None:
    from assetclaw_matting.config import settings

    monkeypatch.setattr(settings, "brain_provider", "local_command")
    response = handle_message(BrainMessage(text="看看 E 盘有哪些文件"))
    assert response.provider == "local_command"
    assert response.text


def test_llm_proxy_unconfigured_does_not_crash(monkeypatch) -> None:
    from assetclaw_matting.config import settings

    monkeypatch.setattr(settings, "brain_provider", "llm_proxy")
    monkeypatch.setattr(settings, "llm_proxy_api_key", "")
    response = handle_message(BrainMessage(text="看看 E 盘有哪些文件"))
    assert response.text


def test_local_command_answers_previous_question(monkeypatch) -> None:
    from assetclaw_matting.config import settings
    from assetclaw_matting.db.repos import insert_brain_message

    monkeypatch.setattr(settings, "brain_provider", "local_command")
    insert_brain_message(
        provider="test",
        channel="feishu",
        conversation_id="prev-question-test",
        user_id="user",
        message_text="那你可以列出e盘全部的图片文件吗",
        response_text="E:\\ 找到 4 项",
        tool_calls_json="[]",
        raw_json="{}",
    )
    response = handle_message(
        BrainMessage(
            conversation_id="prev-question-test",
            user_id="user",
            text="你知道我上个问题是什么吗",
        )
    )
    assert "那你可以列出e盘全部的图片文件吗" in response.text


def test_local_command_completes_folder_and_copy_followup(monkeypatch) -> None:
    from pathlib import Path

    from PIL import Image

    from assetclaw_matting.config import settings
    from assetclaw_matting.db.repos import insert_brain_message

    monkeypatch.setattr(settings, "brain_provider", "local_command")
    src = Path("E:/pytest_compound_image.png")
    dst_dir = Path("E:/pytest_images")
    dst = dst_dir / src.name
    Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(src)
    if dst.exists():
        dst.unlink()

    conversation_id = "copy-followup-test"
    insert_brain_message(
        provider="test",
        channel="feishu",
        conversation_id=conversation_id,
        user_id="user",
        message_text="把这个图片文件发给我 pytest_compound_image.png",
        response_text="已发送文件：pytest_compound_image.png",
        tool_calls_json="[]",
        raw_json="{}",
    )
    ask = handle_message(
        BrainMessage(
            conversation_id=conversation_id,
            user_id="user",
            text="你可以在e盘新增一个文件夹并且把我刚刚提到的这个图片文件复制进去吗",
        )
    )
    assert "新文件夹叫什么名字" in ask.text

    done = handle_message(
        BrainMessage(
            conversation_id=conversation_id,
            user_id="user",
            text="对 就是这个意思 新文件夹就叫pytest_images",
        )
    )
    assert "已创建目录：E:\\pytest_images" in done.text
    assert f"已复制：E:\\pytest_images\\{src.name}" in done.text
    assert dst.exists()


def test_recall_answers_what_you_asked_me(monkeypatch) -> None:
    from assetclaw_matting.config import settings
    from assetclaw_matting.db.repos import insert_brain_message

    monkeypatch.setattr(settings, "brain_provider", "local_command")
    conversation_id = "intent-recall-test"
    insert_brain_message(
        provider="test",
        channel="feishu",
        conversation_id=conversation_id,
        user_id="user",
        message_text="那你可以把我们刚刚新增的文件夹连同它里面含有的文件一并复制到这个f的目录下吗",
        response_text="我理解了。",
        tool_calls_json="[]",
        raw_json="{}",
    )
    response = handle_message(
        BrainMessage(
            conversation_id=conversation_id,
            user_id="user",
            text="你知道我要你干嘛吗",
        )
    )
    assert "复制到这个f的目录下" in response.text


def test_plan_copy_recent_folder_to_f_drive() -> None:
    from assetclaw_matting.brain.file_task_planner import plan_file_task
    from assetclaw_matting.db.repos import insert_brain_message

    conversation_id = "copy-recent-folder-plan-test"
    insert_brain_message(
        provider="test",
        channel="feishu",
        conversation_id=conversation_id,
        user_id="user",
        message_text="对 就是这个意思 新文件夹就叫images",
        response_text="已创建目录：E:\\images\n已复制：E:\\images\\a.png",
        tool_calls_json="[]",
        raw_json="{}",
    )
    planned = plan_file_task(
        BrainMessage(
            conversation_id=conversation_id,
            user_id="user",
            text="那你可以把我们刚刚新增的文件夹连同它里面含有的文件一并复制到这个f的目录下吗",
        )
    )
    assert planned is not None
    tool_calls, _ = planned
    assert tool_calls
    assert tool_calls[0].skill == "file.copy_tree"
    assert tool_calls[0].arguments["src_path"] == "E:\\images"
    assert tool_calls[0].arguments["dst_path"] == "F:\\images"


def test_plan_rename_recent_images_sequence() -> None:
    from assetclaw_matting.brain.file_task_planner import plan_file_task
    from assetclaw_matting.db.repos import insert_brain_message

    conversation_id = "rename-recent-images-plan-test"
    insert_brain_message(
        provider="test",
        channel="feishu",
        conversation_id=conversation_id,
        user_id="user",
        message_text="列出e盘全部的图片",
        response_text=(
            "E:\\ 找到 3 项：\n"
            ".png img_a.png\n"
            ".jpg img_b.jpg\n"
            ".webp img_c.webp"
        ),
        tool_calls_json="[]",
        raw_json="{}",
    )
    planned = plan_file_task(
        BrainMessage(
            conversation_id=conversation_id,
            user_id="user",
            text="把这些图片按照排列的先后顺序修改名字为 123",
        )
    )
    assert planned is not None
    tool_calls, _ = planned
    assert tool_calls
    assert tool_calls[0].skill == "file.rename_sequence"
    assert tool_calls[0].arguments["paths"] == ["E:\\img_a.png", "E:\\img_b.jpg", "E:\\img_c.webp"]


def test_plan_zip_recent_input_and_send() -> None:
    from assetclaw_matting.brain.file_task_planner import plan_file_task
    from assetclaw_matting.db.repos import insert_brain_message

    conversation_id = "zip-recent-input-plan-test"
    insert_brain_message(
        provider="test",
        channel="feishu",
        conversation_id=conversation_id,
        user_id="user",
        message_text="列出共享盘抠图目录有哪些文件",
        response_text="\n".join([
            "Z:\\公共机共享\\抠图：18 项",
            "- input\\",
            "- output\\",
        ]),
        tool_calls_json="[]",
        raw_json="{}",
    )

    planned = plan_file_task(
        BrainMessage(
            conversation_id=conversation_id,
            user_id="user",
            text="可以把这个input文件夹压缩为zip并且发送给我吗",
        )
    )

    assert planned is not None
    tool_calls, _ = planned
    assert tool_calls[0].skill == "feishu.zip_and_send"
    assert tool_calls[0].arguments["paths"] == ["Z:\\公共机共享\\抠图\\input"]


def test_local_command_shared_drive_permission_question(monkeypatch) -> None:
    from assetclaw_matting.config import settings

    monkeypatch.setattr(settings, "brain_provider", "local_command")
    response = handle_message(BrainMessage(text="你可以查看共享盘的文件吗"))

    assert response.text


def test_local_command_shared_drive_list(monkeypatch) -> None:
    from assetclaw_matting.config import settings
    from assetclaw_matting.brain.local_command_brain import LocalCommandBrain

    monkeypatch.setattr(settings, "brain_provider", "local_command")
    tool_calls = LocalCommandBrain()._infer_tool_calls("列出共享盘有哪些文件")

    assert tool_calls[0].skill == "file.list_allowed"
    assert tool_calls[0].arguments["path"] == settings.shared_matting_root


def test_local_command_z_drive_list() -> None:
    from assetclaw_matting.brain.local_command_brain import LocalCommandBrain

    tool_calls = LocalCommandBrain()._infer_tool_calls("列出 Z 盘有哪些文件")

    assert tool_calls[0].skill == "file.list_allowed"
    assert tool_calls[0].arguments["path"] == "Z:\\"
