from __future__ import annotations

from pathlib import Path

from assetclaw_matting.brain.deepseek_brain import DeepSeekBrain
from assetclaw_matting.brain.emotion_planner import plan_emotional_reply
from assetclaw_matting.brain.local_command_brain import LocalCommandBrain
from assetclaw_matting.brain.result_formatter import format_skill_results
from assetclaw_matting.brain.schemas import BrainMessage
from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import init_db
from assetclaw_matting.skills.registry import call_skill, get_skill_meta
from assetclaw_matting.skills.web_skills import fetch_url, research, search_web


def setup_module() -> None:
    init_db(Path("E:/assetclaw-matting-bot/data/test_assetclaw.db"))
    create_tables()


def test_emotional_insult_does_not_trigger_diagnose() -> None:
    assert plan_emotional_reply("你是笨蛋吗")

    local = LocalCommandBrain().handle_message(BrainMessage(text="你是笨蛋吗"))
    assert not local.tool_calls
    assert "接住" in local.text

    deepseek = DeepSeekBrain().handle_message(BrainMessage(text="你是笨蛋吗"))
    assert not deepseek.tool_calls
    assert "接住" in deepseek.text


def test_smalltalk_gets_human_reply_without_identity_disclaimer() -> None:
    samples = {
        "给点情绪价值": "加满一点",
        "给我把初音未来叫过来": "初音在这里",
        "教我魔法": "第一课",
        "太逊了，你快点学会了教我": "确实有点逊",
        "晚上好呀": "初音在",
        "嘿咻": "初音",
        "下雨天了怎么办我好想你": "不敢打给你",
        "给我唱歌": "初音在这里陪你唱",
        "陪我唱歌": "接住回声",
        "我要点歌": "初音在这里陪你唱",
        "可以帮我搜这首歌的歌词吗全部": "不能直接贴给你",
        "为什么失眠的声音 变的好熟悉": "接住",
        "今天任务好多我好崩溃": "最刺痛",
        "睡不着": "脑子会把一点点事放大",
        "就想一只蝴蝶飞过了废墟": "陶喆《蝴蝶》",
        "每次一想到你 心里好平静": "陶喆《蝴蝶》",
        "每次一见到你 心里好平静": "陶喆《蝴蝶》",
    }
    for message, expected in samples.items():
        response = LocalCommandBrain().handle_message(BrainMessage(text=message))
        assert not response.tool_calls
        assert expected in response.text
        assert "不是" not in response.text
        assert "低配" not in response.text
        assert "本尊" not in response.text
        assert "整段唱现成歌词" not in response.text


def test_emotional_reply_does_not_steal_explicit_production_status() -> None:
    response = LocalCommandBrain().handle_message(BrainMessage(text="查看 ComfyUI 任务进度，我有点焦虑"))
    assert response.tool_calls
    assert response.tool_calls[0].skill == "comfyui.run_status"


def test_capability_question_gets_expansive_agent_help() -> None:
    response = LocalCommandBrain().handle_message(BrainMessage(text="你可以干嘛呀"))
    assert response.tool_calls
    assert response.tool_calls[0].skill == "bot.help"
    assert "初音未来机器人" in response.text
    assert "初音能陪你" in response.text
    assert "生产助理" in response.text
    assert "文件和资料管家" in response.text
    assert "老三样" in response.text
    assert "收到，这个我也挺开心" not in response.text


def test_bare_ok_still_gets_warm_ack() -> None:
    response = LocalCommandBrain().handle_message(BrainMessage(text="可以"))
    assert not response.tool_calls
    assert "初音也开心" in response.text


def test_explicit_url_routes_to_web_fetch() -> None:
    assert get_skill_meta("web.fetch_url")
    assert get_skill_meta("web.search")
    assert get_skill_meta("web.research")
    brain = LocalCommandBrain()
    calls = brain._infer_tool_calls("读取这个网页 https://example.com/a?b=1")
    assert calls[0].skill == "web.fetch_url"
    assert calls[0].arguments["url"] == "https://example.com/a?b=1"
    search_calls = brain._infer_tool_calls("搜索陶喆 蝴蝶 歌词")
    assert search_calls[0].skill == "web.search"
    assert search_calls[0].arguments["query"] == "陶喆 蝴蝶 歌词"
    miku_calls = brain._infer_tool_calls("搜索一下初音未来")
    assert miku_calls[0].skill == "web.search"
    assert miku_calls[0].arguments["query"] == "初音未来"
    research_calls = brain._infer_tool_calls("搜索并整合 陶喆 蝴蝶 歌曲含义")
    assert research_calls[0].skill == "web.research"
    assert research_calls[0].arguments["query"] == "陶喆 蝴蝶 歌曲含义"


def test_web_fetch_formats_html(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200
        headers = {"Content-Type": "text/html; charset=utf-8"}
        text = "<html><head><title>Hello</title><style>x</style></head><body><h1>Hi</h1><p>World</p></body></html>"

        def raise_for_status(self) -> None:
            return None

    import assetclaw_matting.skills.web_skills as web_skills

    monkeypatch.setattr(web_skills.requests, "get", lambda *args, **kwargs: FakeResponse())
    payload = fetch_url("https://example.com")
    assert payload["title"] == "Hello"
    assert "World" in payload["text"]
    text = format_skill_results([{"ok": True, "skill": "web.fetch_url", "result": payload}])
    assert "标题：Hello" in text


def test_web_search_parses_duckduckgo_html(monkeypatch) -> None:
    class FakeResponse:
        text = """
        <html><body>
          <a class="result__a" href="/l/?uddg=https%3A%2F%2Fexample.com%2Fa">Result One</a>
          <a class="result__snippet">Useful snippet one.</a>
        </body></html>
        """

        def raise_for_status(self) -> None:
            return None

    import assetclaw_matting.skills.web_skills as web_skills

    monkeypatch.setattr(web_skills.requests, "get", lambda *args, **kwargs: FakeResponse())
    payload = search_web("test query")
    assert payload["items"][0]["title"] == "Result One"
    assert payload["items"][0]["url"] == "https://example.com/a"
    assert payload["items"][0]["domain"] == "example.com"
    text = format_skill_results([{"ok": True, "skill": "web.search", "result": payload}])
    assert "搜索：test query" in text
    assert "Result One" in text


def test_web_research_fetches_pages_and_formats_sources(monkeypatch) -> None:
    class SearchResponse:
        text = """
        <html><body>
          <a class="result__a" href="/l/?uddg=https%3A%2F%2Fexample.com%2Fbutterfly">Butterfly Meaning</a>
          <a class="result__snippet">A song about recovery.</a>
        </body></html>
        """

        def raise_for_status(self) -> None:
            return None

    class PageResponse:
        status_code = 200
        headers = {"Content-Type": "text/html; charset=utf-8"}
        text = "<html><head><title>Butterfly Meaning</title></head><body><p>陶喆 蝴蝶 歌曲含义常被理解为从低谷里重新恢复。</p></body></html>"

        def raise_for_status(self) -> None:
            return None

    import assetclaw_matting.skills.web_skills as web_skills

    def fake_get(url, *args, **kwargs):
        if "duckduckgo.com" in url:
            return SearchResponse()
        return PageResponse()

    monkeypatch.setattr(web_skills.requests, "get", fake_get)
    payload = research("陶喆 蝴蝶 歌曲含义")
    assert payload["source_count"] == 1
    assert "重新恢复" in payload["answer"]
    text = format_skill_results([{"ok": True, "skill": "web.research", "result": payload}])
    assert "联网整合：陶喆 蝴蝶 歌曲含义" in text
    assert "来源：" in text


def test_emotion_skill_registered() -> None:
    assert get_skill_meta("emotion.respond")
    result = call_skill("emotion.respond", {"text": "烦死了"}, requested_by="test")
    assert result["ok"] is True
    assert "磨人" in result["result"]["text"]
