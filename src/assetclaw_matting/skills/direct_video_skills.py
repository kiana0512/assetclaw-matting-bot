from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

from assetclaw_matting.config import settings
from assetclaw_matting.runtime_context import get_runtime_context
from assetclaw_matting.skills import matting_pipeline_skills
from assetclaw_matting.skills.media_skills import VIDEO_EXTS
from assetclaw_matting.skills.security import validate_path


RUNS_ROOT = Path(settings.storage_dir) / "direct_video_runs"
FINISHED = {"DONE", "FAILED", "CANCELED", "DONE_WITH_ERRORS"}
_WORKERS: set[str] = set()


def start(
    video_paths: list[str],
    source_names: list[str] | None = None,
    fps: int = 24,
    max_frames: int = 0,
    workflow_path: str | None = None,
    notify_interval_seconds: int = 60,
    run_label: str = "",
    **_: Any,
) -> dict[str, Any]:
    if not video_paths:
        raise ValueError("video_paths is required")
    videos = [_validate_video(path) for path in video_paths]
    pipeline_notice = ""
    if not workflow_path and Path(settings.comfyui_workflow_path).name == settings.matting_pipeline_workflow_name:
        pipeline = matting_pipeline_skills.ensure_latest_for_task()
        if not pipeline.get("ok"):
            raise RuntimeError(str(pipeline.get("error") or "matting pipeline preflight failed"))
        workflow_path = str(pipeline.get("workflow_path") or "")
        pipeline_notice = str(pipeline.get("message") or "")
    names = list(source_names or [])
    run_id = "VID_" + uuid.uuid4().hex[:12].upper()
    run_dir = RUNS_ROOT / run_id
    originals_dir = run_dir / "original_videos"
    frames_dir = run_dir / "frames"
    matte_dir = run_dir / "matte"
    smooth_dir = run_dir / "smooth"
    originals_dir.mkdir(parents=True, exist_ok=True)
    frames_dir.mkdir(parents=True, exist_ok=True)
    matte_dir.mkdir(parents=True, exist_ok=True)
    smooth_dir.mkdir(parents=True, exist_ok=True)

    copied = []
    for index, video in enumerate(videos, start=1):
        name = _safe_name(names[index - 1] if index - 1 < len(names) else video.name)
        suffix = video.suffix if video.suffix.lower() in VIDEO_EXTS else ".mp4"
        target = originals_dir / f"{index:02d}_{Path(name).stem}{suffix}"
        shutil.copy2(video, target)
        copied.append(str(target))

    ctx = get_runtime_context()
    run = {
        "id": run_id,
        "status": "RUNNING",
        "stage": "queued",
        "created_at": _now(),
        "updated_at": _now(),
        "run_label": run_label or f"{len(copied)} 个动画视频",
        "chat_id": (ctx.get("chat_id") or "") if ctx.get("channel") == "feishu" else "",
        "conversation_id": ctx.get("conversation_id") or "",
        "videos": [
            {
                "index": index,
                "source_path": str(videos[index - 1]),
                "original_path": copied[index - 1],
                "name": Path(copied[index - 1]).name,
                "frame_dir": str(frames_dir / f"video_{index:02d}"),
                "matte_dir": str(matte_dir / f"video_{index:02d}"),
                "smooth_dir": str(smooth_dir / f"video_{index:02d}"),
                "frame_count": 0,
                "aspect": "",
                "cherry_profile": "",
                "cherry_output_size": "",
            }
            for index in range(1, len(copied) + 1)
        ],
        "children": {},
        "fps": int(fps),
        "max_frames": int(max_frames or 0),
        "workflow_path": workflow_path or "",
        "pipeline_notice": pipeline_notice,
        "notify_interval_seconds": max(30, min(int(notify_interval_seconds or 60), 3600)),
        "zip_path": "",
        "error": "",
        "log": [],
    }
    _save(run)
    _start_worker(run_id)
    return {"ok": True, "run_id": run_id, **_public(run)}


def status(run_id: str | None = None, **_: Any) -> dict[str, Any]:
    run = _load(run_id)
    if not run:
        return {"ok": False, "error": "direct video run not found"}
    return {"ok": True, "run_id": run["id"], **_public(run)}


def list_runs(limit: int = 10, include_finished: bool = True, **_: Any) -> dict[str, Any]:
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    items = []
    for path in sorted(RUNS_ROOT.glob("VID_*/status.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        run = json.loads(path.read_text(encoding="utf-8"))
        if run.get("status") in FINISHED and not include_finished:
            continue
        items.append({"run_id": run["id"], **_public(run)})
        if len(items) >= max(1, min(int(limit), 50)):
            break
    return {"ok": True, "count": len(items), "items": items}


def resend_zip(run_id: str | None = None, **_: Any) -> dict[str, Any]:
    run = _load(run_id)
    if not run:
        return {"ok": False, "error": "direct video run not found"}
    zip_path_text = str(run.get("zip_path") or "")
    if not zip_path_text:
        return {"ok": False, "error": "zip_path is empty"}
    zip_path = Path(zip_path_text)
    if not zip_path.exists():
        return {"ok": False, "error": f"zip not found: {zip_path}"}
    _send_zip(run, zip_path)
    run["status"] = "DONE"
    run["stage"] = "done"
    run["error"] = ""
    _append_log(run, f"zip 重发完成：{zip_path.name}，{zip_path.stat().st_size} bytes")
    _save(run)
    return {"ok": True, "run_id": run["id"], "zip_path": str(zip_path), "zip_size": zip_path.stat().st_size}


def cancel(run_id: str | None = None, **_: Any) -> dict[str, Any]:
    run = _load(run_id)
    if not run:
        return {"ok": False, "error": "direct video run not found"}
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


def preview_start_confirmation(arguments: dict[str, Any], confirmation_id: str) -> str:
    videos = arguments.get("video_paths") or []
    names = arguments.get("source_names") or []
    count = len(videos)
    lines = [
        f"收到 {count} 个动画视频，可以开始处理。",
    ]
    for index, path in enumerate(videos[:8], start=1):
        display = names[index - 1] if index - 1 < len(names) else Path(str(path)).name
        lines.append(f"{index}. {display}")
    if count > 8:
        lines.append(f"还有 {count - 8} 个视频也会一起处理。")
    lines.extend(
        [
            "流程：抽帧、抠图、后处理，完成后发 zip。",
            "后处理：正方形 256x256，长方形 384x512，自动判断。",
            "回复“确认执行”开始。",
        ]
    )
    return "\n".join(lines)


def _worker(run_id: str) -> None:
    run = _load(run_id)
    if not run:
        return
    try:
        _mark(run, "RUNNING", "extract_frames")
        _extract_all(run)
        if _is_canceled(run):
            return

        _mark(run, "RUNNING", "matting")
        _run_comfyui(run)
        if _is_canceled(run):
            return

        _mark(run, "RUNNING", "postprocess")
        _run_cherry(run)
        if _is_canceled(run):
            return

        _mark(run, "RUNNING", "zip")
        zip_path = _make_zip(run)
        run["zip_path"] = str(zip_path)
        run["status"] = "DONE"
        run["stage"] = "done"
        run["updated_at"] = _now()
        _save(run)
        plan = _cherry_plan_summary(run.get("videos") or [])
        suffix = f"，{plan}" if plan else ""
        _notify(run, f"动画完成：{run['id']}，正在发送 zip{suffix}。")
        _send_zip(run, zip_path)
    except Exception as exc:
        run = _load(run_id) or run
        if run.get("status") != "CANCELED":
            run["status"] = "FAILED"
            run["error"] = str(exc)
            run["updated_at"] = _now()
            _append_log(run, f"任务失败：{exc}")
            _save(run)
            _notify(run, f"动画处理任务失败：{run_id}\n{exc}")
    finally:
        _WORKERS.discard(run_id)


def _extract_all(run: dict[str, Any]) -> None:
    python = Path(settings.comfyui_python_dir) / "python.exe"
    script = Path(settings.assetclaw_root) / "scripts" / "extract_direct_video_frames.py"
    for item in run["videos"]:
        if _is_canceled(run):
            return
        out_dir = Path(item["frame_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        _append_log(run, f"开始抽帧：{item['name']}")
        _save(run)
        proc = subprocess.Popen(
            [str(python), str(script), item["original_path"], str(out_dir), str(run["fps"]), str(run["max_frames"])],
            cwd=str(Path(settings.assetclaw_root)),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert proc.stdout is not None
        last_payload: dict[str, Any] = {}
        for line in proc.stdout:
            _handle_extract_log(run, line.strip())
            if line.strip().startswith("{"):
                try:
                    payload = json.loads(line)
                    if payload.get("ok"):
                        last_payload = payload
                except Exception:
                    pass
        stderr = proc.stderr.read() if proc.stderr else ""
        rc = proc.wait()
        if rc != 0:
            raise RuntimeError(stderr.strip() or f"extract worker exited with {rc}")
        item["frame_count"] = int(last_payload.get("frame_count") or len(list(out_dir.glob("*.png"))))
        width, height = _first_frame_size(out_dir)
        item["aspect"] = "square" if width and height and width == height else "portrait"
        item["cherry_profile"] = "half" if item["aspect"] == "square" else "full"
        item["cherry_output_size"] = _cherry_output_size(str(item["cherry_profile"]))
        _append_log(
            run,
            f"抽帧完成：{item['name']}，{item['frame_count']} 帧，{width}x{height}，{item['aspect']}，后处理 {item['cherry_output_size']}",
        )
        run["updated_at"] = _now()
        _save(run)


def _run_comfyui(run: dict[str, Any]) -> None:
    from assetclaw_matting.skills.comfyui_skills import run_start, run_status

    run_dir = _run_dir(run)
    result = run_start(
        workflow_path=run.get("workflow_path") or None,
        input_dir=str(run_dir / "frames"),
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
                raise RuntimeError(_format_comfyui_failure(child_id, payload))
            return
        time.sleep(5)


def _run_cherry(run: dict[str, Any]) -> None:
    from assetclaw_matting.skills.cherry_skills import run_start, run_status

    run.setdefault("children", {})["cherry_run_ids"] = []
    for item in run["videos"]:
        matte_dir = Path(str(item["matte_dir"]))
        smooth_dir = Path(str(item["smooth_dir"]))
        if not any(matte_dir.rglob("*.png")):
            raise RuntimeError(f"matte_dir has no png images: {matte_dir}")
        profile = str(item.get("cherry_profile") or "auto")
        item["cherry_output_size"] = item.get("cherry_output_size") or _cherry_output_size(profile)
        result = run_start(
            input_dir=str(matte_dir),
            output_dir=str(smooth_dir),
            recursive=True,
            skip_existing=False,
            notify_interval_seconds=run["notify_interval_seconds"],
            profile=profile,
        )
        options = result.get("options") if isinstance(result.get("options"), dict) else {}
        if options.get("resize_width") and options.get("resize_height"):
            item["cherry_output_size"] = f"{options.get('resize_width')}x{options.get('resize_height')}"
        child_id = result["run_id"]
        run["children"]["cherry_run_id"] = child_id
        run["children"].setdefault("cherry_run_ids", []).append(child_id)
        _append_log(run, f"Cherry 后处理任务已启动：{child_id}，video={item['index']}，profile={profile}")
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
                    raise RuntimeError(f"Cherry run {child_id} ended as {payload.get('status')}")
                break
            time.sleep(5)


def _make_zip(run: dict[str, Any]) -> Path:
    run_dir = _run_dir(run)
    manifest = run_dir / "manifest.json"
    manifest.write_text(json.dumps(_public(run), ensure_ascii=False, indent=2), encoding="utf-8")
    zip_path = run_dir / f"{run['id']}_animation_processed.zip"
    if zip_path.exists():
        zip_path.unlink()
    include_dirs = ["original_videos", "frames", "matte", "smooth"]
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(manifest, arcname="manifest.json")
        for folder in include_dirs:
            root = run_dir / folder
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if path.is_file():
                    zf.write(path, arcname=str(path.relative_to(run_dir)).replace("\\", "/"))
    return zip_path


def _send_zip(run: dict[str, Any], zip_path: Path) -> None:
    chat_id = str(run.get("chat_id") or "")
    if not chat_id:
        return
    from assetclaw_matting.feishu.client import feishu_client

    _append_log(run, f"开始发送 zip：{zip_path.name}，{zip_path.stat().st_size} bytes")
    _save(run)
    sent = feishu_client.send_file_to_chat(chat_id, zip_path, zip_path.name) or {}
    if sent:
        run["drive_file"] = sent
        _append_log(run, f"Drive 文件已授权并发送：{sent.get('url') or sent.get('file_token')}")
        _save(run)


def _handle_extract_log(run: dict[str, Any], raw: str) -> None:
    if not raw:
        return
    try:
        payload = json.loads(raw)
        message = str(payload.get("message") or payload)
    except Exception:
        message = raw
    _append_log(run, message)
    _save(run)


def _format_progress(run: dict[str, Any]) -> str:
    children = run.get("children") or {}
    comfy = children.get("comfyui") if isinstance(children.get("comfyui"), dict) else {}
    cherry = children.get("cherry") if isinstance(children.get("cherry"), dict) else {}
    lines = [
        f"动画处理进度：{run['id']}",
        f"状态：{run.get('status')} / {run.get('stage')}",
        f"视频：{len(run.get('videos') or [])} 个",
    ]
    frame_total = sum(int(item.get("frame_count") or 0) for item in run.get("videos") or [])
    if frame_total:
        lines.append(f"已抽帧：{frame_total} 张")
    if comfy:
        lines.append(f"抠图：{comfy.get('completed', 0)}/{comfy.get('total', 0)}，{comfy.get('status')}")
    if cherry:
        lines.append(f"后处理：{cherry.get('completed', 0)}/{cherry.get('total', 0)}，{cherry.get('status')}")
    log = run.get("log") or []
    if log:
        lines.append(f"最近：{log[-1].get('message')}")
    return "\n".join(lines)


def _format_stage_summary(run: dict[str, Any], title: str) -> str:
    parts = [f"{title}：{run['id']}"]
    details = [f"视频 {len(run.get('videos') or [])}"]
    frame_total = sum(int(item.get("frame_count") or 0) for item in run.get("videos") or [])
    if frame_total:
        details.append(f"抽帧 {frame_total}")
    children = run.get("children") or {}
    comfy = children.get("comfyui") if isinstance(children.get("comfyui"), dict) else {}
    if comfy:
        details.append(f"抠图 {comfy.get('completed', 0)}/{comfy.get('total', 0)}")
    cherry = children.get("cherry") if isinstance(children.get("cherry"), dict) else {}
    if cherry:
        details.append(f"后处理 {cherry.get('completed', 0)}/{cherry.get('total', 0)}")
    cherry_plan = _cherry_plan_summary(run.get("videos") or [])
    if cherry_plan:
        details.append(cherry_plan)
    if details:
        parts.append("，".join(details))
    return "；".join(parts)


def _format_comfyui_failure(run_id: str, payload: dict[str, Any]) -> str:
    detail = [
        f"ComfyUI run {run_id} ended as {payload.get('status')}",
        f"completed={payload.get('completed', 0)}/{payload.get('total', 0)}",
        f"failed={payload.get('failed', 0)}",
    ]
    if payload.get("last_completed"):
        detail.append(f"last_completed={payload.get('last_completed')}")
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
    threading.Thread(target=_worker, args=(run_id,), name=f"direct_video_{run_id}", daemon=True).start()


def _is_canceled(run: dict[str, Any]) -> bool:
    latest = _load(run["id"])
    return bool(latest and latest.get("status") == "CANCELED")


def _mark(run: dict[str, Any], status_text: str, stage: str) -> None:
    run["status"] = status_text
    run["stage"] = stage
    run["updated_at"] = _now()
    _append_log(run, f"进入阶段：{stage}")
    _save(run)
    if stage == "canceled":
        _notify(run, f"任务已取消：{run['id']}")


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
        "videos": run.get("videos") or [],
        "children": run.get("children") or {},
        "fps": run.get("fps"),
        "zip_path": run.get("zip_path") or "",
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
    paths = sorted(RUNS_ROOT.glob("VID_*/status.json"), key=lambda item: item.stat().st_mtime, reverse=True)
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
    paths = sorted(RUNS_ROOT.glob("VID_*/status.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    fallback: dict[str, Any] | None = None
    for path in paths:
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        haystack = " ".join(
            [str(run.get("id") or ""), str(run.get("run_label") or "")]
            + [str(item.get("name") or item.get("source_path") or item.get("original_path") or "") for item in run.get("videos") or []]
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


def _validate_video(path: str) -> Path:
    target = validate_path(path, must_exist=True)
    if not target.is_file():
        raise ValueError(f"video path must be a file: {target}")
    if target.suffix.lower() not in VIDEO_EXTS:
        raise ValueError(f"unsupported video extension: {target.suffix}")
    return target


def _safe_name(value: str) -> str:
    text = str(value or "").replace("\\", "/").split("/")[-1].strip()
    cleaned = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in text).strip(" .")
    return cleaned or "video.mp4"


def _first_frame_size(folder: Path) -> tuple[int, int]:
    first = next(iter(sorted(folder.glob("*.png"))), None)
    if not first:
        return 0, 0
    with Image.open(first) as img:
        return int(img.width), int(img.height)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
