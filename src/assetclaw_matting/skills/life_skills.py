from __future__ import annotations

from datetime import datetime
import random
import re
from typing import Any
from urllib.parse import quote

import requests

from assetclaw_matting.config import settings
from assetclaw_matting.runtime_context import get_runtime_context

LOCATION_KEY = "user_default_location"


def set_location(location: str) -> dict[str, Any]:
    cleaned = _clean_location(location)
    if not cleaned:
        raise ValueError("location is required")
    from assetclaw_matting.db.repos import upsert_memory_note

    scope = _memory_scope()
    upsert_memory_note(scope, LOCATION_KEY, cleaned, source="life.set_location")
    return {"ok": True, "location": cleaned, "scope": scope, "source": "memory"}


def location() -> dict[str, Any]:
    remembered = _remembered_location()
    if remembered:
        return {"ok": True, "location": remembered, "source": "memory"}
    default_location = (settings.user_default_location or "").strip()
    if default_location:
        return {"ok": True, "location": default_location, "source": "config"}
    return {"ok": False, "error": "还不知道你的位置。你可以说：我在上海。"}


def weather(location: str = "", timeout_seconds: int = 8) -> dict[str, Any]:
    target = _clean_location(location) or _location_value()
    if not target:
        return {"ok": True, "available": False, "error": "还不知道城市。你可以说：我在上海，或者问：上海天气怎么样。"}
    url = f"https://wttr.in/{quote(target)}?format=j1&lang=zh"
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "assetclaw-matting-bot/1.0"},
            timeout=max(2, min(int(timeout_seconds), 20)),
        )
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        return {
            "ok": True,
            "available": False,
            "location": target,
            "error": f"天气查询失败：{exc}",
            "fallback": _weather_fallback(target),
        }

    current = (data.get("current_condition") or [{}])[0]
    forecast = (data.get("weather") or [{}])[0]
    condition = _weather_desc(current)
    temp = _pick(current, "temp_C")
    feels = _pick(current, "FeelsLikeC")
    humidity = _pick(current, "humidity")
    wind = _pick(current, "windspeedKmph")
    rain_chance = ""
    hourly = forecast.get("hourly") or []
    if hourly:
        rain_chance = str(max(int(item.get("chanceofrain") or 0) for item in hourly if isinstance(item, dict)))
    advice = _weather_advice(condition, rain_chance, temp)
    return {
        "ok": True,
        "available": True,
        "location": target,
        "condition": condition,
        "temperature_c": temp,
        "feels_like_c": feels,
        "humidity": humidity,
        "wind_kmph": wind,
        "rain_chance": rain_chance,
        "advice": advice,
        "source": "wttr.in",
    }


def food_suggest(meal: str = "", location: str = "", mood: str = "", weather_text: str = "") -> dict[str, Any]:
    target = _clean_location(location) or _location_value()
    meal_name = _meal_name(meal)
    context = " ".join(part for part in (meal, mood, weather_text) if part)
    options = _food_options(meal_name, context)
    opener = _food_opener(meal_name, mood)
    return {
        "ok": True,
        "meal": meal_name,
        "location": target,
        "options": options,
        "opener": opener,
        "note": "按外卖可执行性优先，先给你低决策成本版本。",
    }


def _memory_scope() -> str:
    ctx = get_runtime_context()
    return str(ctx.get("conversation_id") or ctx.get("chat_id") or "global")


def _remembered_location() -> str:
    from assetclaw_matting.db.repos import list_memory_notes

    for scope in (_memory_scope(), "global"):
        notes = list_memory_notes(scope, limit=50)
        for item in notes:
            if item.get("key") == LOCATION_KEY and item.get("value"):
                return str(item["value"])
    return ""


def _location_value() -> str:
    return _remembered_location() or (settings.user_default_location or "").strip()


def _clean_location(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^(我现在|我目前|我|人在|当前位置|位置)\s*(在|是)?", "", text)
    text = re.sub(r"(天气|怎么样|如何|外卖|午饭|晚饭|中午|晚上|今天|现在)", "", text)
    return text.strip(" ，。,.!！?？")


def _pick(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    return "" if value is None else str(value)


def _weather_desc(current: dict[str, Any]) -> str:
    raw = current.get("lang_zh") or current.get("weatherDesc") or []
    if raw and isinstance(raw, list):
        value = raw[0].get("value")
        if value:
            return str(value)
    return str(current.get("weatherDesc", [{}])[0].get("value") or "天气未知")


def _weather_advice(condition: str, rain_chance: str, temp: str) -> str:
    rain = int(rain_chance or 0)
    temp_int = int(float(temp)) if str(temp).replace(".", "", 1).lstrip("-").isdigit() else None
    if "雨" in condition or rain >= 50:
        return "出门带伞，鞋子也别选太娇气的。"
    if temp_int is not None and temp_int >= 30:
        return "偏热，点喝的别太甜，下午会舒服一点。"
    if temp_int is not None and temp_int <= 10:
        return "偏冷，热饮和外套都值得安排。"
    return "整体还行，按正常通勤准备就好。"


def _weather_fallback(target: str) -> str:
    return f"我知道你想查 {target}，但现在天气接口没通。可以稍后再问我一次。"


def _meal_name(text: str) -> str:
    raw = str(text or "")
    now_hour = datetime.now().hour
    if any(word in raw for word in ("早饭", "早餐", "早上")):
        return "早餐"
    if any(word in raw for word in ("午饭", "午餐", "中午")):
        return "午饭"
    if any(word in raw for word in ("晚饭", "晚餐", "晚上")):
        return "晚饭"
    if any(word in raw for word in ("夜宵", "宵夜")):
        return "夜宵"
    if 5 <= now_hour < 10:
        return "早餐"
    if 10 <= now_hour < 15:
        return "午饭"
    if 15 <= now_hour < 21:
        return "晚饭"
    return "夜宵"


def _food_options(meal: str, context: str) -> list[dict[str, str]]:
    tired = any(word in context for word in ("累", "烦", "忙", "赶", "没脑子", "低决策"))
    hot = any(word in context for word in ("热", "闷", "夏", "上火"))
    rainy = any(word in context for word in ("雨", "冷", "湿"))
    spicy = any(word in context for word in ("辣", "重口", "爽"))
    pool = {
        "早餐": [
            ("豆浆饭团/三明治", "快，不占脑子，适合边开工边吃。"),
            ("小馄饨或热粥", "胃会比较舒服。"),
            ("咖啡加鸡蛋卷", "适合需要立刻进入工作状态。"),
        ],
        "午饭": [
            ("黄焖鸡/猪脚饭", "稳定有饱腹感，下午抗到比较久。"),
            ("牛肉面/番茄肥牛面", "热乎，适合工作日中午快速回血。"),
            ("轻食饭/鸡胸肉饭", "不困，下午还有活的时候比较友好。"),
            ("麻辣烫少辣版", "想吃点有味道但又不想太撑。"),
        ],
        "晚饭": [
            ("热汤面或小馄饨", "今天如果累了，这个最省心。"),
            ("盖浇饭加一份青菜", "稳，能吃饱也不会太折腾。"),
            ("寿司/饭团拼盘", "不想洗碗、不想太油的时候很合适。"),
            ("麻辣香锅微辣", "想奖励自己可以选这个，但别太晚吃太重。"),
        ],
        "夜宵": [
            ("粥/小馄饨", "不太压胃，明天不容易后悔。"),
            ("关东煮", "有热气，分量也好控制。"),
            ("酸奶水果加小面包", "想轻一点就这么走。"),
        ],
    }
    items = pool.get(meal, pool["午饭"])
    if rainy:
        items = [("热汤面/砂锅粉", "下雨天吃热的，心情会被捞回来一点。")] + items
    if hot:
        items = [("凉皮/冷面加蛋", "热的时候别硬刚，清爽一点。")] + items
    if spicy:
        items = [("麻辣烫或冒菜", "想吃爽的就选它，辣度别拉满。")] + items
    if tired:
        items = [("最近那家你吃过不踩雷的盖饭", "今天不适合冒险，省决策才是王道。")] + items
    seen: set[str] = set()
    result = []
    for name, reason in items:
        if name in seen:
            continue
        seen.add(name)
        result.append({"name": name, "reason": reason})
        if len(result) >= 4:
            break
    return result


def _food_opener(meal: str, mood: str) -> str:
    if any(word in str(mood) for word in ("累", "烦", "崩", "焦虑")):
        return f"先别为{meal}再消耗脑力了，我给你低决策版本。"
    openers = [
        f"{meal}我建议走稳一点，别让吃饭也变成项目管理。",
        f"可以，{meal}给你几个不容易踩雷的选择。",
        f"我会按“好点、快到、吃完还能干活”来排。",
    ]
    return random.choice(openers)
