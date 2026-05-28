from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from assetclaw_matting.models.batch_models import Batch

log = logging.getLogger(__name__)


def _send(chat_id: str, text: str) -> None:
    if not chat_id:
        return
    try:
        from assetclaw_matting.feishu.client import feishu_client
        feishu_client.send_text_to_chat(chat_id, text)
    except Exception:
        log.exception("Failed to send Feishu notification to %s", chat_id)


def notify_batch_created(batch: "Batch") -> None:
    chat_id = batch.notify_chat_id or ""
    if not chat_id:
        return
    _send(
        chat_id,
        f"批次已创建 ✓\n"
        f"ID: {batch.id}\n"
        f"工作流: {batch.workflow_type}\n"
        f"图片数: {batch.total_count}\n"
        f"输入目录: {batch.input_dir}\n"
        f"输出目录: {batch.output_dir}\n"
        f"使用命令 batch-start --batch-id {batch.id} 开始处理",
    )


def notify_batch_started(batch: "Batch") -> None:
    chat_id = batch.notify_chat_id or ""
    if not chat_id:
        return
    _send(
        chat_id,
        f"批次开始处理 ▶\n"
        f"ID: {batch.id}\n"
        f"工作流: {batch.workflow_type}\n"
        f"共 {batch.total_count} 张图片",
    )


def notify_batch_progress(batch: "Batch") -> None:
    chat_id = batch.notify_chat_id or ""
    if not chat_id:
        return
    completed = batch.succeeded_count + batch.failed_count
    pct = int(completed / batch.total_count * 100) if batch.total_count else 0
    _send(
        chat_id,
        f"批次进度 {pct}%\n"
        f"ID: {batch.id}\n"
        f"完成: {completed}/{batch.total_count}\n"
        f"成功: {batch.succeeded_count}  失败: {batch.failed_count}  排队: {batch.queued_count}",
    )


def notify_batch_completed(batch: "Batch") -> None:
    chat_id = batch.notify_chat_id or ""
    if not chat_id:
        return
    icon = "✓" if batch.status.value == "SUCCEEDED" else "✗"
    _send(
        chat_id,
        f"批次完成 {icon}\n"
        f"ID: {batch.id}\n"
        f"状态: {batch.status}\n"
        f"成功: {batch.succeeded_count}  失败: {batch.failed_count}  取消: {batch.canceled_count}\n"
        f"输出目录: {batch.output_dir}",
    )


def notify_batch_canceled(batch: "Batch") -> None:
    chat_id = batch.notify_chat_id or ""
    if not chat_id:
        return
    _send(
        chat_id,
        f"批次已取消 ✗\n"
        f"ID: {batch.id}\n"
        f"已完成: {batch.succeeded_count}  取消: {batch.canceled_count}",
    )


def should_send_progress(batch: "Batch") -> bool:
    """Return True when we should send a progress notification."""
    completed = batch.succeeded_count + batch.failed_count
    if completed == 0 or batch.total_count == 0:
        return False
    # Send every 10 tasks, or every 25% for large batches
    interval = max(10, batch.total_count // 4)
    return completed % interval == 0
