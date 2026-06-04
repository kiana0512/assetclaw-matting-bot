from __future__ import annotations

import re

from assetclaw_matting.brain.schemas import BrainMessage, ToolCall


def plan_life_task(message: BrainMessage) -> tuple[list[ToolCall], str] | None:
    text = (message.text or "").strip()
    if not text:
        return None
    if _looks_like_lyric_or_longing(text):
        return None
    location = _location_from_set_text(text)
    if location:
        return [ToolCall(skill="life.set_location", arguments={"location": location})], "记住用户当前位置。"
    explicit_weather_location = _location_from_weather_text(text)
    if _asks_weather(text):
        return [ToolCall(skill="life.weather", arguments={"location": explicit_weather_location or ""})], "查询天气。"
    if _asks_location(text):
        return [ToolCall(skill="life.location", arguments={})], "读取已知位置。"
    if _asks_food(text):
        return [
            ToolCall(
                skill="life.food_suggest",
                arguments={
                    "meal": text,
                    "mood": text,
                    "weather_text": "雨" if "下雨" in text else "",
                },
            )
        ], "推荐吃什么。"
    return None


def _asks_weather(text: str) -> bool:
    return any(word in text for word in ("天气", "下雨", "带伞", "冷不冷", "热不热", "温度", "降温", "会冷", "会热"))


def _asks_location(text: str) -> bool:
    return any(word in text for word in ("我在哪里", "我在哪", "你知道我在哪", "当前位置", "我的位置"))


def _asks_food(text: str) -> bool:
    return any(
        word in text
        for word in (
            "吃什么",
            "吃啥",
            "点什么外卖",
            "点啥外卖",
            "外卖点什么",
            "午饭",
            "午餐",
            "中午吃",
            "晚饭",
            "晚餐",
            "夜宵",
            "宵夜",
        )
    )


def _looks_like_lyric_or_longing(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if "歌词" in compact or "一起唱" in compact or "接上" in compact:
        return True
    if "下雨天了怎么办" in compact and any(word in compact for word in ("想你", "好想你")):
        return True
    if "不敢打给你" in compact or "找不到原因" in compact:
        return True
    return False


def _location_from_set_text(text: str) -> str:
    if _asks_weather(text) or _asks_food(text):
        return ""
    match = re.search(r"(?:我(?:现在|目前)?|人在|当前位置(?:是)?|记住我(?:现在)?(?:在)?)\s*在\s*([\w\u4e00-\u9fff·\-]{2,20})", text)
    if match:
        return match.group(1).strip(" ，。,.!！?？")
    match = re.search(r"我(?:现在|目前)?\s*是\s*([\w\u4e00-\u9fff·\-]{2,20})", text)
    if match:
        return match.group(1).strip(" ，。,.!！?？")
    return ""


def _location_from_weather_text(text: str) -> str:
    match = re.search(r"([\w\u4e00-\u9fff·\-]{2,20})(?:天气|会下雨|冷不冷|热不热|温度)", text)
    if match:
        candidate = match.group(1).strip(" ，。,.!！?？今天现在")
        if candidate and candidate not in {"今天", "现在", "这个", "那里", "这里"}:
            return candidate
    return ""
