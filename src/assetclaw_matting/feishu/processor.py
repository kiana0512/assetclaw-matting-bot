from __future__ import annotations

import logging
import re

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

    if _is_simple_greeting(event.text):
        text_out = "你好。"
        _try_reply(event.message_id, event.chat_id, text_out)
        _log_direct_brain_message(conversation_id, event, text_out)
        update_event_dedup_status(dedup_key, "success")
        return FeishuProcessResult(ok=True, trace_id=trace_id, reply_text=text_out)

    # --- main processing chain -----------------------------------------------
    try:
        from assetclaw_matting.brain import router as brain_router
        from assetclaw_matting.brain.schemas import BrainMessage

        _try_reply(
            event.message_id,
            event.chat_id,
            "收到，处理中。",
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
                )
            )
        finally:
            reset_runtime_context(context_token)
            reset_progress_sender(progress_token)
        reply_text = response.text or "完成。"
        _try_reply(event.message_id, event.chat_id, reply_text)
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
    match = re.fullmatch(r"(确认执行|确认|yes|y)\s*([a-fA-F0-9]{6,})?", text, re.IGNORECASE)
    cancel = re.fullmatch(r"(取消|取消执行|cancel|no|n)\s*([a-fA-F0-9]{6,})?", text, re.IGNORECASE)
    if not match and not cancel:
        return None

    from assetclaw_matting.db.repos import (
        get_latest_pending_confirmation,
        mark_pending_confirmation,
        update_event_dedup_status,
    )

    pending = get_latest_pending_confirmation(conversation_id, user_key)
    if not pending:
        text_out = "当前没有等待你确认的操作。"
        _try_reply(event.message_id, event.chat_id, text_out)
        update_event_dedup_status(dedup_key, "success")
        return FeishuProcessResult(ok=True, trace_id=trace_id, reply_text=text_out)

    expected_id = pending["id"]
    provided_id = (match or cancel).group(2)
    if provided_id and provided_id != expected_id:
        text_out = f"确认码不匹配。当前待确认操作的确认码是：{expected_id}"
        _try_reply(event.message_id, event.chat_id, text_out)
        update_event_dedup_status(dedup_key, "success")
        return FeishuProcessResult(ok=True, trace_id=trace_id, reply_text=text_out)

    if cancel:
        mark_pending_confirmation(expected_id, "cancelled")
        text_out = f"已取消待确认操作：{pending['skill']}（{expected_id}）"
        _try_reply(event.message_id, event.chat_id, text_out)
        trace(
            "skill.confirmation_cancelled",
            trace_id=trace_id,
            conversation_id=conversation_id,
            confirmation_id=expected_id,
        )
        update_event_dedup_status(dedup_key, "success")
        return FeishuProcessResult(ok=True, trace_id=trace_id, reply_text=text_out)

    from assetclaw_matting.brain.result_formatter import format_skill_results
    from assetclaw_matting.skills.registry import call_skill

    _try_reply(event.message_id, event.chat_id, f"确认收到，正在执行：{pending['skill']}（{expected_id}）")
    trace(
        "skill.confirmed_execute",
        trace_id=trace_id,
        conversation_id=conversation_id,
        confirmation_id=expected_id,
        skill=pending["skill"],
        arguments=pending["arguments"],
    )
    context_token = set_runtime_context(
        channel="feishu",
        chat_id=event.chat_id,
        open_id=event.open_id,
        user_id=event.user_id,
        conversation_id=conversation_id,
        trace_id=trace_id,
    )
    try:
        result = call_skill(pending["skill"], pending["arguments"], requested_by="feishu_confirmed")
    finally:
        reset_runtime_context(context_token)
    mark_pending_confirmation(expected_id, "executed" if result.get("ok") else "failed")
    update_event_dedup_status(dedup_key, "success" if result.get("ok") else "failed")
    text_out = format_skill_results([result])
    _try_send_chat(event.chat_id, text_out)
    trace(
        "feishu.reply",
        trace_id=trace_id,
        conversation_id=conversation_id,
        chat_id=event.chat_id,
        open_id=event.open_id,
        text=text_out,
    )
    return FeishuProcessResult(ok=bool(result.get("ok")), trace_id=trace_id, reply_text=text_out)


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


def _is_simple_greeting(text: str) -> bool:
    normalized = re.sub(r"[\s!！。,.，~～]+", "", text.strip().lower())
    return normalized in {"hi", "hello", "hey", "你好", "您好", "嗨", "哈喽"}


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
