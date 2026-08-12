from __future__ import annotations

import os
import subprocess
import sys
import threading
import uuid
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any, Literal

from assetclaw_matting.config import settings


ACTIVE = {"QUEUED", "PACKAGING", "UPLOADING"}
Kind = Literal["image", "video"]
_REQUEST_LOCK = threading.Lock()


def _serialized_request(function: Any) -> Any:
    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        with _REQUEST_LOCK:
            return function(*args, **kwargs)

    return wrapped


@_serialized_request
def request_redelivery(kind: Kind, run_id: str | None) -> dict[str, Any]:
    module = _module(kind)
    run = module._load(run_id)
    if not run:
        return {"ok": False, "error": f"direct {kind} run not found"}
    if not str(run.get("chat_id") or "").strip():
        return {"ok": False, "error": "任务没有原飞书会话，无法定向重发"}
    if str(run.get("status") or "").upper() not in {"DONE", "FAILED", "CANCELED", "DONE_WITH_ERRORS"}:
        return {"ok": False, "error": "任务仍在处理，请完成或停止后再重发已有完整素材"}
    current = dict(run.get("redelivery") or {})
    if str(current.get("status") or "").upper() in ACTIVE and _pid_alive(current.get("worker_pid")):
        return {"ok": True, "already_running": True, "run_id": run["id"], "redelivery": current}

    request_id = "RESEND_" + uuid.uuid4().hex[:12].upper()
    redelivery = {
        "request_id": request_id,
        "status": "QUEUED",
        "requested_at": _now(),
        "updated_at": _now(),
        "worker_pid": 0,
        "file_name": "",
        "file_size": 0,
        "message_id": "",
        "file_token": "",
        "url": "",
        "delivery_method": "",
        "error": "",
    }
    run["redelivery"] = redelivery
    module._append_log(run, "已创建完整素材重发任务；正在后台重新打包。")
    module._save(run)

    script = Path(__file__).resolve().parents[3] / "scripts" / "task_redelivery_worker.py"
    log_path = Path(settings.storage_dir) / "logs" / f"task_redelivery_{run['id']}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    flags = 0
    if os.name == "nt":
        flags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) | int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        with log_path.open("a", encoding="utf-8") as log_handle:
            process = subprocess.Popen(
                [sys.executable, str(script), kind, str(run["id"]), request_id],
                cwd=str(Path(__file__).resolve().parents[3]),
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                creationflags=flags,
                close_fds=True,
            )
    except Exception as exc:
        latest = module._load(str(run["id"])) or run
        latest["redelivery"].update({
            "status": "FAILED",
            "updated_at": _now(),
            "finished_at": _now(),
            "error": f"重发后台进程启动失败：{exc}",
        })
        module._append_log(latest, latest["redelivery"]["error"])
        module._save(latest)
        return {"ok": False, "run_id": run["id"], "error": latest["redelivery"]["error"]}
    latest = module._load(str(run["id"])) or run
    if str((latest.get("redelivery") or {}).get("request_id") or "") == request_id:
        latest["redelivery"]["worker_pid"] = int(process.pid)
        latest["redelivery"]["updated_at"] = _now()
        module._save(latest)
    return {"ok": True, "run_id": run["id"], "redelivery": latest.get("redelivery") or redelivery}


def run_redelivery(kind: Kind, run_id: str, request_id: str) -> dict[str, Any]:
    module = _module(kind)
    try:
        run = _update(kind, run_id, request_id, "PACKAGING")
        if kind == "video":
            package = _package_video(module, run)
        else:
            package = _package_image(module, run)
        run = _update(
            kind,
            run_id,
            request_id,
            "UPLOADING",
            file_name=package.name,
            file_size=package.stat().st_size,
        )
        from assetclaw_matting.feishu.client import feishu_client

        receipt = feishu_client.send_file_to_chat(str(run["chat_id"]), package, package.name) or {}
        message_id = str(receipt.get("message_id") or "")
        if not message_id:
            raise RuntimeError("飞书发送没有返回 message_id，不能确认交付成功")
        run = _update(
            kind,
            run_id,
            request_id,
            "DONE",
            message_id=message_id,
            file_token=str(receipt.get("file_token") or ""),
            url=str(receipt.get("url") or ""),
            delivery_method=str(receipt.get("delivery_method") or ""),
            finished_at=_now(),
        )
        history = list(run.get("redelivery_history") or [])
        history.append(dict(run["redelivery"]))
        run["redelivery_history"] = history[-20:]
        module._append_log(run, f"完整素材已重新发送：{package.name}，message_id={message_id}")
        module._save(run)
        return {"ok": True, "run_id": run_id, "redelivery": run["redelivery"]}
    except Exception as exc:
        try:
            run = _update(kind, run_id, request_id, "FAILED", error=str(exc), finished_at=_now())
            module._append_log(run, f"完整素材重发失败：{exc}")
            module._save(run)
        except Exception:
            pass
        return {"ok": False, "run_id": run_id, "error": str(exc)}


def _package_video(module: Any, run: dict[str, Any]) -> Path:
    videos = list(run.get("videos") or [])
    if not videos:
        raise RuntimeError("任务没有原视频记录")
    for item in videos:
        if not Path(str(item.get("original_path") or "")).is_file():
            raise RuntimeError(f"原视频缺失：{item.get('source_name') or item.get('name') or '未知视频'}")
        if not _has_files(Path(str(item.get("frame_dir") or ""))):
            raise RuntimeError(f"抽帧结果缺失：{item.get('source_name') or item.get('name') or '未知视频'}")
        if not _has_files(Path(str(item.get("matte_dir") or ""))):
            raise RuntimeError(f"抠图结果缺失：{item.get('source_name') or item.get('name') or '未知视频'}")
    has_postprocessed = all(_has_files(Path(str(item.get("smooth_dir") or ""))) for item in videos)
    package = module._make_zip(run) if has_postprocessed else module._make_matte_only_zip(run)
    run["zip_path"] = str(package)
    module._save(run)
    return package


def _package_image(module: Any, run: dict[str, Any]) -> Path:
    items = sorted(run.get("images") or [], key=lambda item: int(item.get("index") or 0))
    if not items:
        raise RuntimeError("任务没有原始图片或序列帧记录")
    matte_files: list[tuple[dict[str, Any], Path]] = []
    has_postprocessed = True
    for item in items:
        original = Path(str(item.get("original_path") or ""))
        matte = module._latest_image(Path(str(item.get("matte_dir") or "")))
        smooth = module._latest_image(Path(str(item.get("smooth_dir") or "")))
        if not original.is_file():
            raise RuntimeError(f"原始序列帧缺失：{item.get('source_name') or item.get('name') or '未知图片'}")
        if not matte:
            raise RuntimeError(f"抠图结果缺失：{item.get('source_name') or item.get('name') or '未知图片'}")
        matte_files.append((item, matte))
        has_postprocessed = has_postprocessed and bool(smooth)
    if has_postprocessed:
        module._prepare_result_files(run)
        package = module._make_sequence_zip(run, package_name=_complete_name(run))
    else:
        package = module._make_matte_only_zip(run, matte_files)
    run["sequence_zip_path"] = str(package)
    module._save(run)
    return package


def _complete_name(run: dict[str, Any]) -> str:
    label = str(run.get("run_label") or run.get("id") or "任务").strip()
    safe = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in label).strip(" ._") or str(run["id"])
    return f"{safe}_完整素材重发.zip"


def _update(kind: Kind, run_id: str, request_id: str, status: str, **fields: Any) -> dict[str, Any]:
    module = _module(kind)
    run = module._load(run_id)
    if not run:
        raise RuntimeError(f"direct {kind} run not found")
    redelivery = dict(run.get("redelivery") or {})
    if str(redelivery.get("request_id") or "") != request_id:
        raise RuntimeError("重发任务已被更新的请求替代")
    redelivery.update({"status": status, "updated_at": _now(), "error": "" if status != "FAILED" else redelivery.get("error", ""), **fields})
    run["redelivery"] = redelivery
    module._save(run)
    return run


def _module(kind: Kind) -> Any:
    if kind == "video":
        from assetclaw_matting.skills import direct_video_skills

        return direct_video_skills
    if kind == "image":
        from assetclaw_matting.skills import direct_image_skills

        return direct_image_skills
    raise ValueError(f"unsupported redelivery kind: {kind}")


def _has_files(path: Path) -> bool:
    return path.is_dir() and any(item.is_file() for item in path.rglob("*"))


def _pid_alive(value: Any) -> bool:
    try:
        pid = int(value or 0)
        if pid <= 0:
            return False
        os.kill(pid, 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
