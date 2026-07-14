from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

from assetclaw_matting.config import settings
from assetclaw_matting.runtime_context import get_runtime_context
from assetclaw_matting.skills import matting_pipeline_skills
from assetclaw_matting.skills.media_skills import IMAGE_EXTS
from assetclaw_matting.skills.security import validate_path


RUNS_ROOT = Path(settings.storage_dir) / "direct_image_runs"
FINISHED = {"DONE", "FAILED", "CANCELED", "DONE_WITH_ERRORS"}
_WORKERS: set[str] = set()


def start(
    image_paths: list[str],
    source_names: list[str] | None = None,
    workflow_path: str | None = None,
    notify_interval_seconds: int = 60,
    run_label: str = "",
    **_: Any,
) -> dict[str, Any]:
    if not image_paths:
        raise ValueError("image_paths is required")
    images = [_validate_image(path) for path in image_paths]
    pipeline_notice = ""
    if not workflow_path and Path(settings.comfyui_workflow_path).name == settings.matting_pipeline_workflow_name:
        pipeline = matting_pipeline_skills.ensure_latest_for_task()
        if not pipeline.get("ok"):
            raise RuntimeError(str(pipeline.get("error") or "matting pipeline preflight failed"))
        workflow_path = str(pipeline.get("workflow_path") or "")
        pipeline_notice = str(pipeline.get("message") or "")
    names = list(source_names or [])
    run_id = "IMG_" + uuid.uuid4().hex[:12].upper()
    run_dir = RUNS_ROOT / run_id
    originals_dir = run_dir / "original_images"
    matte_dir = run_dir / "matte"
    smooth_dir = run_dir / "smooth"
    originals_dir.mkdir(parents=True, exist_ok=True)
    matte_dir.mkdir(parents=True, exist_ok=True)
    smooth_dir.mkdir(parents=True, exist_ok=True)

    items = []
    for index, image in enumerate(images, start=1):
        name = _safe_name(names[index - 1] if index - 1 < len(names) else image.name)
        suffix = image.suffix if image.suffix.lower() in IMAGE_EXTS else ".png"
        image_dir = originals_dir / f"image_{index:02d}"
        image_dir.mkdir(parents=True, exist_ok=True)
        target = image_dir / f"{index:02d}_{Path(name).stem}{suffix}"
        shutil.copy2(image, target)
        width, height = _image_size(target)
        aspect = "square" if width and height and width == height else "portrait"
        items.append(
            {
                "index": index,
                "source_path": str(image),
                "original_path": str(target),
                "name": target.name,
                "matte_dir": str(matte_dir / f"image_{index:02d}"),
                "smooth_dir": str(smooth_dir / f"image_{index:02d}"),
                "width": width,
                "height": height,
                "aspect": aspect,
                "cherry_profile": "half" if aspect == "square" else "full",
                "cherry_output_size": _cherry_output_size("half" if aspect == "square" else "full"),
                "result_path": "",
            }
        )

    ctx = get_runtime_context()
    run = {
        "id": run_id,
        "status": "RUNNING",
        "stage": "queued",
        "created_at": _now(),
        "updated_at": _now(),
        "run_label": run_label or f"{len(items)} 张图片",
        "chat_id": (ctx.get("chat_id") or "") if ctx.get("channel") == "feishu" else "",
        "conversation_id": ctx.get("conversation_id") or "",
        "images": items,
        "children": {},
        "workflow_path": workflow_path or "",
        "pipeline_notice": pipeline_notice,
        "notify_interval_seconds": max(30, min(int(notify_interval_seconds or 60), 3600)),
        "sent_files": [],
        "error": "",
        "log": [],
    }
    _save(run)
    _start_worker(run_id)
    return {"ok": True, "run_id": run_id, **_public(run)}


def status(run_id: str | None = None, **_: Any) -> dict[str, Any]:
    run = _load(run_id)
    if not run:
        return {"ok": False, "error": "direct image run not found"}
    return {"ok": True, "run_id": run["id"], **_public(run)}


def list_runs(limit: int = 10, include_finished: bool = True, **_: Any) -> dict[str, Any]:
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    items = []
    for path in sorted(RUNS_ROOT.glob("IMG_*/status.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        run = json.loads(path.read_text(encoding="utf-8"))
        if run.get("status") in FINISHED and not include_finished:
            continue
        items.append({"run_id": run["id"], **_public(run)})
        if len(items) >= max(1, min(int(limit), 50)):
            break
    return {"ok": True, "count": len(items), "items": items}


def cancel(run_id: str | None = None, **_: Any) -> dict[str, Any]:
    run = _load(run_id)
    if not run:
        return {"ok": False, "error": "direct image run not found"}
    cancel_results = _cancel_child_runs(run)
    run["status"] = "CANCELED"
    run["stage"] = "canceled"
    run.setdefault("children", {})["cancel_results"] = cancel_results
    run["updated_at"] = _now()
    _append_log(run, "用户请求取消任务。")
    if cancel_results:
        _append_log(run, "已同步取消子任务：" + "，".join(_child_cancel_label(item) for item in cancel_results))
    _save(run)
    return {"ok": True, "run_id": run["id"], "status": "CANCELED", "cancel_results": cancel_results, **_public(run)}


def _worker(run_id: str) -> None:
    run = _load(run_id)
    if not run:
        return
    try:
        _mark(run, "RUNNING", "matting")
        _run_comfyui(run)
        if _is_canceled(run):
            return

        _mark(run, "RUNNING", "postprocess")
        _run_cherry(run)
        if _is_canceled(run):
            return

        _mark(run, "RUNNING", "send")
        sent = _send_results(run)
        run["sent_files"] = sent
        run["status"] = "DONE"
        run["stage"] = "done"
        run["error"] = ""
        run["updated_at"] = _now()
        _append_log(run, f"结果文件发送完成：{len(sent)} 个")
        _save(run)
        plan = _cherry_plan_summary(run.get("images") or [])
        suffix = f"，{plan}" if plan else ""
        _notify(run, f"图片完成：{run['id']}，已发回 {len(sent)} 个附件{suffix}。")
    except Exception as exc:
        run = _load(run_id) or run
        if run.get("status") != "CANCELED":
            run["status"] = "FAILED"
            run["error"] = str(exc)
            run["updated_at"] = _now()
            _append_log(run, f"任务失败：{exc}")
            _save(run)
            _notify(run, f"图片处理任务失败：{run_id}\n{exc}")
    finally:
        _WORKERS.discard(run_id)


def _run_comfyui(run: dict[str, Any]) -> None:
    from assetclaw_matting.skills.comfyui_skills import run_start, run_status

    run_dir = _run_dir(run)
    result = run_start(
        workflow_path=run.get("workflow_path") or None,
        input_dir=str(run_dir / "original_images"),
        output_dir=str(run_dir / "matte"),
        recursive=True,
        preserve_structure=True,
        skip_existing=False,
        notify_interval_seconds=run["notify_interval_seconds"],
    )
    child_id = result["run_id"]
    run.setdefault("children", {})["comfyui_run_id"] = child_id
    _append_log(run, f"ComfyUI 抠图任务已启动：{child_id}")
    _save(run)
    while True:
        if _is_canceled(run):
            return
        payload = run_status(child_id, include_gpu=False)
        run["children"]["comfyui"] = payload
        _save(run)
        if payload.get("status") in {"DONE", "FAILED", "CANCELED", "DONE_WITH_ERRORS"}:
            if payload.get("status") in {"FAILED", "CANCELED"}:
                raise RuntimeError(_format_child_failure("ComfyUI", child_id, payload))
            return
        time.sleep(5)


def _run_cherry(run: dict[str, Any]) -> None:
    from assetclaw_matting.skills.cherry_skills import run_start, run_status

    run.setdefault("children", {})["cherry_run_ids"] = []
    for item in run["images"]:
        matte_dir = Path(str(item["matte_dir"]))
        smooth_dir = Path(str(item["smooth_dir"]))
        if not _wait_for_images(matte_dir):
            raise RuntimeError(f"matte_dir has no images: {matte_dir}")
        result = run_start(
            input_dir=str(matte_dir),
            output_dir=str(smooth_dir),
            recursive=True,
            skip_existing=False,
            notify_interval_seconds=run["notify_interval_seconds"],
            profile=str(item.get("cherry_profile") or "auto"),
        )
        options = result.get("options") if isinstance(result.get("options"), dict) else {}
        if options.get("resize_width") and options.get("resize_height"):
            item["cherry_output_size"] = f"{options.get('resize_width')}x{options.get('resize_height')}"
        child_id = result["run_id"]
        run["children"]["cherry_run_id"] = child_id
        run["children"].setdefault("cherry_run_ids", []).append(child_id)
        _append_log(run, f"Cherry 后处理任务已启动：{child_id}，image={item['index']}，profile={item.get('cherry_profile')}")
        _save(run)
        while True:
            if _is_canceled(run):
                return
            payload = run_status(child_id, include_gpu=False)
            run["children"]["cherry"] = payload
            run["children"].setdefault("cherry_runs", {})[child_id] = payload
            _save(run)
            if payload.get("status") in {"DONE", "FAILED", "CANCELED", "DONE_WITH_ERRORS"}:
                if payload.get("status") in {"FAILED", "CANCELED"}:
                    raise RuntimeError(_format_child_failure("Cherry", child_id, payload))
                break
            time.sleep(5)


def _send_results(run: dict[str, Any]) -> list[str]:
    chat_id = str(run.get("chat_id") or "")
    if not chat_id:
        return []
    from assetclaw_matting.feishu.client import feishu_client

    sent: list[str] = []
    for item in run.get("images") or []:
        smooth_dir = Path(str(item["smooth_dir"]))
        result = _latest_image(smooth_dir)
        if not result:
            raise RuntimeError(f"smooth_dir has no result image: {smooth_dir}")
        send_name = f"{Path(str(item.get('name') or result.name)).stem}_processed{result.suffix.lower()}"
        feishu_client.send_file_to_chat(chat_id, result, send_name)
        item["result_path"] = str(result)
        sent.append(str(result))
        _append_log(run, f"已发送结果附件：{result.name}")
        _save(run)
    return sent


def resume_from_postprocess(run_id: str | None = None, **_: Any) -> dict[str, Any]:
    run = _load(run_id)
    if not run:
        return {"ok": False, "error": "direct image run not found"}
    run["status"] = "RUNNING"
    run["stage"] = "postprocess"
    run["error"] = ""
    _append_log(run, "从已生成的抠图结果继续 Cherry 后处理。")
    _save(run)
    _run_cherry(run)
    _mark(run, "RUNNING", "send")
    sent = _send_results(run)
    run["sent_files"] = sent
    run["status"] = "DONE"
    run["stage"] = "done"
    run["error"] = ""
    _append_log(run, f"结果文件发送完成：{len(sent)} 个")
    _save(run)
    return {"ok": True, "run_id": run["id"], **_public(run)}


def _format_stage_summary(run: dict[str, Any], title: str) -> str:
    parts = [f"{title}：{run['id']}"]
    details = [f"图片 {len(run.get('images') or [])}"]
    children = run.get("children") or {}
    comfy = children.get("comfyui") if isinstance(children.get("comfyui"), dict) else {}
    if comfy:
        details.append(f"抠图 {comfy.get('completed', 0)}/{comfy.get('total', 0)}")
    cherry = children.get("cherry") if isinstance(children.get("cherry"), dict) else {}
    if cherry:
        details.append(f"后处理 {cherry.get('completed', 0)}/{cherry.get('total', 0)}")
    cherry_plan = _cherry_plan_summary(run.get("images") or [])
    if cherry_plan:
        details.append(cherry_plan)
    if details:
        parts.append("，".join(details))
    return "；".join(parts)


def _format_child_failure(kind: str, run_id: str, payload: dict[str, Any]) -> str:
    detail = [
        f"{kind} run {run_id} ended as {payload.get('status')}",
        f"completed={payload.get('completed', 0)}/{payload.get('total', 0)}",
        f"failed={payload.get('failed', 0)}",
    ]
    if payload.get("last_error"):
        detail.append(f"last_error={payload.get('last_error')}")
    if payload.get("error"):
        detail.append(f"error={payload.get('error')}")
    return "；".join(detail)


def _cancel_child_runs(run: dict[str, Any]) -> list[dict[str, Any]]:
    children = run.get("children") if isinstance(run.get("children"), dict) else {}
    results: list[dict[str, Any]] = []
    comfy_id = str(children.get("comfyui_run_id") or "").strip()
    if comfy_id:
        try:
            from assetclaw_matting.skills.comfyui_skills import run_cancel as cancel_comfyui

            result = cancel_comfyui(comfy_id, interrupt_current=True, notify=False)
            results.append({"kind": "ComfyUI", "run_id": comfy_id, "ok": bool(result.get("ok")), "status": result.get("status"), "error": result.get("error") or result.get("queue_error") or ""})
        except Exception as exc:
            results.append({"kind": "ComfyUI", "run_id": comfy_id, "ok": False, "error": str(exc)})
    cherry_ids = list(dict.fromkeys(str(item).strip() for item in (children.get("cherry_run_ids") or []) if str(item).strip()))
    cherry_id = str(children.get("cherry_run_id") or "").strip()
    if cherry_id and cherry_id not in cherry_ids:
        cherry_ids.append(cherry_id)
    for child_id in cherry_ids:
        try:
            from assetclaw_matting.skills.cherry_skills import run_cancel as cancel_cherry

            result = cancel_cherry(child_id, notify=False)
            results.append({"kind": "Cherry", "run_id": child_id, "ok": bool(result.get("ok")), "status": result.get("status"), "error": result.get("error") or ""})
        except Exception as exc:
            results.append({"kind": "Cherry", "run_id": child_id, "ok": False, "error": str(exc)})
    return results


def _child_cancel_label(item: dict[str, Any]) -> str:
    status = item.get("status") or ("OK" if item.get("ok") else "失败")
    return f"{item.get('kind')} {item.get('run_id')} {status}"


def _notify(run: dict[str, Any], text: str) -> None:
    chat_id = str(run.get("chat_id") or "")
    if not chat_id:
        return
    from assetclaw_matting.services.notification_service import send_text

    send_text(chat_id, text)


def _start_worker(run_id: str) -> None:
    if run_id in _WORKERS:
        return
    _WORKERS.add(run_id)
    threading.Thread(target=_worker, args=(run_id,), name=f"direct_image_{run_id}", daemon=True).start()


def _is_canceled(run: dict[str, Any]) -> bool:
    latest = _load(run["id"])
    return bool(latest and latest.get("status") == "CANCELED")


def _mark(run: dict[str, Any], status_text: str, stage: str) -> None:
    run["status"] = status_text
    run["stage"] = stage
    run["updated_at"] = _now()
    _append_log(run, f"进入阶段：{stage}")
    _save(run)


def _append_log(run: dict[str, Any], message: str) -> None:
    logs = run.setdefault("log", [])
    logs.append({"ts": _now(), "message": str(message)})
    run["log"] = logs[-120:]
    run["updated_at"] = _now()


def _public(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": run.get("status"),
        "stage": run.get("stage"),
        "run_label": run.get("run_label"),
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
        "images": run.get("images") or [],
        "children": run.get("children") or {},
        "sent_files": run.get("sent_files") or [],
        "pipeline_notice": run.get("pipeline_notice") or "",
        "error": run.get("error") or "",
        "last_log": (run.get("log") or [{}])[-1].get("message", ""),
        "run_dir": str(_run_dir(run)),
    }


def _brief_pipeline_notice(text: str) -> str:
    if "已自动更新" in (text or ""):
        return "管线已自动更新"
    if "最新" in (text or ""):
        return "管线已确认最新"
    return "管线已确认"


def _cherry_output_size(profile: str) -> str:
    return "256x256" if str(profile or "").lower() in {"half", "emoji", "square"} else "384x512"


def _cherry_plan_summary(items: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for item in items:
        profile = str(item.get("cherry_profile") or "")
        if not profile:
            continue
        aspect = "正方形" if str(item.get("aspect") or "").lower() == "square" or profile == "half" else "长方形"
        size = str(item.get("cherry_output_size") or _cherry_output_size(profile))
        key = f"{aspect} {size}"
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return ""
    return "后处理 " + "，".join(f"{key}×{count}" for key, count in counts.items())


def _load(run_id: str | None = None) -> dict[str, Any] | None:
    if run_id:
        path = RUNS_ROOT / run_id / "status.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        matched = _find_run_by_text(run_id)
        if matched:
            return matched
        return None
    if not RUNS_ROOT.exists():
        return None
    paths = sorted(RUNS_ROOT.glob("IMG_*/status.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not paths:
        return None
    active = []
    for path in paths:
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if run.get("status") not in FINISHED:
            active.append(run)
    return active[0] if active else json.loads(paths[0].read_text(encoding="utf-8"))


def _find_run_by_text(value: str) -> dict[str, Any] | None:
    query = str(value or "").strip().lower()
    if not query or not RUNS_ROOT.exists():
        return None
    paths = sorted(RUNS_ROOT.glob("IMG_*/status.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    fallback: dict[str, Any] | None = None
    for path in paths:
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        haystack = " ".join(
            [str(run.get("id") or ""), str(run.get("run_label") or "")]
            + [str(item.get("name") or item.get("source_path") or item.get("original_path") or "") for item in run.get("images") or []]
        ).lower()
        if query not in haystack:
            continue
        if run.get("status") not in FINISHED:
            return run
        fallback = fallback or run
    return fallback


def _save(run: dict[str, Any]) -> None:
    path = _run_dir(run) / "status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")


def _run_dir(run: dict[str, Any]) -> Path:
    return RUNS_ROOT / str(run["id"])


def _validate_image(path: str) -> Path:
    target = validate_path(path, must_exist=True)
    if not target.is_file():
        raise ValueError(f"image path must be a file: {target}")
    if target.suffix.lower() not in IMAGE_EXTS:
        raise ValueError(f"unsupported image extension: {target.suffix}")
    return target


def _safe_name(value: str) -> str:
    text = str(value or "").replace("\\", "/").split("/")[-1].strip()
    cleaned = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in text).strip(" .")
    return cleaned or "image.png"


def _image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as img:
        return int(img.width), int(img.height)


def _latest_image(folder: Path) -> Path | None:
    images = [path for path in folder.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_EXTS]
    if not images:
        return None
    return max(images, key=lambda path: path.stat().st_mtime)


def _wait_for_images(folder: Path, timeout_seconds: float = 10.0) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() <= deadline:
        if any(path.is_file() and path.suffix.lower() in IMAGE_EXTS for path in folder.rglob("*")):
            return True
        time.sleep(0.5)
    return False


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
