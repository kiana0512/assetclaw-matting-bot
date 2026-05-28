"""Execute parsed Feishu text commands and return reply strings.

Extracted here to break circular imports between event_handler ↔ openclaw/bridge.
"""
from __future__ import annotations

from assetclaw_matting.feishu.command_parser import parse

_HELP_TEXT = (
    "AssetClaw Matting Bot 命令：\n"
    "  help                      查看帮助\n"
    "  queue                     查看当前队列状态\n"
    "  batch list                查看最近批次\n"
    "  batch status <batch_id>   查看批次详情\n"
    "  batch cancel <batch_id>   取消批次\n"
    "  task status <task_id>     查看任务详情\n"
    "\n"
    "批量抠图请通过 CLI 或 Admin API 创建批次。\n"
    "如需 AI 助手，请联系管理员启用 OpenClaw Agent。"
)


def execute_command(text: str, chat_id: str = "") -> str:
    """Parse and execute a command. Returns the reply string."""
    cmd = parse(text)

    if cmd.name == "help":
        return _HELP_TEXT

    if cmd.name == "queue":
        return _queue_status()

    if cmd.name == "batch_list":
        return _batch_list()

    if cmd.name == "batch_status":
        return _batch_status(cmd.args.get("batch_id", ""))

    if cmd.name == "batch_cancel":
        return _batch_cancel(cmd.args.get("batch_id", ""))

    if cmd.name in ("batch_pause", "batch_resume"):
        return "暂未实现，敬请期待。"

    if cmd.name == "task_status":
        return _task_status(cmd.args.get("task_id", ""))

    return ""  # unknown — caller decides what to do


def is_known_command(text: str) -> bool:
    return parse(text).name != "unknown"


# ── Command implementations ───────────────────────────────────────────────────

def _queue_status() -> str:
    from assetclaw_matting.db import batch_repo, task_repo
    stats = task_repo.queue_stats()
    running_batches = batch_repo.running_batch_count()
    return (
        f"当前队列状态：\n"
        f"  RUNNING 批次: {running_batches}\n"
        f"  QUEUED 任务:  {stats['QUEUED']}\n"
        f"  RUNNING 任务: {stats['RUNNING']}\n"
        f"  FAILED 任务:  {stats['FAILED']}"
    )


def _batch_list() -> str:
    from assetclaw_matting.db.batch_repo import list_batches
    batches = list_batches(limit=10)
    if not batches:
        return "暂无批次记录。"
    lines = ["最近 10 个批次："]
    for b in batches:
        done = b.succeeded_count + b.failed_count
        lines.append(
            f"  {b.id}  {b.status:<10}  {done}/{b.total_count}  {b.created_at[:10]}"
        )
    return "\n".join(lines)


def _batch_status(batch_id: str) -> str:
    if not batch_id:
        return "请提供 batch_id，例如：batch status BATCH_XXXX"
    from assetclaw_matting.db.batch_repo import get_batch
    b = get_batch(batch_id)
    if b is None:
        return f"找不到批次: {batch_id}"
    return (
        f"批次详情：\n"
        f"  ID:       {b.id}\n"
        f"  状态:     {b.status}\n"
        f"  工作流:   {b.workflow_type}\n"
        f"  总数:     {b.total_count}\n"
        f"  成功:     {b.succeeded_count}\n"
        f"  失败:     {b.failed_count}\n"
        f"  取消:     {b.canceled_count}\n"
        f"  运行中:   {b.running_count}\n"
        f"  排队:     {b.queued_count}\n"
        f"  输入目录: {b.input_dir}\n"
        f"  输出目录: {b.output_dir}"
    )


def _batch_cancel(batch_id: str) -> str:
    if not batch_id:
        return "请提供 batch_id"
    from assetclaw_matting.services.batch_service import cancel_batch
    try:
        cancel_batch(batch_id)
        return f"批次 {batch_id} 已取消。RUNNING 任务将完成当前图片后停止。"
    except ValueError as exc:
        return f"取消失败：{exc}"


def _task_status(task_id: str) -> str:
    if not task_id:
        return "请提供 task_id"
    from assetclaw_matting.db.task_repo import get_task
    t = get_task(task_id)
    if t is None:
        return f"找不到任务: {task_id}"
    return (
        f"任务详情：\n"
        f"  ID:     {t.id}\n"
        f"  批次:   {t.batch_id}\n"
        f"  状态:   {t.status}\n"
        f"  工作流: {t.workflow_type}\n"
        f"  文件名: {t.original_filename}\n"
        f"  输出:   {t.output_path}\n"
        f"  错误:   {t.error or '无'}"
    )
