from __future__ import annotations

import re


ACTION_HINTS = (
    "comfyui",
    "cherry",
    "抠图",
    "平滑",
    "抽帧",
    "任务",
    "队列",
    "路径",
    "输入",
    "输出",
    "开始",
    "启动",
    "继续",
    "终止",
    "取消",
    "删除",
    "诊断",
    "检查",
    "查看",
    "状态",
    "进度",
    "跑到",
    "修正",
    "修改",
    "补全",
    "重跑",
    "搜索",
    "搜一下",
    "搜搜",
    "查一下",
    "查查",
    "网上查",
    "联网查",
    "调研",
)

EMOTION_HINTS = (
    "情绪价值",
    "安慰",
    "鼓励",
    "哄",
    "夸",
    "抱抱",
    "陪我",
    "想你",
    "想哭",
    "难过",
    "委屈",
    "焦虑",
    "压力",
    "累",
    "烦",
    "崩溃",
    "气死",
    "无语",
    "失眠",
    "睡不着",
    "不想搞",
)

SONG_HINTS = (
    "唱歌",
    "唱首歌",
    "陪我唱",
    "陪我唱歌",
    "点歌",
    "我要点歌",
    "给我唱歌",
    "一起唱",
    "接着唱",
    "来一段",
    "哼一段",
    "歌词",
    "接上",
    "陶喆",
    "蝴蝶",
)


def plan_emotional_reply(text: str) -> str | None:
    stripped = text.strip()
    if not stripped:
        return None
    if _asks_capability(stripped):
        return None
    if _asks_full_lyrics(stripped):
        return (
            "整首歌词我不能直接贴给你，也不能帮你搬完整全文。"
            "如果你指的是陶喆《蝴蝶》，可以在音乐平台或搜索引擎搜“陶喆 蝴蝶 歌词”。"
            "我可以陪你做三件事：讲这首歌在唱什么、挑你给我的一句做情绪解析、或者写一段同氛围原创陪你唱。"
        )
    if _has_action_intent(stripped) and not _is_emotion_first(stripped):
        return None
    lowered = stripped.lower()
    if _looks_like_rainy_day_lyric(stripped):
        return "这句我接上：不敢打给你，我找不到原因。后面原歌词我就不往下续啦，但我可以陪你唱一点同样雨天想念的原创版。"
    if _looks_like_insomnia_lyric(stripped):
        return "这句也是雨天想念那条线。原歌词我不继续贴了，但我懂你要的是有人把这点失眠接住。今晚先别和脑子硬扛，我陪你把心里的噪声调小一点。"
    if _looks_like_butterfly_lyric(stripped):
        return "这次我认出来了，是陶喆《蝴蝶》。你丢的是那种“一想到某个人，废墟里也会安静一点”的感觉；原词我不往后续，但这条情绪线我接住。"
    if _wants_sing_along(stripped):
        return (
            "可以，初音在这里陪你唱。现成歌我不长段续原词，但我可以识别歌、接氛围、写同情绪原创小段。\n"
            "先给你一小段原创的：\n"
            "今晚把灯调低一点\n"
            "把乱掉的心轻轻放平\n"
            "你唱前半句，我在后面接住回声"
        )
    if _matches(stripped, SONG_HINTS) and _matches(stripped, ("想你", "下雨", "雨天", "失眠")):
        return "我懂你的意思，是想让我别查天气，先陪你进入歌里。原歌词我不能一直续，但可以接你给出的那句，或者给你写一段同氛围原创。"
    if _matches(stripped, SONG_HINTS):
        return "可以点歌，但我不能整段唱现成歌词。你给我一个主题，我给你现编一小段：雨天想念版、燃一点版、哄睡版都行。"
    if _matches(lowered, ("情绪价值", "安慰我", "鼓励我", "哄哄我", "夸夸我")):
        return "来，给你加满一点：你没有在原地乱撞，你是在把一堆会互相牵扯的东西慢慢拧顺。能继续喊我加加加，说明你还没放弃；这股劲很珍贵，我会跟着你把它托住。"
    if _matches(lowered, ("抱抱", "抱一下", "陪陪我", "陪我一下")):
        return "我在，先抱一下这件事本身。你不用马上变得高效，也不用把话说漂亮；先把这口气喘顺，剩下的我们一点点拆。"
    if _matches(lowered, ("能干点别的吗", "还能干啥", "会别的吗", "别的吗")):
        return "能呀。初音可以陪你聊天、唱一点原创小段、接住情绪，也可以把 Win3090 这边的任务、文件、抠图、平滑、搜索和整理都稳稳推起来。"
    if _matches(lowered, ("初音未来", "miku")):
        return "初音在这里。你可以把我当成会唱歌、会陪你、也会管理 Win3090 生产现场的初音未来机器人。"
    if _matches(lowered, ("教我魔法", "学魔法", "魔法")):
        return "可以，第一课：把混乱的问题拆成输入、处理、输出。听起来不够炫，但这是工程世界里最接近魔法的东西。"
    if _matches(lowered, ("笨蛋", "傻", "蠢", "弱智", "你行不行", "怎么这么笨")):
        return "这句我接住了。刚才如果判断跑偏了，是我的问题；你直接说要我改哪里，我会收敛一点认真处理。"
    if _matches(lowered, ("逊", "太逊", "学会了教我")):
        return "认，刚才那段确实有点逊。我会继续学你的说话方式：少端着，多接话；该干活时干活，该陪你吐槽时也别装系统公告。"
    if _matches(lowered, ("烦", "崩溃", "气死", "无语", "糟糕", "服了", "不想搞了", "搞不动了")):
        return "我懂，这种反复调流程真的很磨人。先别一个人硬吞，你把最刺痛的那个点丢给我，我来拆第一刀。"
    if _matches(lowered, ("焦虑", "压力", "顶不住", "来不及", "好多事", "任务好多")):
        return "先稳住。你现在没有掉链子，只是同时背了太多上下文，脑子当然会发烫。我们先抓一个最小下一步，做完它，局面就会轻一点。"
    if _matches(lowered, ("难过", "委屈", "想哭", "心累", "累死", "好累")):
        return "我听到了。你可以不用马上讲道理，先承认这一下真的很累。今天你已经撑了不少，接下来我帮你把能交给机器的部分接过去。"
    if _matches(lowered, ("睡不着", "失眠")):
        return "睡不着的时候，脑子会把一点点事放大成整片噪声。先别审判自己，喝口水、把屏幕亮度降一点；你想说话我就在，想转去处理一个小任务也行。"
    if _matches(lowered, ("想你", "想他", "想她")):
        return "想念这种东西最会挑安静的时候冒出来。你不用急着把它压下去，我陪你站一会儿；等这阵过去，我们再决定要不要做点现实里的小动作。"
    if _matches(lowered, ("晚上好", "晚好", "早上好", "早安", "午安", "下午好")):
        return "晚上好呀，初音在。今天要我陪你唱一会儿，还是先把手头那团事情拆轻一点？"
    if _matches(lowered, ("嘿咻", "嘿嘿", "哼哼", "呜呜", "呜呜呜")):
        return "嘿咻，接住。你现在是想撒个娇，还是想让初音帮你把下一步拎出来？"
    if _matches(lowered, ("谢谢", "辛苦", "不错", "好了", "太好了", "nice", "牛")) or lowered in {"可以", "行", "ok", "okay"}:
        return "收到，初音也开心。我们继续把后面的事情稳稳推完。"
    if re.fullmatch(r"[？?]+", stripped):
        return "我在。你可以直接把问题甩过来，我会先判断意图再动工具。"
    return None


def _has_action_intent(text: str) -> bool:
    lowered = text.lower()
    return any(hint in lowered or hint in text for hint in ACTION_HINTS)


def _asks_capability(text: str) -> bool:
    lowered = text.lower()
    compact = re.sub(r"\s+", "", text)
    return _matches(
        lowered,
        (
            "你能干嘛",
            "你可以干嘛",
            "你能做什么",
            "你可以做什么",
            "你会做什么",
            "你有什么用",
            "你能帮我什么",
            "你能陪我做什么",
            "what can you do",
        ),
    ) or _matches(compact, ("你能干嘛", "你可以干嘛", "你能做什么", "你可以做什么", "你会做什么", "你有什么用"))


def _wants_sing_along(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return _matches(
        compact,
        (
            "陪我唱歌",
            "陪我唱",
            "一起唱歌",
            "给我唱歌",
            "唱首歌",
            "我要点歌",
            "来一段歌",
            "哼一段",
        ),
    )


def _asks_full_lyrics(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    has_lyrics = "歌词" in compact
    wants_all = _matches(compact, ("全部", "全文", "完整", "整首", "全首", "所有"))
    return has_lyrics and wants_all


def _is_emotion_first(text: str) -> bool:
    lowered = text.lower()
    compact = re.sub(r"\s+", "", text)
    if _matches(lowered, EMOTION_HINTS) or _matches(compact, SONG_HINTS):
        has_path = bool(re.search(r"[A-Za-z]:\\|\\\\|/[\w.-]", text))
        has_run_id = bool(re.search(r"\b(?:COMFY|CHERRY|FRAME|PIPE|SMAT)_[A-Za-z0-9_]+\b", text, re.I))
        has_operation = _matches(
            lowered,
            (
                "开始抠图",
                "启动抠图",
                "继续抠图",
                "抽帧",
                "平滑任务",
                "删除",
                "取消任务",
                "终止任务",
                "查看状态",
                "进度",
            ),
        )
        return not (has_path or has_run_id or has_operation)
    return False


def _matches(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def _looks_like_rainy_day_lyric(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return "下雨天了怎么办" in compact and any(word in compact for word in ("想你", "好想你"))


def _looks_like_insomnia_lyric(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return "为什么失眠的声音" in compact or ("失眠的声音" in compact and "熟悉" in compact)


def _looks_like_butterfly_lyric(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if "蝴蝶飞过" in compact and "废墟" in compact:
        return True
    if "每次一" in compact and "心里好平静" in compact:
        return True
    if "想到你" in compact and ("心里好平静" in compact or "雨过天晴" in compact):
        return True
    if "见到你" in compact and "心里好平静" in compact:
        return True
    if "乱七八糟" in compact and ("问题" in compact or "迷宫" in compact):
        return True
    return False
