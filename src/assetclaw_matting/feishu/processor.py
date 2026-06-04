from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from pathlib import Path

from assetclaw_matting.errors import classify_exception
from assetclaw_matting.feishu.models import FeishuMessageEvent, FeishuProcessResult
from assetclaw_matting.ops_trace import trace
from assetclaw_matting.progress import reset_progress_sender, set_progress_sender
from assetclaw_matting.runtime_context import reset_runtime_context, set_runtime_context
from assetclaw_matting.skills.security import redact_secrets

log = logging.getLogger(__name__)


def process_feishu_message(event: FeishuMessageEvent) -> FeishuProcessResult:
    """
    Unified feishu message processor.
    Called by both ws_receiver (WS mode) and event_handler (webhook legacy mode).
    Full chain: dedup -> permission -> brain -> reply -> audit.
    """
    from assetclaw_matting.config import settings
    from assetclaw_matting.db.repos import try_insert_event_dedup, update_event_dedup_status

    trace_id = event.trace_id
    dedup_key = event.event_id or event.message_id or trace_id

    log.info(
        "process_message trace_id=%s event_id=%s message_id=%s chat_id=%s open_id=%s",
        trace_id, event.event_id, event.message_id, event.chat_id, event.open_id,
    )
    user_key = event.open_id or event.user_id or "unknown_user"
    conversation_id = f"feishu:{event.chat_id}:{user_key}"
    trace(
        "feishu.incoming",
        trace_id=trace_id,
        conversation_id=conversation_id,
        chat_id=event.chat_id,
        open_id=event.open_id,
        text=event.text,
    )
    # --- dedup ---------------------------------------------------------------
    is_new = try_insert_event_dedup(
        dedup_key, event.message_id, event.chat_id, event.open_id or "", trace_id
    )
    if not is_new:
        log.info("DEDUP HIT trace_id=%s key=%s", trace_id, dedup_key)
        return FeishuProcessResult(ok=True, trace_id=trace_id)

    if _is_stale_event(event):
        log.warning("ignore stale feishu event trace_id=%s message_id=%s", trace_id, event.message_id)
        update_event_dedup_status(dedup_key, "ignored_stale")
        return FeishuProcessResult(ok=True, trace_id=trace_id, reply_text="")

    # --- permission check ----------------------------------------------------
    if settings.feishu_allowed_chat_ids_list and event.chat_id not in settings.feishu_allowed_chat_ids_list:
        log.warning("blocked chat_id=%s trace_id=%s", event.chat_id, trace_id)
        update_event_dedup_status(dedup_key, "blocked")
        _try_reply(event.message_id, event.chat_id, "此群组未在允许列表中，机器人无法响应。")
        return FeishuProcessResult(ok=False, trace_id=trace_id, error={"reason": "chat_blocked"})

    if settings.feishu_allowed_open_ids_list and event.open_id not in settings.feishu_allowed_open_ids_list:
        log.warning("blocked open_id=%s trace_id=%s", event.open_id, trace_id)
        update_event_dedup_status(dedup_key, "blocked")
        _try_reply(event.message_id, event.chat_id, "你没有使用此机器人的权限。")
        return FeishuProcessResult(ok=False, trace_id=trace_id, error={"reason": "user_blocked"})

    confirmation_result = _try_handle_confirmation(event, conversation_id, user_key, trace_id, dedup_key)
    if confirmation_result is not None:
        return confirmation_result

    event.attachments = _prepare_attachments(event, conversation_id)
    _remember_recent_attachments(conversation_id, event.attachments)

    if _is_simple_greeting(event.text):
        text_out = "初音在。今天想让我陪你唱一会儿，还是一起把某个任务往前推一点？"
        _try_reply(event.message_id, event.chat_id, text_out)
        _try_send_emotional_sticker(event.chat_id, event.text, text_out)
        _log_direct_brain_message(conversation_id, event, text_out)
        update_event_dedup_status(dedup_key, "success")
        return FeishuProcessResult(ok=True, trace_id=trace_id, reply_text=text_out)

    # --- main processing chain -----------------------------------------------
    try:
        from assetclaw_matting.brain import router as brain_router
        from assetclaw_matting.brain.schemas import BrainMessage

        if _should_send_processing_ack(event):
            _try_reply(
                event.message_id,
                event.chat_id,
                "我收到啦，正在处理。",
            )

        def _progress_sender(text: str) -> None:
            log.info("progress trace_id=%s text=%s", trace_id, redact_secrets(text))

        progress_token = set_progress_sender(_progress_sender)
        context_token = set_runtime_context(
            channel="feishu",
            chat_id=event.chat_id,
            open_id=event.open_id,
            user_id=event.user_id,
            conversation_id=conversation_id,
            trace_id=trace_id,
        )
        try:
            response = brain_router.handle_message(
                BrainMessage(
                    channel="feishu",
                    conversation_id=conversation_id,
                    user_id=event.open_id or "",
                    text=event.text,
                    attachments=event.attachments,
                )
            )
        finally:
            reset_runtime_context(context_token)
            reset_progress_sender(progress_token)
        reply_text = response.text or "完成。"
        _try_reply(event.message_id, event.chat_id, reply_text)
        _try_send_emotional_sticker(event.chat_id, event.text, reply_text)
        trace(
            "feishu.reply",
            trace_id=trace_id,
            conversation_id=conversation_id,
            chat_id=event.chat_id,
            open_id=event.open_id,
            text=reply_text,
        )
        update_event_dedup_status(dedup_key, "success")
        log.info("process_message SUCCESS trace_id=%s", trace_id)
        return FeishuProcessResult(ok=True, trace_id=trace_id, reply_text=reply_text)

    except Exception as exc:
        update_event_dedup_status(dedup_key, "failed")
        log.exception("process_message FAILED trace_id=%s", trace_id)
        envelope = classify_exception(exc, phase="feishu_event", trace_id=trace_id)
        if settings.bot_error_push_enabled:
            _try_reply(event.message_id, event.chat_id, envelope.to_feishu_text())
        return FeishuProcessResult(
            ok=False,
            trace_id=trace_id,
            error=envelope.to_log_dict(),
        )


def _try_handle_confirmation(
    event: FeishuMessageEvent,
    conversation_id: str,
    user_key: str,
    trace_id: str,
    dedup_key: str,
) -> FeishuProcessResult | None:
    text = event.text.strip()
    confirm_like = re.search(r"\b(?:yes|y)\b|确认(?:执行)?", text, re.IGNORECASE)
    cancel_like = _is_confirmation_cancel(text)
    if not confirm_like and not cancel_like:
        return None

    from assetclaw_matting.db.repos import (
        get_pending_confirmation_by_id,
        get_latest_pending_confirmation,
        mark_pending_confirmation,
        update_event_dedup_status,
    )

    provided_ids = re.findall(r"\b[a-fA-F0-9]{6,}\b", text)
    if provided_ids:
        pending_items = []
        missing = []
        for provided_id in provided_ids:
            pending = get_pending_confirmation_by_id(provided_id, conversation_id, user_key)
            if pending:
                pending_items.append(pending)
            else:
                missing.append(provided_id)
        if not pending_items:
            text_out = "这些确认码当前都不可用，可能已执行、过期或不属于这个会话。"
            _try_reply(event.message_id, event.chat_id, text_out)
            update_event_dedup_status(dedup_key, "success")
            return FeishuProcessResult(ok=True, trace_id=trace_id, reply_text=text_out)
    else:
        pending = get_latest_pending_confirmation(conversation_id, user_key)
        pending_items = [pending] if pending else []
        missing = []

    if not pending_items:
        text_out = "当前没有等待你确认的操作。"
        _try_reply(event.message_id, event.chat_id, text_out)
        update_event_dedup_status(dedup_key, "success")
        return FeishuProcessResult(ok=True, trace_id=trace_id, reply_text=text_out)

    if cancel_like and not confirm_like:
        lines = []
        for pending in pending_items:
            mark_pending_confirmation(pending["id"], "cancelled")
            lines.append(f"已取消：{pending['skill']}（{pending['id']}）")
        if missing:
            lines.append("未找到：" + "、".join(missing))
        text_out = "\n".join(lines)
        _try_reply(event.message_id, event.chat_id, text_out)
        for pending in pending_items:
            trace(
                "skill.confirmation_cancelled",
                trace_id=trace_id,
                conversation_id=conversation_id,
                confirmation_id=pending["id"],
            )
        update_event_dedup_status(dedup_key, "success")
        return FeishuProcessResult(ok=True, trace_id=trace_id, reply_text=text_out)

    from assetclaw_matting.brain.result_formatter import format_skill_results
    from assetclaw_matting.skills.registry import call_skill

    if len(pending_items) == 1:
        pending_text = f"确认收到，正在执行：{pending_items[0]['skill']}（{pending_items[0]['id']}）"
    else:
        pending_text = f"确认收到，正在执行 {len(pending_items)} 个操作。"
    _try_reply(event.message_id, event.chat_id, pending_text)
    context_token = set_runtime_context(
        channel="feishu",
        chat_id=event.chat_id,
        open_id=event.open_id,
        user_id=event.user_id,
        conversation_id=conversation_id,
        trace_id=trace_id,
    )
    results = []
    try:
        for pending in pending_items:
            trace(
                "skill.confirmed_execute",
                trace_id=trace_id,
                conversation_id=conversation_id,
                confirmation_id=pending["id"],
                skill=pending["skill"],
                arguments=pending["arguments"],
            )
            result = call_skill(pending["skill"], pending["arguments"], requested_by="feishu_confirmed")
            mark_pending_confirmation(pending["id"], "executed" if result.get("ok") else "failed")
            results.append(result)
    finally:
        reset_runtime_context(context_token)
    update_event_dedup_status(dedup_key, "success" if all(result.get("ok") for result in results) else "failed")
    text_out = format_skill_results(results)
    if missing:
        text_out = text_out + "\n未找到：" + "、".join(missing)
    _try_send_chat(event.chat_id, text_out)
    _try_send_emotional_sticker(event.chat_id, event.text, text_out)
    trace(
        "feishu.reply",
        trace_id=trace_id,
        conversation_id=conversation_id,
        chat_id=event.chat_id,
        open_id=event.open_id,
        text=text_out,
    )
    return FeishuProcessResult(ok=all(bool(result.get("ok")) for result in results), trace_id=trace_id, reply_text=text_out)


def _is_confirmation_cancel(text: str) -> bool:
    stripped = text.strip()
    if re.search(r"(COMFY|CHERRY|FRAME|PIPE|SMAT)_[A-Fa-f0-9]{12}", stripped):
        return False
    if re.fullmatch(r"(?:取消|取消执行|cancel|no|n)", stripped, re.IGNORECASE):
        return True
    return bool(re.fullmatch(r"(?:取消|取消执行)\s+[a-fA-F0-9]{6,}", stripped, re.IGNORECASE))


def _try_reply(message_id: str, chat_id: str, text: str) -> None:
    from assetclaw_matting.feishu.client import feishu_client

    if message_id:
        try:
            feishu_client.reply_text(message_id, text)
            return
        except Exception as exc:
            log.error("reply via message_id failed: %s", redact_secrets(str(exc)))
    if chat_id:
        _try_send_chat(chat_id, text)


def _try_send_chat(chat_id: str, text: str) -> None:
    from assetclaw_matting.feishu.client import feishu_client

    if not chat_id:
        return
    try:
        feishu_client.send_text_to_chat(chat_id, text)
    except Exception as exc:
        log.error("send to chat failed: %s", redact_secrets(str(exc)))


def _try_send_emotional_sticker(chat_id: str, message_text: str, reply_text: str) -> None:
    from assetclaw_matting.services.sticker_service import send_sticker_to_chat

    if not chat_id:
        return
    try:
        send_sticker_to_chat(chat_id, message_text=message_text, reply_text=reply_text)
    except Exception as exc:
        log.error("send emotional sticker failed: %s", redact_secrets(str(exc)))


def _prepare_attachments(event: FeishuMessageEvent, conversation_id: str) -> list[dict[str, object]]:
    if not event.attachments:
        return []
    from assetclaw_matting.config import settings
    from assetclaw_matting.feishu.client import feishu_client

    safe_conversation = re.sub(r"[^a-zA-Z0-9_.-]+", "_", conversation_id)[-80:]
    day = datetime.now().strftime("%Y%m%d")
    inbox = settings.storage_dir / "feishu_inbox" / day / safe_conversation
    prepared: list[dict[str, object]] = []
    for index, attachment in enumerate(event.attachments, start=1):
        item = dict(attachment)
        name = _safe_attachment_name(str(item.get("file_name") or f"attachment_{index}.bin"))
        target = _unique_path(inbox / name)
        resource_type = _download_resource_type(str(item.get("type") or "file"))
        try:
            feishu_client.download_message_resource(
                event.message_id,
                str(item.get("resource_key") or ""),
                target,
                resource_type=resource_type,  # type: ignore[arg-type]
            )
            item["local_path"] = str(target)
            item["file_name"] = target.name
            item["size"] = target.stat().st_size
            item["downloaded"] = True
        except Exception as exc:
            log.error("download feishu attachment failed: %s", redact_secrets(str(exc)))
            item["downloaded"] = False
            item["error"] = str(exc)
            trace(
                "feishu.attachment_download_failed",
                trace_id=event.trace_id,
                conversation_id=conversation_id,
                message_id=event.message_id,
                resource_type=resource_type,
                error=redact_secrets(str(exc)),
            )
        prepared.append(item)
    return prepared


def _download_resource_type(raw_type: str) -> str:
    if raw_type in {"image", "file", "video", "audio", "media"}:
        return raw_type
    return "file"


def _is_stale_event(event: FeishuMessageEvent) -> bool:
    from assetclaw_matting.config import settings

    threshold = max(0, int(settings.feishu_ignore_events_older_than_seconds or 0))
    if threshold <= 0 or not event.message_create_time:
        return False
    created = event.message_create_time / (1000 if event.message_create_time > 10_000_000_000 else 1)
    return time.time() - created > threshold


def _remember_recent_attachments(conversation_id: str, attachments: list[dict[str, object]]) -> None:
    if not attachments:
        return
    from assetclaw_matting.db.repos import upsert_memory_note
    from assetclaw_matting.skills.media_skills import IMAGE_EXTS

    for item in reversed(attachments):
        path = str(item.get("local_path") or "")
        if path and Path(path).suffix.lower() in IMAGE_EXTS:
            upsert_memory_note(conversation_id, "last_image_path", path, source="feishu_attachment")
            upsert_memory_note(conversation_id, "last_image_name", str(item.get("file_name") or Path(path).name), source="feishu_attachment")
            return


def _safe_attachment_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(". ")
    return cleaned or "feishu_attachment.bin"


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for i in range(2, 1000):
        candidate = path.with_name(f"{stem}_{i}{suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"too many duplicate attachment names: {path}")


def _is_simple_greeting(text: str) -> bool:
    normalized = re.sub(r"[\s!！。,.，~～]+", "", text.strip().lower())
    return normalized in {"hi", "hello", "hey", "你好", "您好", "嗨", "哈喽"}


def _should_send_processing_ack(event: FeishuMessageEvent) -> bool:
    if event.attachments:
        return True
    text = (event.text or "").strip()
    if not text:
        return False
    try:
        from assetclaw_matting.brain.emotion_planner import plan_emotional_reply

        if plan_emotional_reply(text):
            return False
    except Exception:
        log.debug("failed to classify conversational message for ack", exc_info=True)
    return True


def _log_direct_brain_message(conversation_id: str, event: FeishuMessageEvent, response_text: str) -> None:
    from assetclaw_matting.db.repos import insert_brain_message

    try:
        insert_brain_message(
            provider="direct",
            channel="feishu",
            conversation_id=conversation_id,
            user_id=event.open_id or "",
            message_text=event.text,
            response_text=response_text,
            tool_calls_json="[]",
            raw_json="{}",
        )
    except Exception:
        log.debug("failed to log direct message", exc_info=True)
