from __future__ import annotations

from pathlib import Path

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


def _setup(monkeypatch, tmp_path: Path) -> Path:
    refs = tmp_path / "Charactor"
    refs.mkdir()
    (refs / "huggy.png").write_bytes(b"huggy-reference")
    (refs / "tasha.png").write_bytes(b"tasha-reference")
    monkeypatch.setattr(settings, "cherry_character_reference_dir", refs)
    init_db(tmp_path / "assetclaw.db")
    create_tables()
    return refs


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
    assert Path(row["reference_snapshot_path"]).is_file()

    item = {"character_unit_id": "VID_MATCH:video:01", "source_name": "2校色 huggy (1).mp4"}
    result = bind_run_items("direct_video", "VID_MATCH", [item])
    assert result["ready"] is True
    assert item["character_id"] == "huggy"
    assert item["color_correction_status"] == "READY"
    assert item["position_alignment_enabled"] is True


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
