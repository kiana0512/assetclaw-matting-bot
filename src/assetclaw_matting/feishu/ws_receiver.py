"""
Feishu long-connection WebSocket receiver.

Connects to Feishu's official WebSocket endpoint using lark-oapi SDK.
No public IP, no domain, no Cloudflare Tunnel required.
Only requires outbound internet access to open.feishu.cn.

Run:
    python -m assetclaw_matting.feishu.ws_receiver
"""
from __future__ import annotations

import logging
import os
import signal
import sys
from pathlib import Path
from typing import BinaryIO
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="feishu_ws_proc")
_instance_lock: BinaryIO | None = None


def _acquire_instance_lock(path: Path) -> BinaryIO | None:
    """Acquire a non-blocking process-lifetime lock for the WS receiver."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        handle.close()
        return None
    return handle


def _release_instance_lock(handle: BinaryIO | None) -> None:
    if handle is None or handle.closed:
        return
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        handle.close()


def _build_event_handler(settings):
    try:
        import lark_oapi as lark
    except ImportError:
        log.error("lark_oapi not installed. Run: pip install lark-oapi")
        sys.exit(1)

    from assetclaw_matting.feishu.message_adapter import from_lark_event
    from assetclaw_matting.feishu.processor import process_feishu_message

    def _on_im_message_receive_v1(data) -> None:
        try:
            event = from_lark_event(data)
            log.info(
                "ws_event received trace_id=%s event_id=%s message_id=%s",
                event.trace_id, event.event_id, event.message_id,
            )
            _executor.submit(_safe_process, event)
        except Exception:
            log.exception("ws event dispatch error (event dropped)")

    def _safe_process(event):
        try:
            process_feishu_message(event)
        except Exception:
            log.exception("process_feishu_message unhandled error trace_id=%s", event.trace_id)

    # encrypt_key and verification_token are for webhook encryption; empty string = disabled
    encrypt_key = settings.feishu_encrypt_key or ""
    verification_token = settings.feishu_verification_token or ""

    handler = (
        lark.EventDispatcherHandler.builder(encrypt_key, verification_token)
        .register_p2_im_message_receive_v1(_on_im_message_receive_v1)
        .build()
    )
    return handler


def _build_ws_client(settings, event_handler):
    try:
        from lark_oapi.ws import Client as WSClient
    except ImportError as exc:
        log.error("lark_oapi.ws not available: %s", exc)
        sys.exit(1)

    return WSClient(
        settings.feishu_app_id,
        settings.feishu_app_secret,
        event_handler=event_handler,
    )


def main() -> None:
    global _instance_lock

    from assetclaw_matting.config import settings
    from assetclaw_matting.db.schema import create_tables
    from assetclaw_matting.db.sqlite import init_db
    from assetclaw_matting.logging_setup import setup_logging

    settings.ensure_dirs()
    lock_path = Path(settings.storage_dir) / ".feishu_ws_receiver.lock"
    _instance_lock = _acquire_instance_lock(lock_path)
    if _instance_lock is None:
        print("ERROR: another Feishu WS receiver is already running; refusing duplicate startup")
        raise SystemExit(2)
    setup_logging(settings.log_dir, name="feishu_ws")
    init_db(settings.data_db_path)
    create_tables()

    from assetclaw_matting.skills.animation_flow_skills import recover_incomplete_runs as recover_animation_flows
    from assetclaw_matting.skills.direct_image_skills import recover_incomplete_runs as recover_direct_images
    from assetclaw_matting.skills.direct_video_skills import recover_incomplete_runs

    recovery = recover_incomplete_runs()
    log.info(
        "direct video recovery scan recovered=%s still_running=%s",
        recovery.get("recovered"),
        recovery.get("still_running"),
    )
    flow_recovery = recover_animation_flows()
    log.info(
        "animation flow recovery scan closed=%s still_running=%s",
        flow_recovery.get("closed"),
        flow_recovery.get("still_running"),
    )
    image_recovery = recover_direct_images()
    log.info(
        "direct image recovery scan closed=%s still_running=%s",
        image_recovery.get("closed"),
        image_recovery.get("still_running"),
    )

    log.info("Feishu WS receiver initializing")
    log.info("event_mode=%s cloudflare=disabled public_exposure=none", settings.feishu_event_mode)

    if not settings.feishu_app_id or not settings.feishu_app_secret:
        log.error("FEISHU_APP_ID or FEISHU_APP_SECRET is not configured in .env")
        print("ERROR: FEISHU_APP_ID or FEISHU_APP_SECRET missing in .env")
        sys.exit(1)

    if settings.feishu_event_mode != "ws":
        log.warning(
            "FEISHU_EVENT_MODE=%s, expected 'ws'. Still starting WS receiver.",
            settings.feishu_event_mode,
        )

    event_handler = _build_event_handler(settings)
    ws_client = _build_ws_client(settings, event_handler)
    logging.getLogger("Lark").setLevel(logging.WARNING)
    logging.getLogger("Lark").disabled = True

    print("Feishu websocket: connecting...")
    print(f"App ID: {settings.feishu_app_id[:8]}***")
    print("Cloudflare: disabled")
    print("Public exposure: none")
    print("Event mode: ws")
    print("Press Ctrl+C to stop.")

    def _graceful_shutdown(sig, frame):
        log.info("Received signal %s, shutting down WS receiver", sig)
        print("\nShutting down WS receiver...")
        _executor.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, _graceful_shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _graceful_shutdown)

    try:
        ws_client.start()
    except KeyboardInterrupt:
        log.info("WS receiver stopped by user (KeyboardInterrupt)")
    except Exception as exc:
        log.exception("WS receiver crashed: %s", exc)
        sys.exit(1)
    finally:
        _executor.shutdown(wait=False)
        _release_instance_lock(_instance_lock)
        _instance_lock = None


if __name__ == "__main__":
    main()
