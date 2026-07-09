from __future__ import annotations


def answer_recent_question(text: str, conversation_id: str) -> str | None:
    from assetclaw_matting.db.repos import get_recent_brain_messages

    recent = get_recent_brain_messages(conversation_id, limit=5)
    if _asks_intent_question(text):
        intent = _last_actionable_user_message(recent)
        if intent:
            return f"你要我做的是：{intent}"
        return "我这里没找到明确的上一条任务。"

    if not _asks_previous_question(text):
        return None
    if not recent:
        return "我这里还没有上一条对话记录。"
    previous = recent[-1].get("message_text") or ""
    if not previous:
        return "我这里还没有上一条对话记录。"
    return f"你上一个问题是：{previous}"


def _asks_previous_question(text: str) -> bool:
    normalized = "".join(text.strip().split()).lower()
    patterns = (
        "上个问题",
        "上一个问题",
        "刚才问",
        "刚刚问",
        "上一句",
        "上一条",
        "lastquestion",
        "previousquestion",
    )
    return any(pattern in normalized for pattern in patterns)


def _asks_intent_question(text: str) -> bool:
    normalized = "".join(text.strip().split()).lower()
    patterns = (
        "你知道我要你干嘛吗",
        "我要你干嘛",
        "你理解了啥",
        "理解了啥",
        "不是我理解了",
        "你知道我要你做什么吗",
    )
    return any(pattern in normalized for pattern in patterns)


def _last_actionable_user_message(recent: list[dict]) -> str:
    skip_parts = ("我理解了", "理解了啥", "你知道我要你干嘛", "不是我理解了", "你好")
    for item in reversed(recent):
        text = (item.get("message_text") or "").strip()
        if not text:
            continue
        if any(part in text for part in skip_parts):
            continue
        if text in {"好的我知道了", "好", "嗯", "收到"}:
            continue
        return text
    return ""
