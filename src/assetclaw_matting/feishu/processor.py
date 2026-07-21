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

    folder_result = _try_handle_folder_message(event, conversation_id, trace_id, dedup_key)
    if folder_result is not None:
        return folder_result

    if _is_simple_greeting(event.text):
        text_out = "初音在。今天想让我陪你聊一会儿，还是一起把某个任务往前推一点？"
        _try_reply(event.message_id, event.chat_id, text_out)
        _try_send_tts_reply(event.chat_id, conversation_id, event, text_out)
        _try_send_emotional_sticker(event.chat_id, event.text, text_out)
        _log_direct_brain_message(conversation_id, event, text_out)
        update_event_dedup_status(dedup_key, "success")
        return FeishuProcessResult(ok=True, trace_id=trace_id, reply_text=text_out)

    # --- main processing chain -----------------------------------------------
    try:
        from assetclaw_matting.brain.schemas import BrainMessage

        if _should_send_processing_ack(event):
            _try_reply(
                event.message_id,
                event.chat_id,
                _processing_ack_text(event),
            )
        if _is_progress_query(event.text):
            _try_add_progress_reaction(event)

        if settings.agent_queue_enabled:
            from assetclaw_matting.services.agent_job_queue import enqueue_brain_job

            def _on_job_done(job: dict[str, object]) -> None:
                if str(job.get("status") or "") == "DONE":
                    response = job.get("response") if isinstance(job.get("response"), dict) else {}
                    reply_text = str(response.get("text") or "完成。")
                    _try_reply(event.message_id, event.chat_id, reply_text)
                    _try_send_tts_reply(event.chat_id, conversation_id, event, reply_text)
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
                    log.info("process_message ASYNC SUCCESS trace_id=%s job_id=%s", trace_id, job.get("job_id"))
                    return
                update_event_dedup_status(dedup_key, "failed")
                error_text = str(job.get("error") or "Agent 后台任务失败")
                if settings.bot_error_push_enabled:
                    _try_reply(event.message_id, event.chat_id, f"这条消息后台处理失败：{error_text}")
                log.error("process_message ASYNC FAILED trace_id=%s job_id=%s error=%s", trace_id, job.get("job_id"), error_text)

            job = enqueue_brain_job(
                BrainMessage(
                    channel="feishu",
                    conversation_id=conversation_id,
                    user_id=event.open_id or "",
                    text=event.text,
                    attachments=event.attachments,
                ),
                trace_id=trace_id,
                context={
                    "channel": "feishu",
                    "chat_id": event.chat_id,
                    "open_id": event.open_id,
                    "user_id": event.user_id,
                    "conversation_id": conversation_id,
                    "trace_id": trace_id,
                },
                callback=_on_job_done,
            )
            log.info("process_message QUEUED trace_id=%s job_id=%s", trace_id, job.get("job_id"))
            return FeishuProcessResult(ok=True, trace_id=trace_id, reply_text="已进入后台队列。")

        from assetclaw_matting.brain import router as brain_router

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
        _try_send_tts_reply(event.chat_id, conversation_id, event, reply_text)
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
    provided_ids = re.findall(r"\b[a-fA-F0-9]{6,}\b", text)
    bare_confirmation_code = bool(re.fullmatch(r"\s*[a-fA-F0-9]{6,}\s*", text))
    confirm_like = re.search(r"\b(?:yes|y)\b|确认(?:执行)?", text, re.IGNORECASE) or bare_confirmation_code
    cancel_like = _is_confirmation_cancel(text)
    if not confirm_like and not cancel_like:
        return None

    from assetclaw_matting.db.repos import (
        get_pending_confirmation_by_id,
        get_latest_pending_confirmation,
        claim_pending_confirmation,
        mark_pending_confirmation,
        supersede_similar_pending_confirmations,
        update_event_dedup_status,
    )

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

    context_token = set_runtime_context(
        channel="feishu",
        chat_id=event.chat_id,
        open_id=event.open_id,
        user_id=event.user_id,
        conversation_id=conversation_id,
        trace_id=trace_id,
    )
    results = []
    claimed_count = 0
    try:
        for pending in pending_items:
            if not claim_pending_confirmation(pending["id"]):
                continue
            claimed_count += 1
            if claimed_count == 1:
                pending_text = "收到，开始处理。" if len(pending_items) == 1 else f"收到，开始处理 {len(pending_items)} 个任务。"
                _try_reply(event.message_id, event.chat_id, pending_text)
            trace(
                "skill.confirmed_execute",
                trace_id=trace_id,
                conversation_id=conversation_id,
                confirmation_id=pending["id"],
                skill=pending["skill"],
                arguments=pending["arguments"],
            )
            arguments = _normalize_confirmed_arguments(pending["skill"], dict(pending["arguments"] or {}))
            supersede_similar_pending_confirmations(conversation_id, user_key, pending["skill"], arguments, pending["id"])
            result = call_skill(pending["skill"], arguments, requested_by="feishu_confirmed")
            mark_pending_confirmation(pending["id"], "executed" if result.get("ok") else "failed")
            results.append(result)
    finally:
        reset_runtime_context(context_token)
    if not results:
        text_out = "这个确认已经处理过了。"
        update_event_dedup_status(dedup_key, "success")
        _try_send_chat(event.chat_id, text_out)
        return FeishuProcessResult(ok=True, trace_id=trace_id, reply_text=text_out)
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


def _normalize_confirmed_arguments(skill: str, arguments: dict) -> dict:
    if skill != "animation_flow.start":
        return arguments
    mode = arguments.get("unity_import_mode") or arguments.get("import_mode")
    if mode is not None:
        text = str(mode).strip().lower()
        if text in {"迭代", "资源迭代", "替换", "贴图迭代", "高清化", "直接替换", "iteration", "iterate", "replace", "replacement", "update", "iter"}:
            arguments["unity_import_mode"] = "iteration"
            arguments.pop("import_mode", None)
        elif text in {"导入", "新导入", "批量导入", "import", "new", "batch"}:
            arguments["unity_import_mode"] = "import"
            arguments.pop("import_mode", None)
    priority = arguments.get("priority_characters")
    if isinstance(priority, str):
        values = [item.strip() for item in re.split(r"[,，、\s]+", priority) if item.strip()]
        arguments["priority_characters"] = values
    return arguments


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


def _try_add_progress_reaction(event: FeishuMessageEvent) -> None:
    from assetclaw_matting.config import settings
    from assetclaw_matting.feishu.client import feishu_client

    if not bool(getattr(settings, "feishu_progress_reaction_enabled", True)):
        return
    if not event.message_id:
        return
    emoji_types = [
        item.strip()
        for item in str(getattr(settings, "feishu_progress_reaction_emoji_types", "") or "").split(";")
        if item.strip()
    ]
    for emoji_type in emoji_types:
        try:
            if feishu_client.add_message_reaction(event.message_id, emoji_type):
                return
        except Exception as exc:
            log.debug("add progress reaction failed emoji=%s error=%s", emoji_type, redact_secrets(str(exc)))


def _try_send_emotional_sticker(chat_id: str, message_text: str, reply_text: str) -> None:
    from assetclaw_matting.services.sticker_service import send_sticker_to_chat

    if not chat_id:
        return
    try:
        send_sticker_to_chat(chat_id, message_text=message_text, reply_text=reply_text)
    except Exception as exc:
        log.error("send emotional sticker failed: %s", redact_secrets(str(exc)))


def _try_send_tts_reply(chat_id: str, conversation_id: str, event: FeishuMessageEvent, reply_text: str) -> None:
    from assetclaw_matting.config import settings

    if not bool(getattr(settings, "bot_tts_enabled", False)):
        return
    if not chat_id or not _should_send_voice_reply(event, conversation_id):
        return
    clean_text = (reply_text or "").strip()
    if not clean_text:
        return
    try:
        from assetclaw_matting.feishu.client import feishu_client
        from assetclaw_matting.skills.speech_skills import synthesize

        _try_send_tts_progress(chat_id)
        payload = synthesize(clean_text)
        if not payload.get("ok"):
            log.error("tts synthesize failed: %s", redact_secrets(str(payload.get("error") or payload)))
            return
        target = Path(str(payload["output_path"]))
        feishu_client.send_file_to_chat(chat_id, target, str(payload.get("file_name") or target.name))
    except Exception as exc:
        log.error("send tts reply failed: %s", redact_secrets(str(exc)))


def _try_send_tts_progress(chat_id: str) -> None:
    from assetclaw_matting.config import settings

    if not bool(getattr(settings, "voice_reply_progress_enabled", True)):
        return
    _try_send_chat(chat_id, "文字先给你，语音正在合成。通常 8-20 秒，首次加载本地 TTS 模型可能更久。")


def _should_send_voice_reply(event: FeishuMessageEvent, conversation_id: str) -> bool:
    from assetclaw_matting.config import settings
    from assetclaw_matting.brain.speech_planner import voice_reply_enabled

    if not bool(getattr(settings, "bot_tts_enabled", False)):
        return False
    if bool(getattr(settings, "voice_reply_on_audio", True)) and _has_audio_attachment(event):
        return True
    return voice_reply_enabled(conversation_id)


def _has_audio_attachment(event: FeishuMessageEvent) -> bool:
    if not event.attachments:
        return False
    from assetclaw_matting.skills.speech_skills import AUDIO_EXTS
    from assetclaw_matting.skills.media_skills import VIDEO_EXTS

    for item in event.attachments:
        raw_type = str(item.get("type") or "").lower()
        if raw_type in {"video", "media"}:
            continue
        path = str(item.get("local_path") or "")
        name = str(item.get("file_name") or "")
        if Path(path or name).suffix.lower() in VIDEO_EXTS:
            continue
        if raw_type in {"audio", "voice"}:
            return True
        if Path(path or name).suffix.lower() in AUDIO_EXTS:
            return True
    return False


def _prepare_attachments(event: FeishuMessageEvent, conversation_id: str) -> list[dict[str, object]]:
    if not event.attachments:
        return []
    from assetclaw_matting.config import settings
    from assetclaw_matting.feishu.client import feishu_client

    safe_conversation = re.sub(r"[^a-zA-Z0-9_.-]+", "_", conversation_id)[-80:]
    day = datetime.now().strftime("%Y%m%d")
    inbox = settings.storage_dir / "feishu_inbox" / day / safe_conversation
    prepared: list[dict[str, object]] = []
    pending_collection = _pending_folder_name(conversation_id)
    for index, attachment in enumerate(event.attachments, start=1):
        item = dict(attachment)
        if str(item.get("type") or "").lower() == "folder":
            item["downloaded"] = False
            item["folder_message"] = True
            item["error"] = "Feishu bot API does not expose native chat-folder contents"
            prepared.append(item)
            continue
        if _should_skip_compressed_feishu_video_download(item):
            item["downloaded"] = False
            item["download_skipped"] = True
            item["error"] = "feishu media/video messages may be transcoded; send as file for original quality"
            prepared.append(item)
            continue
        name = _safe_attachment_name(str(item.get("file_name") or f"attachment_{index}.bin"))
        if str(item.get("type") or "").lower() in {"audio", "voice"} and Path(name).suffix.lower() not in _audio_suffixes():
            name = f"{Path(name).stem or f'voice_{index}'}.mp3"
        target = _unique_path(inbox / name)
        resource_type = _download_resource_type(str(item.get("type") or "file"))
        try:
            used_resource_type = _download_message_resource(
                feishu_client,
                event.message_id,
                str(item.get("resource_key") or ""),
                target,
                resource_type,
            )
            item["local_path"] = str(target)
            item["file_name"] = target.name
            item["size"] = target.stat().st_size
            if str(item.get("type") or "").lower() == "video" and not _looks_like_video_file(target):
                try:
                    target.unlink()
                except OSError:
                    pass
                raise RuntimeError(f"downloaded video resource is not a playable video file: {target.name}")
            item["downloaded"] = True
            item["download_resource_type"] = used_resource_type
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
    image_items = [
        item for item in prepared
        if str(item.get("type") or "").lower() == "image" and item.get("local_path")
    ]
    if pending_collection and image_items:
        for item in image_items:
            item["source_collection"] = pending_collection
        from assetclaw_matting.db.repos import upsert_memory_note

        upsert_memory_note(conversation_id, "pending_image_collection_name", "", source="feishu_attachment")
    return prepared


def _should_skip_compressed_feishu_video_download(item: dict[str, object]) -> bool:
    source_type = str(item.get("source_message_type") or "").lower()
    raw_type = str(item.get("type") or "").lower()
    return source_type in {"media", "video"} and raw_type in {"video", "media"}


def _download_message_resource(
    feishu_client: object,
    message_id: str,
    resource_key: str,
    target: Path,
    resource_type: str,
) -> str:
    normalized = _download_resource_type(resource_type)
    getattr(feishu_client, "download_message_resource")(
        message_id,
        resource_key,
        target,
        resource_type=normalized,
    )
    return normalized


def _looks_like_video_file(path: Path) -> bool:
    try:
        header = path.read_bytes()[:512]
    except OSError:
        return False
    if not header:
        return False
    if header.startswith(b"\xff\xd8\xff") or header.startswith(b"\x89PNG\r\n\x1a\n") or header.startswith(b"GIF8"):
        return False
    suffix = path.suffix.lower()
    if suffix in {".mp4", ".mov", ".m4v"}:
        return b"ftyp" in header[:32] or b"moov" in header[:256] or b"mdat" in header[:256]
    if suffix == ".avi":
        return header.startswith(b"RIFF") and b"AVI" in header[:32]
    if suffix in {".mkv", ".webm"}:
        return header.startswith(b"\x1a\x45\xdf\xa3")
    return True


def _download_resource_type(raw_type: str) -> str:
    if raw_type == "media":
        return "video"
    if raw_type in {"image", "file", "video", "audio"}:
        return raw_type
    return "file"


def _audio_suffixes() -> set[str]:
    from assetclaw_matting.skills.speech_skills import AUDIO_EXTS

    return AUDIO_EXTS


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

    folders = [item for item in attachments if str(item.get("type") or "").lower() == "folder"]
    if folders:
        folder_name = str(folders[-1].get("file_name") or "序列帧").strip()
        upsert_memory_note(conversation_id, "pending_image_collection_name", folder_name, source="feishu_folder")
        return

    for item in reversed(attachments):
        path = str(item.get("local_path") or "")
        if path and _looks_like_image_set_path(Path(path)):
            upsert_memory_note(conversation_id, "last_image_set_path", path, source="feishu_attachment")
            upsert_memory_note(conversation_id, "last_image_set_name", str(item.get("file_name") or Path(path).name), source="feishu_attachment")
            if Path(path).suffix.lower() not in IMAGE_EXTS:
                return
        if path and Path(path).suffix.lower() in IMAGE_EXTS:
            upsert_memory_note(conversation_id, "last_image_path", path, source="feishu_attachment")
            upsert_memory_note(conversation_id, "last_image_name", str(item.get("file_name") or Path(path).name), source="feishu_attachment")
            return


def _pending_folder_name(conversation_id: str) -> str:
    from assetclaw_matting.db.repos import list_memory_notes

    for note in list_memory_notes(conversation_id, limit=30):
        if note.get("key") == "pending_image_collection_name":
            name = str(note.get("value") or "").strip()
            if not name:
                return ""
            try:
                updated = datetime.fromisoformat(str(note.get("updated_at") or ""))
                now = datetime.now(updated.tzinfo) if updated.tzinfo else datetime.now()
                if (now - updated).total_seconds() > 3600:
                    return ""
            except (TypeError, ValueError):
                return ""
            return name
    return ""


def _try_handle_folder_message(
    event: FeishuMessageEvent,
    conversation_id: str,
    trace_id: str,
    dedup_key: str,
) -> FeishuProcessResult | None:
    folders = [item for item in event.attachments if str(item.get("type") or "").lower() == "folder"]
    if not folders:
        return None
    from assetclaw_matting.db.repos import update_event_dedup_status

    name = str(folders[0].get("file_name") or "序列帧")
    text_out = (
        f"已收到文件夹「{name}」，但飞书暂不向机器人提供文件夹内的文件。"
        "请一次全选其中的图片直接发送，不用逐张发送；"
        f"我会自动合并为一个「{name}」序列任务，按文件名顺序处理，并只返回一个 ZIP。"
    )
    _try_reply(event.message_id, event.chat_id, text_out)
    _log_direct_brain_message(conversation_id, event, text_out)
    update_event_dedup_status(dedup_key, "success")
    trace(
        "feishu.folder_recognized",
        trace_id=trace_id,
        conversation_id=conversation_id,
        message_id=event.message_id,
        folder_name=name,
    )
    return FeishuProcessResult(ok=True, trace_id=trace_id, reply_text=text_out)


def _looks_like_image_set_path(path: Path) -> bool:
    from assetclaw_matting.skills.media_skills import IMAGE_EXTS

    suffix = path.suffix.lower()
    if suffix == ".zip":
        return True
    if path.is_dir():
        try:
            return any(item.is_file() and item.suffix.lower() in IMAGE_EXTS for item in path.rglob("*"))
        except OSError:
            return False
    return suffix in IMAGE_EXTS


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
        return _has_audio_attachment(event) and not _is_direct_media_attachment_event(event)
    text = (event.text or "").strip()
    if not text:
        return False
    if _is_progress_query(text):
        return False
    if _is_task_control_query(text):
        return False
    try:
        from assetclaw_matting.brain.emotion_planner import plan_emotional_reply

        if plan_emotional_reply(text):
            return False
    except Exception:
        log.debug("failed to classify conversational message for ack", exc_info=True)
    return True


def _is_progress_query(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text or "")
    return any(
        keyword in normalized
        for keyword in (
            "进度",
            "状态",
            "到哪",
            "哪里了",
            "做到哪",
            "跑到哪",
            "处理到哪",
            "完成了吗",
            "好了吗",
            "具体信息",
            "详细信息",
            "任务详情",
            "汇总",
        )
    )


def _is_task_control_query(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text or "")
    return any(
        keyword in normalized
        for keyword in (
            "取消任务",
            "终止任务",
            "停止任务",
            "取消这个任务",
            "终止这个任务",
            "停止这个任务",
            "取消视频",
            "终止视频",
            "停止视频",
            "取消图片",
            "终止图片",
            "停止图片",
        )
    )


def _is_direct_media_attachment_event(event: FeishuMessageEvent) -> bool:
    if not event.attachments:
        return False
    try:
        from assetclaw_matting.skills.media_skills import IMAGE_EXTS, VIDEO_EXTS
    except Exception:
        IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}  # type: ignore[assignment]
        VIDEO_EXTS = {".mp4", ".mov", ".avi", ".webm"}  # type: ignore[assignment]
    media_exts = set(IMAGE_EXTS) | set(VIDEO_EXTS)
    for item in event.attachments:
        raw_type = str(item.get("type") or "").lower()
        if raw_type in {"image", "video", "media"}:
            return True
        path = str(item.get("local_path") or item.get("file_name") or "")
        if Path(path).suffix.lower() in media_exts:
            return True
    return False


def _processing_ack_text(event: FeishuMessageEvent) -> str:
    if _has_audio_attachment(event):
        suffix = " 如果还要合成语音，我会先发文字结果，再补发语音。"
        if _deepseek_thinking_enabled():
            return "收到语音，转文字后处理。" + suffix
        return "收到语音，转文字中。" + suffix
    if event.attachments:
        return "附件收到，处理中。"
    if _deepseek_thinking_enabled():
        return "收到，思考中。"
    return "收到，处理中。"


def _deepseek_thinking_enabled() -> bool:
    from assetclaw_matting.config import settings

    if str(getattr(settings, "brain_provider", "")).lower() != "deepseek":
        return False
    thinking_type = str(getattr(settings, "deepseek_thinking_type", "") or "").strip().lower()
    reasoning_effort = str(getattr(settings, "deepseek_reasoning_effort", "") or "").strip().lower()
    return thinking_type not in {"", "disabled", "none", "off", "false"} or reasoning_effort in {"high", "maximum"}


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
