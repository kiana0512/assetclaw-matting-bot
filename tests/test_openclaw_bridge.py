"""Tests for the OpenClaw bridge and message router."""
from __future__ import annotations

import pytest

import assetclaw_matting.db.sqlite as db_module


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    import assetclaw_matting.config as cfg_module
    from assetclaw_matting.config import Settings

    s = Settings(
        storage_dir=str(tmp_path / "storage"),
        data_dir=str(tmp_path / "data"),
        log_dir=str(tmp_path / "logs"),
        allowed_roots="",
        openclaw_enabled=False,
        openclaw_message_mode="local_command_first",
        comfyui_fake_mode=True,
    )
    monkeypatch.setattr(cfg_module, "settings", s)
    s.ensure_dirs()

    from assetclaw_matting.db.sqlite import init_db
    from assetclaw_matting.db.schema import create_tables
    db_path = s.data_dir / "test.db"
    init_db(db_path)
    create_tables()
    yield s
    db_module._db_path = None


# ── message_router.route ─────────────────────────────────────────────────────

def test_local_command_help(temp_db):
    from assetclaw_matting.openclaw.message_router import route
    reply = route(text="help", chat_id="c1", sender_id="u1")
    assert "help" in reply.lower() or "命令" in reply


def test_local_command_queue(temp_db):
    from assetclaw_matting.openclaw.message_router import route
    reply = route(text="queue", chat_id="c1", sender_id="u1")
    assert "QUEUED" in reply or "排队" in reply


def test_unknown_command_openclaw_disabled(temp_db):
    from assetclaw_matting.openclaw.message_router import route
    reply = route(text="帮我运行一下抠图任务", chat_id="c1", sender_id="u1")
    # Should return "OpenClaw not enabled" message
    assert "OpenClaw" in reply or "未启用" in reply


def test_relay_only_mode_with_openclaw_disabled(monkeypatch, temp_db):
    import assetclaw_matting.config as cfg_module
    from assetclaw_matting.config import Settings

    s = Settings(
        storage_dir=str(temp_db.storage_dir),
        data_dir=str(temp_db.data_dir),
        log_dir=str(temp_db.log_dir),
        allowed_roots="",
        openclaw_enabled=False,
        openclaw_message_mode="relay_only",
    )
    monkeypatch.setattr(cfg_module, "settings", s)

    from assetclaw_matting.openclaw.message_router import route
    reply = route(text="queue", chat_id="c1", sender_id="u1")
    assert "OpenClaw" in reply or "未启用" in reply


def test_batch_list_command(temp_db):
    from assetclaw_matting.openclaw.message_router import route
    reply = route(text="batch list", chat_id="c1", sender_id="u1")
    assert "批次" in reply or "batch" in reply.lower()


# ── OpenClaw client mock ──────────────────────────────────────────────────────

def test_openclaw_client_disabled_returns_mock():
    from assetclaw_matting.openclaw.client import OpenClawClient, _mock_response
    client = OpenClawClient()
    response = client.send_message("conv1", "user1", "test text")
    assert response.type == "text"
    assert "未启用" in response.text or "OPENCLAW_ENABLED" in response.text


def test_openclaw_client_processes_tool_call(monkeypatch, temp_db):
    """Simulate OpenClaw returning a tool_call and verify skill execution."""
    import assetclaw_matting.config as cfg_module
    from assetclaw_matting.config import Settings
    from assetclaw_matting.openclaw.schemas import OpenClawResponse, OpenClawToolCall

    s = Settings(
        storage_dir=str(temp_db.storage_dir),
        data_dir=str(temp_db.data_dir),
        log_dir=str(temp_db.log_dir),
        allowed_roots="",
        openclaw_enabled=True,
        openclaw_message_mode="relay_only",
        openclaw_base_url="http://mock.example.com",
        openclaw_api_key="mock_key",
    )
    monkeypatch.setattr(cfg_module, "settings", s)

    # Patch the client to return a tool_call response
    mock_response = OpenClawResponse(
        type="tool_call",
        text="Checking queue for you...",
        tool_calls=[OpenClawToolCall(skill="queue.status", arguments={})],
    )

    import assetclaw_matting.openclaw.client as oc_module
    import assetclaw_matting.openclaw.message_router as router_module

    monkeypatch.setattr(
        oc_module.openclaw_client, "send_message",
        lambda *a, **kw: mock_response,
    )
    monkeypatch.setattr(router_module, "openclaw_client", oc_module.openclaw_client)

    from assetclaw_matting.openclaw.message_router import route
    reply = route(text="any text", chat_id="c1", sender_id="u1")
    # Should contain the skill result
    assert "queue.status" in reply or "queue" in reply.lower() or "Checking" in reply
