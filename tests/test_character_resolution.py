from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from PIL import Image

from assetclaw_matting.config import settings
from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import init_db
from assetclaw_matting.services.character_resolution import (
    _parse_assignments,
    all_run_units_frozen,
    bind_run_items,
    get_run_resolutions,
    initialize_run_resolutions,
    try_resolve_reply,
)
from assetclaw_matting.services import character_resolution


def _write_reference(path: Path, size: tuple[int, int], color: tuple[int, int, int, int]) -> None:
    Image.new("RGBA", size, color).save(path)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _setup(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    full_refs = tmp_path / "CharactorFull"
    emoji_refs = tmp_path / "CharactorEmoji"
    full_refs.mkdir()
    emoji_refs.mkdir()

    _write_reference(full_refs / "huggy.png", (384, 512), (10, 20, 230, 255))
    _write_reference(full_refs / "tasha.png", (384, 512), (220, 20, 30, 255))
    _write_reference(emoji_refs / "tasha.png", (256, 256), (20, 220, 30, 255))

    monkeypatch.setattr(settings, "cherry_character_full_reference_dir", full_refs)
    monkeypatch.setattr(settings, "cherry_character_emoji_reference_dir", emoji_refs)
    # Keep the legacy setting deterministic while the compatibility alias exists.
    monkeypatch.setattr(settings, "cherry_character_reference_dir", full_refs)
    init_db(tmp_path / "assetclaw.db")
    create_tables()
    return full_refs, emoji_refs


def test_filename_match_freezes_reference_and_binds_item(monkeypatch, tmp_path: Path) -> None:
    _setup(monkeypatch, tmp_path)
    run_dir = tmp_path / "runs" / "VID_MATCH"
    state = initialize_run_resolutions(
        run_kind="direct_video",
        run_id="VID_MATCH",
        run_dir=run_dir,
        conversation_id="feishu:chat:user",
        chat_id="chat",
        user_id="user",
        units=[
            {
                "unit_id": "VID_MATCH:video:01",
                "item_index": 1,
                "group_key": "video:1",
                "source_name": "2校色 huggy (1).mp4",
                "evidence": ["2校色 huggy (1).mp4"],
            }
        ],
    )

    assert state["prompt"] == ""
    assert all_run_units_frozen("direct_video", "VID_MATCH") is True
    row = get_run_resolutions("direct_video", "VID_MATCH")[0]
    assert row["character_id"] == "huggy"
    assert row["status"] == "FROZEN"

    item = {
        "character_unit_id": "VID_MATCH:video:01",
        "source_name": "2校色 huggy (1).mp4",
        "cherry_profile": "full",
    }
    result = bind_run_items("direct_video", "VID_MATCH", [item])
    assert result["ready"] is True
    assert item["character_id"] == "huggy"
    assert item["color_correction_status"] == "READY"
    assert item["position_alignment_enabled"] is True
    assert Path(item["color_reference_path"]).is_file()
    assert item["color_reference_sha256"] == _sha256(Path(item["color_reference_path"]))


def test_multiple_pending_units_require_tokens_and_allow_reverse_order(monkeypatch, tmp_path: Path) -> None:
    _setup(monkeypatch, tmp_path)
    run_dir = tmp_path / "runs" / "VID_PENDING"
    state = initialize_run_resolutions(
        run_kind="direct_video",
        run_id="VID_PENDING",
        run_dir=run_dir,
        conversation_id="feishu:chat:user",
        chat_id="chat",
        user_id="user",
        units=[
            {"unit_id": "VID_PENDING:video:01", "item_index": 1, "group_key": "v1", "source_name": "2-3.mp4", "evidence": ["2-3.mp4"]},
            {"unit_id": "VID_PENDING:video:02", "item_index": 2, "group_key": "v2", "source_name": "5.mp4", "evidence": ["5.mp4"]},
        ],
    )
    tokens = [item["question_token"] for item in state["pending"]]
    assert len(tokens) == 2
    assert all(token in state["prompt"] for token in tokens)

    bare = try_resolve_reply(
        conversation_id="feishu:chat:user",
        user_id="user",
        message_id="om_bare",
        text="huggy",
    )
    assert bare["handled"] is True
    assert bare["ok"] is False
    assert all_run_units_frozen("direct_video", "VID_PENDING") is False

    reply = try_resolve_reply(
        conversation_id="feishu:chat:user",
        user_id="user",
        message_id="om_mapping",
        text=f"{tokens[1]}=tasha {tokens[0]}=huggy",
    )
    assert reply["ok"] is True
    assert all_run_units_frozen("direct_video", "VID_PENDING") is True
    rows = get_run_resolutions("direct_video", "VID_PENDING")
    assert [row["character_id"] for row in rows] == ["huggy", "tasha"]


def test_another_user_cannot_consume_pending_token(monkeypatch, tmp_path: Path) -> None:
    _setup(monkeypatch, tmp_path)
    state = initialize_run_resolutions(
        run_kind="direct_image",
        run_id="IMG_PRIVATE",
        run_dir=tmp_path / "runs" / "IMG_PRIVATE",
        conversation_id="feishu:chat:owner",
        chat_id="chat",
        user_id="owner",
        units=[
            {"unit_id": "IMG_PRIVATE:image:01", "item_index": 1, "group_key": "i1", "source_name": "0000.png", "evidence": ["0000.png"]}
        ],
    )
    token = state["pending"][0]["question_token"]
    result = try_resolve_reply(
        conversation_id="feishu:chat:intruder",
        user_id="intruder",
        message_id="om_intruder",
        text=f"{token}=huggy",
    )
    assert result == {"handled": False}
    assert all_run_units_frozen("direct_image", "IMG_PRIVATE") is False


def test_natural_language_mention_is_not_treated_as_a_bare_answer(monkeypatch, tmp_path: Path) -> None:
    _setup(monkeypatch, tmp_path)
    initialize_run_resolutions(
        run_kind="direct_video",
        run_id="VID_NEGATION",
        run_dir=tmp_path / "runs" / "VID_NEGATION",
        conversation_id="feishu:chat:user",
        chat_id="chat",
        user_id="user",
        units=[{"unit_id": "VID_NEGATION:video:01", "item_index": 1, "source_name": "2-3.mp4", "evidence": ["2-3.mp4"]}],
    )

    result = try_resolve_reply(
        conversation_id="feishu:chat:user",
        user_id="user",
        message_id="om_sentence",
        text="这个不是 huggy，结果不对",
    )

    assert result == {"handled": False}
    assert all_run_units_frozen("direct_video", "VID_NEGATION") is False


def test_assignment_parser_accepts_copied_brackets_and_chinese_copula() -> None:
    assert _parse_assignments("[C-123456789ABC] = huggy") == {"C-123456789ABC": "huggy"}
    assert _parse_assignments("C-ABCDEF123456是tasha") == {"C-ABCDEF123456": "tasha"}


def test_same_character_uses_distinct_full_and_emoji_reference_snapshots(monkeypatch, tmp_path: Path) -> None:
    full_refs, emoji_refs = _setup(monkeypatch, tmp_path)
    initialize_run_resolutions(
        run_kind="direct_image",
        run_id="IMG_VARIANTS",
        run_dir=tmp_path / "runs" / "IMG_VARIANTS",
        conversation_id="feishu:chat:user",
        chat_id="chat",
        user_id="user",
        units=[
            {
                "unit_id": "IMG_VARIANTS:image:01",
                "item_index": 1,
                "group_key": "full",
                "source_name": "tasha full.png",
                "evidence": ["tasha full.png"],
            },
            {
                "unit_id": "IMG_VARIANTS:image:02",
                "item_index": 2,
                "group_key": "emoji",
                "source_name": "tasha emoji.png",
                "evidence": ["tasha emoji.png"],
            },
        ],
    )
    items = [
        {
            "character_unit_id": "IMG_VARIANTS:image:01",
            "source_name": "tasha full.png",
            "cherry_profile": "full",
        },
        {
            "character_unit_id": "IMG_VARIANTS:image:02",
            "source_name": "tasha emoji.png",
            "cherry_profile": "half",
        },
    ]

    result = bind_run_items("direct_image", "IMG_VARIANTS", items)

    assert result["ready"] is True
    full_snapshot = Path(items[0]["color_reference_path"])
    emoji_snapshot = Path(items[1]["color_reference_path"])
    assert full_snapshot != emoji_snapshot
    assert items[0]["color_reference_sha256"] == _sha256(full_refs / "tasha.png")
    assert items[1]["color_reference_sha256"] == _sha256(emoji_refs / "tasha.png")
    assert items[0]["color_reference_sha256"] != items[1]["color_reference_sha256"]
    with Image.open(full_snapshot) as image:
        assert image.size == (384, 512)
    with Image.open(emoji_snapshot) as image:
        assert image.size == (256, 256)


def test_one_character_unit_can_bind_mixed_profiles_without_crossing_references(monkeypatch, tmp_path: Path) -> None:
    full_refs, emoji_refs = _setup(monkeypatch, tmp_path)
    initialize_run_resolutions(
        run_kind="direct_image",
        run_id="IMG_MIXED_UNIT",
        run_dir=tmp_path / "runs" / "IMG_MIXED_UNIT",
        conversation_id="feishu:chat:user",
        chat_id="chat",
        user_id="user",
        units=[
            {
                "unit_id": "IMG_MIXED_UNIT:sequence:01",
                "item_index": 1,
                "group_key": "sequence:tasha",
                "source_name": "tasha.zip",
                "evidence": ["tasha.zip"],
            }
        ],
    )
    items = [
        {
            "character_unit_id": "IMG_MIXED_UNIT:sequence:01",
            "source_name": "frame_full.png",
            "cherry_profile": "full",
        },
        {
            "character_unit_id": "IMG_MIXED_UNIT:sequence:01",
            "source_name": "frame_emoji.png",
            "cherry_profile": "half",
        },
    ]

    result = bind_run_items("direct_image", "IMG_MIXED_UNIT", items)

    assert result["ready"] is True
    assert items[0]["character_id"] == items[1]["character_id"] == "tasha"
    assert items[0]["color_reference_path"] != items[1]["color_reference_path"]
    assert items[0]["color_reference_sha256"] == _sha256(full_refs / "tasha.png")
    assert items[1]["color_reference_sha256"] == _sha256(emoji_refs / "tasha.png")


def test_missing_emoji_reference_fails_closed_instead_of_using_full_reference(monkeypatch, tmp_path: Path) -> None:
    full_refs, _emoji_refs = _setup(monkeypatch, tmp_path)
    initialize_run_resolutions(
        run_kind="direct_image",
        run_id="IMG_MISSING_EMOJI",
        run_dir=tmp_path / "runs" / "IMG_MISSING_EMOJI",
        conversation_id="feishu:chat:user",
        chat_id="chat",
        user_id="user",
        units=[
            {
                "unit_id": "IMG_MISSING_EMOJI:image:01",
                "item_index": 1,
                "source_name": "huggy.png",
                "evidence": ["huggy.png"],
            }
        ],
    )
    item = {
        "character_unit_id": "IMG_MISSING_EMOJI:image:01",
        "source_name": "huggy.png",
        "cherry_profile": "half",
    }

    with pytest.raises(RuntimeError) as error:
        bind_run_items("direct_image", "IMG_MISSING_EMOJI", [item])

    message = str(error.value).lower()
    assert "huggy" in message
    assert any(marker in message for marker in ("half", "emoji", "256x256", "半身"))
    assert "color_reference_path" not in item
    assert str(full_refs / "huggy.png") not in str(item)


def test_video_without_profile_waits_then_binds_emoji_after_profile_detection(monkeypatch, tmp_path: Path) -> None:
    _full_refs, emoji_refs = _setup(monkeypatch, tmp_path)
    initialize_run_resolutions(
        run_kind="direct_video",
        run_id="VID_LATE_PROFILE",
        run_dir=tmp_path / "runs" / "VID_LATE_PROFILE",
        conversation_id="feishu:chat:user",
        chat_id="chat",
        user_id="user",
        units=[
            {
                "unit_id": "VID_LATE_PROFILE:video:01",
                "item_index": 1,
                "group_key": "video:1",
                "source_name": "tasha.mp4",
                "evidence": ["tasha.mp4"],
            }
        ],
    )
    item = {
        "character_unit_id": "VID_LATE_PROFILE:video:01",
        "source_name": "tasha.mp4",
        "cherry_profile": "",
    }

    waiting = bind_run_items("direct_video", "VID_LATE_PROFILE", [item])

    assert waiting["ready"] is False
    assert "color_reference_path" not in item
    assert "color_reference_sha256" not in item

    item["cherry_profile"] = "half"
    bound = bind_run_items("direct_video", "VID_LATE_PROFILE", [item])

    assert bound["ready"] is True
    assert item["color_reference_sha256"] == _sha256(emoji_refs / "tasha.png")
    with Image.open(item["color_reference_path"]) as image:
        assert image.size == (256, 256)


def test_bound_variant_snapshot_survives_reference_hot_update(monkeypatch, tmp_path: Path) -> None:
    full_refs, _emoji_refs = _setup(monkeypatch, tmp_path)
    initialize_run_resolutions(
        run_kind="direct_image",
        run_id="IMG_HOT_UPDATE",
        run_dir=tmp_path / "runs" / "IMG_HOT_UPDATE",
        conversation_id="feishu:chat:user",
        chat_id="chat",
        user_id="user",
        units=[
            {
                "unit_id": "IMG_HOT_UPDATE:image:01",
                "item_index": 1,
                "source_name": "tasha.png",
                "evidence": ["tasha.png"],
            }
        ],
    )
    first_item = {
        "character_unit_id": "IMG_HOT_UPDATE:image:01",
        "source_name": "tasha.png",
        "cherry_profile": "full",
    }
    assert bind_run_items("direct_image", "IMG_HOT_UPDATE", [first_item])["ready"] is True
    frozen_path = Path(first_item["color_reference_path"])
    frozen_sha = first_item["color_reference_sha256"]
    frozen_bytes = frozen_path.read_bytes()

    _write_reference(full_refs / "tasha.png", (384, 512), (5, 6, 7, 255))
    assert _sha256(full_refs / "tasha.png") != frozen_sha
    second_item = {
        "character_unit_id": "IMG_HOT_UPDATE:image:01",
        "source_name": "tasha.png",
        "cherry_profile": "full",
    }

    assert bind_run_items("direct_image", "IMG_HOT_UPDATE", [second_item])["ready"] is True
    assert Path(second_item["color_reference_path"]) == frozen_path
    assert second_item["color_reference_sha256"] == frozen_sha
    assert frozen_path.read_bytes() == frozen_bytes


def test_tampered_variant_snapshot_is_rejected(monkeypatch, tmp_path: Path) -> None:
    _setup(monkeypatch, tmp_path)
    initialize_run_resolutions(
        run_kind="direct_image",
        run_id="IMG_TAMPERED",
        run_dir=tmp_path / "runs" / "IMG_TAMPERED",
        conversation_id="feishu:chat:user",
        chat_id="chat",
        user_id="user",
        units=[
            {
                "unit_id": "IMG_TAMPERED:image:01",
                "item_index": 1,
                "source_name": "tasha.png",
                "evidence": ["tasha.png"],
            }
        ],
    )
    first_item = {
        "character_unit_id": "IMG_TAMPERED:image:01",
        "source_name": "tasha.png",
        "cherry_profile": "half",
    }
    assert bind_run_items("direct_image", "IMG_TAMPERED", [first_item])["ready"] is True
    Path(first_item["color_reference_path"]).write_bytes(b"tampered-reference")
    retry_item = {
        "character_unit_id": "IMG_TAMPERED:image:01",
        "source_name": "tasha.png",
        "cherry_profile": "half",
    }

    with pytest.raises(RuntimeError) as error:
        bind_run_items("direct_image", "IMG_TAMPERED", [retry_item])

    message = str(error.value).lower()
    assert any(marker in message for marker in ("snapshot", "sha", "changed", "tamper", "篡改"))
    assert "color_reference_path" not in retry_item


def test_snapshot_failure_leaves_user_assignment_pending(monkeypatch, tmp_path: Path) -> None:
    _setup(monkeypatch, tmp_path)
    state = initialize_run_resolutions(
        run_kind="direct_image",
        run_id="IMG_RETRYABLE",
        run_dir=tmp_path / "runs" / "IMG_RETRYABLE",
        conversation_id="feishu:chat:user",
        chat_id="chat",
        user_id="user",
        units=[{"unit_id": "IMG_RETRYABLE:image:01", "item_index": 1, "source_name": "0000.png", "evidence": ["0000.png"]}],
    )
    token = state["pending"][0]["question_token"]
    monkeypatch.setattr(character_resolution, "_snapshot_reference", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")))

    try:
        try_resolve_reply(
            conversation_id="feishu:chat:user",
            user_id="user",
            message_id="om_retry",
            text=f"{token}=huggy",
        )
    except OSError as exc:
        assert "disk full" in str(exc)
    else:
        raise AssertionError("snapshot failure must be surfaced")

    row = get_run_resolutions("direct_image", "IMG_RETRYABLE")[0]
    assert row["status"] == "PENDING"
