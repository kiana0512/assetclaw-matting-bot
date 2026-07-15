from __future__ import annotations

from pathlib import Path

from PIL import Image

from assetclaw_matting.skills.media_skills import (
    feishu_send_image,
    feishu_send_image_by_name,
    feishu_send_file_by_name,
    file_copy_as,
    file_duplicate_same_dir,
    file_list_by_type,
    image_batch_info,
    image_convert_format,
    image_info,
    image_list,
    image_resize,
)
from assetclaw_matting.runtime_context import reset_runtime_context, set_runtime_context
from assetclaw_matting.skills.file_extra_skills import file_find_name


def _make_test_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "RGB" if path.suffix.lower() in {".jpg", ".jpeg"} else "RGBA"
    color = (255, 0, 0) if mode == "RGB" else (255, 0, 0, 255)
    Image.new(mode, (8, 6), color).save(path)


def test_image_list_and_info() -> None:
    path = Path.cwd() / "storage/debug/media_list_case/pytest_image.png"
    _make_test_image(path)

    listed = image_list(".\\storage\\debug\\media_list_case", max_results=20)
    assert listed["ok"] is True
    assert any(item["name"] == "pytest_image.png" for item in listed["items"])

    info = image_info(str(path))
    assert info["ok"] is True
    assert info["width"] == 8
    assert info["height"] == 6


def test_file_copy_as_and_duplicate_same_dir() -> None:
    src = Path.cwd() / "storage/debug/pytest_copy_as_src.png"
    _make_test_image(src)

    copied = file_copy_as(str(src), "pytest_copy_as_dst.png", overwrite=True)
    assert copied["ok"] is True
    assert Path(copied["dst_path"]).exists()

    duplicated = file_duplicate_same_dir(str(src), suffix="_bak", overwrite=True)
    assert duplicated["ok"] is True
    assert duplicated["dst_path"].endswith("pytest_copy_as_src_bak.png")


def test_file_list_by_type_tables() -> None:
    csv_path = Path.cwd() / "storage/debug/pytest_table.csv"
    csv_path.write_text("a,b\n1,2\n", encoding="utf-8")

    listed = file_list_by_type(".\\storage\\debug", kind="table")
    assert listed["ok"] is True
    assert any(item["name"] == "pytest_table.csv" for item in listed["items"])


def test_find_name_supports_ellipsis_pattern() -> None:
    path = Path.cwd() / "storage/debug/img_v3_02125_53d2b164_REAL_608g.png"
    _make_test_image(path)

    result = file_find_name(
        "img_v3_02125_53d2b164...608g.png",
        search_root=".\\storage\\debug",
    )
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["items"][0]["name"] == path.name


def test_feishu_send_file_by_name_supports_ellipsis(monkeypatch) -> None:
    path = Path.cwd() / "storage/debug/img_v3_02125_82151f84_REAL_c77g.jpg"
    _make_test_image(path)
    sent: dict[str, str] = {}

    def fake_send_file_to_chat(chat_id, target, file_name=None):
        sent["chat_id"] = chat_id
        sent["target"] = str(target)
        sent["file_name"] = file_name or target.name

    from assetclaw_matting.feishu.client import feishu_client

    monkeypatch.setattr(feishu_client, "send_file_to_chat", fake_send_file_to_chat)
    token = set_runtime_context(chat_id="chat_test")
    try:
        result = feishu_send_file_by_name(
            "img_v3_02125_82151f84...c77g.jpg",
            search_root=".\\storage\\debug",
        )
    finally:
        reset_runtime_context(token)

    assert result["ok"] is True
    assert sent["chat_id"] == "chat_test"
    assert sent["target"].endswith(path.name)


def test_feishu_send_image_sends_inline_image(monkeypatch) -> None:
    path = Path.cwd() / "storage/debug/inline_preview.png"
    _make_test_image(path)
    sent: dict[str, str] = {}

    def fake_send_image_to_chat(chat_id, target):
        sent["chat_id"] = chat_id
        sent["target"] = str(target)

    from assetclaw_matting.feishu.client import feishu_client

    monkeypatch.setattr(feishu_client, "send_image_to_chat", fake_send_image_to_chat)
    token = set_runtime_context(chat_id="chat_test")
    try:
        result = feishu_send_image(str(path))
    finally:
        reset_runtime_context(token)

    assert result["ok"] is True
    assert sent["chat_id"] == "chat_test"
    assert sent["target"].endswith("inline_preview.png")


def test_feishu_send_image_by_name(monkeypatch) -> None:
    path = Path.cwd() / "storage/debug/inline_name_preview.png"
    _make_test_image(path)
    sent: dict[str, str] = {}

    def fake_send_image_to_chat(chat_id, target):
        sent["chat_id"] = chat_id
        sent["target"] = str(target)

    from assetclaw_matting.feishu.client import feishu_client

    monkeypatch.setattr(feishu_client, "send_image_to_chat", fake_send_image_to_chat)
    token = set_runtime_context(chat_id="chat_test")
    try:
        result = feishu_send_image_by_name("inline_name_preview.png", search_root=".\\storage\\debug")
    finally:
        reset_runtime_context(token)

    assert result["ok"] is True
    assert sent["target"].endswith("inline_name_preview.png")


def test_image_batch_info_convert_and_resize() -> None:
    src = Path.cwd() / "storage/debug/media_ops_src.png"
    jpg = Path.cwd() / "storage/debug/media_ops_src.jpg"
    resized = Path.cwd() / "storage/debug/media_ops_small.png"
    _make_test_image(src)
    if jpg.exists():
        jpg.unlink()
    if resized.exists():
        resized.unlink()

    batch = image_batch_info([str(src)])
    assert batch["count"] == 1
    assert batch["items"][0]["width"] == 8

    converted = image_convert_format(str(src), str(jpg))
    assert converted["ok"] is True
    assert jpg.exists()

    output = image_resize(str(src), str(resized), width=4, height=3)
    assert output["ok"] is True
    assert image_info(str(resized))["width"] == 4
