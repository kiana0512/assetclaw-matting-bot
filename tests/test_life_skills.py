from __future__ import annotations

from pathlib import Path

from assetclaw_matting.brain.deepseek_brain import DeepSeekBrain
from assetclaw_matting.brain.local_command_brain import LocalCommandBrain
from assetclaw_matting.brain.result_formatter import format_skill_results
from assetclaw_matting.brain.schemas import BrainMessage
from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import init_db
from assetclaw_matting.runtime_context import reset_runtime_context, set_runtime_context
from assetclaw_matting.skills.registry import call_skill, get_skill_meta


def setup_module() -> None:
    init_db(Path("E:/assetclaw-matting-bot/data/test_assetclaw.db"))
    create_tables()


def test_life_skills_registered() -> None:
    assert get_skill_meta("life.location")
    assert get_skill_meta("life.set_location")
    assert get_skill_meta("life.weather")
    assert get_skill_meta("life.food_suggest")


def test_location_can_be_remembered_per_conversation() -> None:
    token = set_runtime_context(conversation_id="life-test-chat")
    try:
        remembered = call_skill("life.set_location", {"location": "徐汇"}, requested_by="test")
        assert remembered["ok"] is True
        result = call_skill("life.location", {}, requested_by="test")
    finally:
        reset_runtime_context(token)
    assert result["ok"] is True
    assert result["result"]["location"] == "徐汇"
    text = format_skill_results([result])
    assert "徐汇" in text


def test_weather_uses_wttr_json(monkeypatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {
                "current_condition": [
                    {
                        "temp_C": "26",
                        "FeelsLikeC": "27",
                        "humidity": "70",
                        "windspeedKmph": "9",
                        "lang_zh": [{"value": "小雨"}],
                    }
                ],
                "weather": [{"hourly": [{"chanceofrain": "80"}, {"chanceofrain": "20"}]}],
            }

    import assetclaw_matting.skills.life_skills as life_skills

    monkeypatch.setattr(life_skills.requests, "get", lambda *args, **kwargs: FakeResponse())
    result = call_skill("life.weather", {"location": "上海"}, requested_by="test")
    assert result["ok"] is True
    payload = result["result"]
    assert payload["location"] == "上海"
    assert payload["condition"] == "小雨"
    assert payload["rain_chance"] == "80"
    text = format_skill_results([result])
    assert "上海现在" in text
    assert "带伞" in text


def test_food_suggestion_routes_through_life_skill() -> None:
    response = LocalCommandBrain().handle_message(BrainMessage(text="中午点什么外卖", conversation_id="food-chat"))
    assert response.tool_calls
    assert response.tool_calls[0].skill == "life.food_suggest"
    assert "午饭" in response.text or "中午" in response.text
    assert "外卖" in response.text or "盖饭" in response.text or "牛肉面" in response.text


def test_location_and_weather_route_before_llm(monkeypatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {
                "current_condition": [{"temp_C": "31", "FeelsLikeC": "34", "humidity": "60", "weatherDesc": [{"value": "Sunny"}]}],
                "weather": [{"hourly": [{"chanceofrain": "10"}]}],
            }

    import assetclaw_matting.skills.life_skills as life_skills

    monkeypatch.setattr(life_skills.requests, "get", lambda *args, **kwargs: FakeResponse())
    brain = DeepSeekBrain()
    set_response = brain.handle_message(BrainMessage(text="我在杭州", conversation_id="life-llm-chat"))
    assert set_response.tool_calls[0].skill == "life.set_location"
    where = brain.handle_message(BrainMessage(text="我在哪", conversation_id="life-llm-chat"))
    assert where.tool_calls[0].skill == "life.location"
    assert "杭州" in where.text
    weather = brain.handle_message(BrainMessage(text="今天天气怎么样", conversation_id="life-llm-chat"))
    assert weather.tool_calls[0].skill == "life.weather"
    assert "杭州" in weather.text


def test_feishu_runtime_context_does_not_duplicate_conversation_id(monkeypatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {
                "current_condition": [{"temp_C": "18", "FeelsLikeC": "18", "lang_zh": [{"value": "小雨"}]}],
                "weather": [{"hourly": [{"chanceofrain": "70"}]}],
            }

    import assetclaw_matting.skills.life_skills as life_skills

    monkeypatch.setattr(life_skills.requests, "get", lambda *args, **kwargs: FakeResponse())
    token = set_runtime_context(channel="feishu", chat_id="chat-test", conversation_id="feishu-context")
    try:
        response = LocalCommandBrain().handle_message(
            BrainMessage(text="今天下雨了怎么办", conversation_id="feishu-context", user_id="user-test")
        )
    finally:
        reset_runtime_context(token)
    assert response.tool_calls[0].skill == "life.weather"
    assert "带伞" in response.text
    assert "multiple values" not in response.text


def test_lyric_like_rainy_day_message_does_not_route_to_weather() -> None:
    response = LocalCommandBrain().handle_message(BrainMessage(text="下雨天了怎么办我好想你", conversation_id="lyric-chat"))
    assert not response.tool_calls
    assert "不敢打给你" in response.text
