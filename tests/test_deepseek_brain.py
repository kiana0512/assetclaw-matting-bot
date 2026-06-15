from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests

from assetclaw_matting.brain.deepseek_brain import DeepSeekBrain
from assetclaw_matting.brain.pre_llm_router import handle_pre_llm_message
from assetclaw_matting.brain.router import get_provider, handle_message
from assetclaw_matting.brain.schemas import BrainMessage, BrainResponse, ToolCall
from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import init_db


def setup_module() -> None:
    init_db(Path("E:/assetclaw-matting-bot/data/test_assetclaw.db"))
    create_tables()


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.reason = text

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            exc = requests.HTTPError(str(self.status_code))
            exc.response = self
            raise exc


def _configure(monkeypatch) -> None:
    from assetclaw_matting.config import settings

    monkeypatch.setattr(settings, "deepseek_api_key", "sk-test-secret")
    monkeypatch.setattr(settings, "deepseek_base_url", "https://api.deepseek.com")
    monkeypatch.setattr(settings, "deepseek_model", "deepseek-v4-pro")
    monkeypatch.setattr(settings, "deepseek_router_model", "deepseek-v4-flash")
    monkeypatch.setattr(settings, "deepseek_summary_model", "deepseek-v4-pro")
    monkeypatch.setattr(settings, "deepseek_thinking_type", "disabled")
    monkeypatch.setattr(settings, "deepseek_reasoning_effort", "medium")
    monkeypatch.setattr(settings, "deepseek_stream", False)
    monkeypatch.setattr(settings, "deepseek_temperature", 0.1)
    monkeypatch.setattr(settings, "deepseek_timeout_seconds", 3)
    monkeypatch.setattr(settings, "deepseek_max_retries", 1)


def test_deepseek_builds_openai_chat_request(monkeypatch) -> None:
    _configure(monkeypatch)
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured.update({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return FakeResponse(200, {"choices": [{"message": {"content": "{\"type\":\"final\",\"content\":\"ok\"}"}}]})

    monkeypatch.setattr(requests, "post", fake_post)
    text = DeepSeekBrain()._complete([{"role": "user", "content": "ping"}], "tool")

    assert json.loads(text)["type"] == "final"
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-test-secret"
    assert captured["json"]["model"] == "deepseek-v4-flash"
    assert captured["json"]["stream"] is False
    assert captured["json"]["thinking"] == {"type": "disabled"}
    assert captured["json"]["response_format"] == {"type": "json_object"}


def test_deepseek_thinking_enabled(monkeypatch) -> None:
    _configure(monkeypatch)
    from assetclaw_matting.config import settings

    monkeypatch.setattr(settings, "deepseek_thinking_type", "enabled")
    monkeypatch.setattr(settings, "deepseek_reasoning_effort", "high")
    payload = DeepSeekBrain()._build_payload([{"role": "user", "content": "ping"}], purpose="summary")

    assert payload["model"] == "deepseek-v4-pro"
    assert payload["thinking"] == {"type": "enabled"}
    assert payload["reasoning_effort"] == "high"


def test_deepseek_response_format_fallback_on_400(monkeypatch) -> None:
    _configure(monkeypatch)
    calls = []

    def fake_post(_url, headers, json, timeout):
        calls.append(dict(json))
        if len(calls) == 1:
            return FakeResponse(400, text="response_format unsupported")
        return FakeResponse(200, {"choices": [{"message": {"content": "{\"type\":\"final\",\"content\":\"ok\"}"}}]})

    monkeypatch.setattr(requests, "post", fake_post)
    assert DeepSeekBrain()._complete([{"role": "user", "content": "ping"}], "tool")
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]


def test_deepseek_parses_router_formats_and_ignores_reasoning() -> None:
    brain = DeepSeekBrain()
    parsed = brain._parse_json('{"type":"tool_calls","tool_calls":[{"name":"file.list_allowed","arguments":{"path":"E:\\\\"}}]}')
    assert parsed["tool_calls"][0]["skill"] == "file.list_allowed"
    assert parsed["tool_calls"][0]["arguments"]["path"] == "E:\\"

    final = brain._parse_json('{"type":"final","content":"你好"}')
    assert final == {"tool_calls": [], "text": "你好"}

    data = {
        "choices": [
            {
                "message": {
                    "reasoning_content": "hidden",
                    "content": "{\"type\":\"final\",\"content\":\"visible\"}",
                }
            }
        ]
    }
    assert brain._extract_message_content(data) == "{\"type\":\"final\",\"content\":\"visible\"}"


def test_deepseek_parses_openai_tool_calls() -> None:
    data = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "file.list_allowed",
                                "arguments": "{\"path\":\"E:\\\\\"}",
                            }
                        }
                    ]
                }
            }
        ]
    }
    parsed = DeepSeekBrain()._parse_json(DeepSeekBrain()._extract_message_content(data))
    assert parsed["tool_calls"][0]["skill"] == "file.list_allowed"


def test_deepseek_401_redacts_key(monkeypatch) -> None:
    _configure(monkeypatch)
    response = FakeResponse(401, text='{"error":"bad sk-test-secret"}')
    exc = requests.HTTPError("401")
    exc.response = response

    text = DeepSeekBrain()._format_http_error(exc)
    assert "DEEPSEEK_API_KEY" in text
    assert "sk-test-secret" not in text
    assert "[REDACTED]" in text


def test_deepseek_retries_timeout(monkeypatch) -> None:
    _configure(monkeypatch)
    calls = {"count": 0}

    def fake_post(*_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise requests.Timeout("slow")
        return FakeResponse(200, {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(requests, "post", fake_post)
    assert DeepSeekBrain()._complete([{"role": "user", "content": "ping"}], "summary") == "ok"
    assert calls["count"] >= 2


def test_router_selects_deepseek(monkeypatch) -> None:
    _configure(monkeypatch)
    from assetclaw_matting.config import settings

    monkeypatch.setattr(settings, "brain_provider", "deepseek")
    assert get_provider().name == "deepseek"


def test_deepseek_brain_generates_tool_call(monkeypatch) -> None:
    _configure(monkeypatch)
    from assetclaw_matting.config import settings

    monkeypatch.setattr(settings, "brain_provider", "deepseek")
    monkeypatch.setattr(
        DeepSeekBrain,
        "_complete",
        lambda self, messages, model_kind: '{"type":"tool_calls","tool_calls":[{"name":"file.list_allowed","arguments":{"path":"E:\\\\"}}]}',
    )
    response = handle_message(BrainMessage(text="看看 E 盘有哪些文件"))
    assert response.provider == "deepseek"
    assert response.tool_calls[0].skill == "file.list_allowed"


def test_animation_flow_command_is_routed_before_deepseek_llm() -> None:
    class FakeDeepSeekProvider:
        name = "deepseek"

        def __init__(self) -> None:
            self.calls: list[ToolCall] = []
            self.logged: BrainResponse | None = None

        def execute_tool_calls(
            self,
            tool_calls: list[ToolCall],
            conversation_id: str = "",
            user_id: str = "",
        ) -> list[dict]:
            self.calls = tool_calls
            return [{"ok": True, "skill": tool_calls[0].skill, "result": {"ok": True, "run_id": "AFLOW_TEST"}}]

        def log_message(self, message: BrainMessage, response: BrainResponse, raw: dict | None = None) -> None:
            self.logged = response

    provider = FakeDeepSeekProvider()
    response = handle_pre_llm_message(
        provider,
        BrainMessage(conversation_id="c", user_id="u", text="启动动画流程 20260610 替换 faker"),
    )

    assert response is not None
    assert provider.calls[0].skill == "animation_flow.start"
    assert provider.calls[0].arguments["date_root"] == "E:\\animation_automation\\2026-06-10"
    assert provider.calls[0].arguments["unity_import_mode"] == "iteration"
    assert provider.calls[0].arguments["fake_matting_from_frames"] is True
    assert response.raw["deterministic_plan"] == "deterministic animation_flow route before LLM"


def test_dangerous_skill_still_requires_confirmation(monkeypatch) -> None:
    _configure(monkeypatch)
    from assetclaw_matting.config import settings

    monkeypatch.setattr(settings, "brain_provider", "deepseek")
    monkeypatch.setattr(
        DeepSeekBrain,
        "_complete",
        lambda self, messages, model_kind: '{"type":"tool_calls","tool_calls":[{"name":"file.delete","arguments":{"path":"E:\\\\danger.txt"}}]}',
    )
    response = handle_message(BrainMessage(conversation_id="deepseek-danger", user_id="u", text="删除 E:\\danger.txt"))
    assert response.tool_calls[0].skill == "file.delete"
    assert "确认" in response.text
