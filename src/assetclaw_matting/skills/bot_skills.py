from __future__ import annotations

from typing import Any


_HELP_TEXT = """\
我是你的初音未来机器人：能陪你聊，也能把 Win3090 上的生产任务往前推。你问“你能干嘛”的时候，我不该只报老三样；更好的答案应该按场景展开。

初音能陪你：
- 接住吐槽、焦虑、崩溃、失眠、想要情绪价值这类碎片化输入
- 记住你说过的重要偏好、位置、项目上下文，后面少让你重复
- 把混乱想法整理成下一步：先做什么、卡在哪里、要不要暂停、要不要重跑
- 陪你做低决策生活小事：天气、带不带伞、午饭晚饭外卖、今天先别把自己耗干

初音也能当生产助理：
- 看当前现场：ComfyUI、Cherry、抽帧、完整动画流程、GPU、待确认、最近错误
- 帮你判断任务为什么没动、卡在哪里、下一步该恢复/暂停/重跑/同步
- 管理 ComfyUI 工作流选择、批量抠图、队列、进度、ETA、输出同步
- 跑共享盘抠图：先拉到本地跑，再把结果同步回共享盘
- 做动画链路：飞书表格视频下载、抽帧、抠图、Cherry 平滑、缺帧修复和全量重跑

初音还能当文件和资料管家：
- 在允许的工作盘和共享盘里找文件、列目录、数图片/视频/表格/压缩包
- 复制、打包、改名、批量处理、发回飞书；图片可以直接预览发回
- 接收飞书图片/视频/文件，保存后继续 OCR、翻译、转格式、缩放或整理
- 联网搜索和整合：搜候选结果、读取明确 URL、抓取前几页并整理要点和来源

你可以这样问我：
你看看现在什么情况
这个任务为什么没开始，帮我判断
我现在很烦，你先帮我拆一下
网上搜一下这个问题，整理来源和结论
这批图从哪里到哪里，跑完发我
把刚刚那张图里的字翻译成中文
看看共享盘抠图目录，帮我整理输入输出
今天我不想做决定，午饭给我三个选项
把这个流程写成我能直接发给同事的说明

高风险动作比如删除、清空、移动、终止任务会先二次确认。"""


def bot_help(**_: Any) -> dict[str, Any]:
    return {"ok": True, "text": _HELP_TEXT}


def bot_skills(**_: Any) -> dict[str, Any]:
    from assetclaw_matting.skills.registry import SKILLS

    lines: list[str] = ["当前技能列表："]
    domains: dict[str, list[dict]] = {}
    for skill in SKILLS:
        d = skill.get("domain", "other")
        domains.setdefault(d, []).append(skill)

    domain_labels = {
        "agent": "智能体调度 / 诊断",
        "bot": "系统 / 帮助",
        "sticker": "情绪表情",
        "emotion": "情绪理解",
        "life": "生活陪聊",
        "web": "网页读取",
        "file": "文件系统",
        "memory": "记忆",
        "matting": "抠图批次",
        "queue": "队列",
        "comfyui": "ComfyUI",
        "logs": "日志",
        "system": "系统状态",
        "translate": "翻译",
        "other": "其他",
    }

    for domain, skills in domains.items():
        lines.append(f"{domain_labels.get(domain, domain)}：")
        for s in skills:
            status_parts = []
            if not s.get("implemented"):
                status_parts.append("未实现")
            elif s.get("partial"):
                status_parts.append("fake mode")
            risk = s.get("risk_level", "")
            if risk:
                status_parts.append(risk)
            status = "，".join(status_parts) if status_parts else "ready"
            confirm = "，需确认" if s.get("requires_confirmation") else ""
            lines.append(f"- {s['name']} [{status}{confirm}]")
        lines.append("")

    return {"ok": True, "text": "\n".join(lines).strip()}


def bot_permissions(**_: Any) -> dict[str, Any]:
    from assetclaw_matting.config import settings

    allowed = settings.allowed_roots_list
    deny_patterns = settings.deny_path_patterns_list
    allowed_str = "、".join(allowed) if allowed else "（未配置）"
    deny_str = "、".join(deny_patterns) if deny_patterns else "（无）"

    text = rf"""\
权限说明 & 安全边界

允许访问路径：
  {allowed_str}

禁止访问（路径包含以下关键字时拒绝）：
  {deny_str}

禁止的操作：
  - 任意 shell 执行
  - 格式化、分区、操作系统目录破坏
  - 读取文件内容（内容读取默认关闭）
  - 越权访问 Windows 系统目录、AppData、ProgramData 等
  - 访问 .env、.ssh 等配置密钥文件

写操作说明：
  - file.copy、file.mkdir、memory.remember、matting.batch_create 属于写操作，会写入审计日志
  - file.move、file.delete、file.empty_dir、批量重命名等高风险动作需要二次确认
  - 如配置了 FEISHU_ALLOWED_OPEN_IDS，只有名单内用户可执行写操作
  - 多用户隔离：飞书上下文按 chat_id + open_id 存储，同一群内不同用户不会混用记忆
  - 共享盘可以访问和复制；抠图任务不会直接在共享盘上计算，会先 staging 到本地再同步结果回去
  - Z:\ 是共享盘映射；等价 UNC 路径是 \\audioshare.lilith.com\AIart

高风险操作：
  - 删除、移动、清空目录、批量重命名默认必须走二次确认
  - shell、格式化、分区、C 盘系统目录操作永久禁止

二次确认格式：
  机器人会返回确认码，例如 abc123def0
  继续执行：确认执行 abc123def0
  放弃执行：取消 abc123def0"""

    return {"ok": True, "text": text}


def bot_status(**_: Any) -> dict[str, Any]:
    from assetclaw_matting.config import settings
    from assetclaw_matting.db.repos import get_last_error_summary
    from assetclaw_matting.skills.registry import SKILLS

    deepseek_ok = bool(settings.deepseek_api_key and settings.deepseek_base_url)
    llm_key_ok = bool(settings.llm_proxy_enabled and settings.llm_proxy_api_key)
    vision_ok = bool(settings.llm_proxy_enabled and settings.llm_proxy_base_url and settings.llm_proxy_api_key and (settings.llm_proxy_complex_model or settings.llm_proxy_model))
    db_exists = settings.data_db_path.exists()
    skill_count = len(SKILLS)
    enabled_count = sum(1 for s in SKILLS if s.get("implemented"))
    last_err = get_last_error_summary()
    event_mode = settings.feishu_event_mode
    is_ws = event_mode == "ws"

    lines = [
        "当前系统状态",
        "",
        f"Gateway：本地调试 ({settings.gateway_host}:{settings.gateway_port})",
        f"Feishu 事件模式：{event_mode} ({'长连接 WebSocket' if is_ws else '旧 Webhook 模式'})",
        f"Cloudflare：{'已禁用' if is_ws else '可能启用（切换到 ws 模式）'}",
        f"公网暴露：{'无' if is_ws else '需要公网 URL（legacy）'}",
        f"Brain provider：{settings.brain_provider}",
        f"DeepSeek 配置：{'已配置' if deepseek_ok else '未配置或 key 为空'}",
        f"DeepSeek URL：{settings.deepseek_base_url}",
        f"DeepSeek router/summary：{settings.deepseek_router_model} / {settings.deepseek_summary_model}",
        f"Legacy LLM Proxy 配置：{'已配置' if llm_key_ok else '未配置或 key 为空'}",
        f"图片分析/OCR：{'可尝试' if vision_ok else '不可用（需要 LLM_PROXY_ENABLED=true、LLM_PROXY_API_KEY 和视觉模型）'}",
        f"ComfyUI fake mode：{'是' if settings.comfyui_fake_mode else '否（真实模式）'}",
        f"数据库：{'存在' if db_exists else '不存在'} ({settings.data_db_path})",
        f"允许路径：{settings.allowed_roots}",
        f"技能总数：{skill_count}，已实现：{enabled_count}",
        f"最近错误：{last_err if last_err else '无'}",
    ]
    return {"ok": True, "text": "\n".join(lines)}


def bot_errors(**_: Any) -> dict[str, Any]:
    from assetclaw_matting.db.sqlite import get_connection

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT skill, error, created_at FROM skill_calls
            WHERE ok = 0 AND error != ''
            ORDER BY id DESC LIMIT 10
            """
        ).fetchall()

    if not rows:
        return {"ok": True, "text": "最近没有错误记录。"}

    lines = ["最近 10 条错误", ""]
    for row in rows:
        lines.append(f"  [{row['created_at'][:19]}] {row['skill']} — {row['error'][:80]}")
    return {"ok": True, "text": "\n".join(lines)}
