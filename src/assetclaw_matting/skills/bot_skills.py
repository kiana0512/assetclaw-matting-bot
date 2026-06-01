from __future__ import annotations

from typing import Any


_HELP_TEXT = """\
AssetClaw Win3090 自动化执行节点

我现在能做这些：
- 在 D/E/F 工作盘列目录、查文件、找图片/视频/表格/压缩包
- 跨盘复制文件/目录、复制并改名、创建目录
- 批量复制/移动/重命名/建目录，按顺序改名
- 读取/写入安全文本文件、计算 hash、查看磁盘空间
- 图片批量查尺寸、转格式、缩放
- 删除/清空/移动这类高风险动作会先二次确认
- 把本地文件通过飞书发回当前会话
- 管理抠图批次（当前 fake mode，不跑 GPU）
- 保存 / 查询本地记忆

可以直接说：
看看 E 盘有哪些图片
把 E:\\a.png 复制一份并改名为 a_bak.png
把 E:\\assetclaw-matting-bot\\README.md 通过飞书发给我
查看技能列表"""


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
        "bot": "系统 / 帮助",
        "file": "文件系统",
        "memory": "记忆",
        "matting": "抠图批次",
        "queue": "队列",
        "comfyui": "ComfyUI",
        "logs": "日志",
        "system": "系统状态",
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

    text = f"""\
权限说明 & 安全边界

允许访问路径：
  {allowed_str}

禁止访问（路径包含以下关键字时拒绝）：
  {deny_str}

禁止的操作：
  - 任意 shell 执行
  - 删除文件 / 目录
  - 读取文件内容（内容读取默认关闭）
  - 越权访问 Windows 系统目录、AppData、ProgramData 等
  - 访问 .env、.ssh 等配置密钥文件

写操作说明：
  - file.copy、file.mkdir、memory.remember、matting.batch_create 属于写操作，会写入审计日志
  - file.move 属于中风险写操作，需要二次确认
  - 如配置了 FEISHU_ALLOWED_OPEN_IDS，只有名单内用户可执行写操作
  - 多用户隔离：飞书上下文按 chat_id + open_id 存储，同一群内不同用户不会混用记忆

高风险操作：
  - 目前删除类、shell 执行类操作不提供，永久禁止
  - 后续如果新增删除、覆盖批量文件、真实 GPU 批处理等高风险动作，默认必须走二次确认

二次确认格式：
  机器人会返回确认码，例如 abc123def0
  继续执行：确认执行 abc123def0
  放弃执行：取消 abc123def0"""

    return {"ok": True, "text": text}


def bot_status(**_: Any) -> dict[str, Any]:
    from assetclaw_matting.config import settings
    from assetclaw_matting.db.repos import get_last_error_summary
    from assetclaw_matting.skills.registry import SKILLS

    llm_key_ok = bool(settings.llm_proxy_enabled and settings.llm_proxy_api_key)
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
        f"LLM Proxy 配置：{'已配置' if llm_key_ok else '未配置或 key 为空'}",
        f"LLM Proxy URL：{settings.llm_proxy_base_url}",
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
