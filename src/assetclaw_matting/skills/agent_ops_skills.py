from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any


ACTIVE_STATUSES = {"RUNNING", "PAUSED", "QUEUED", "PENDING"}
DONE_STATUSES = {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED"}


def current_work(
    root: str | None = None,
    include_gpu: bool = True,
    date_start: str | None = None,
    date_end: str | None = None,
    query: str | None = None,
    detail: bool = False,
    media_type: str | None = None,
    include_finished: bool = True,
) -> dict[str, Any]:
    """Summarize the machine's current production context in one readonly call."""
    video_runs = _direct_video_runs(limit=40)
    image_runs = _direct_image_runs(limit=40)
    video_runs = _filter_media_runs(video_runs, date_start=date_start, date_end=date_end, query=query, include_finished=include_finished)
    image_runs = _filter_media_runs(image_runs, date_start=date_start, date_end=date_end, query=query, include_finished=include_finished)
    media = str(media_type or "").lower()
    if media in {"video", "videos", "动画", "视频"}:
        image_runs = []
    elif media in {"image", "images", "图片", "图"}:
        video_runs = []
    from assetclaw_matting.config import settings

    payload = {
        "ok": True,
        "root": root or str(Path(settings.animation_root) / datetime.now().strftime("%Y-%m-%d")),
        "filters": {
            "date_start": date_start or "",
            "date_end": date_end or "",
            "query": query or "",
            "detail": bool(detail),
            "media_type": media_type or "",
            "include_finished": bool(include_finished),
        },
        "direct_videos": video_runs,
        "direct_images": image_runs,
        "comfyui": _latest_comfyui(),
        "cherry": _latest_cherry(),
        "frame": _latest_frame(),
        "pipeline": _latest_pipeline(),
        "pending_confirmations": _pending_confirmations(),
        "recent_errors": _recent_errors(),
    }
    payload["active"] = [
        item
        for item in (payload["comfyui"], payload["cherry"], payload["frame"], payload["pipeline"])
        if item and item.get("status") not in DONE_STATUSES
    ]
    if include_gpu:
        payload["gpu"] = _safe_call("assetclaw_matting.skills.status_skills", "gpu_status")
    return payload


def diagnose(root: str | None = None, include_gpu: bool = True) -> dict[str, Any]:
    """Diagnose likely reasons for confusing task states and suggest the next skill."""
    snapshot = current_work(root=root, include_gpu=include_gpu)
    findings: list[dict[str, Any]] = []
    next_actions: list[dict[str, Any]] = []

    comfy = snapshot.get("comfyui") or {}
    if comfy:
        queue = _safe_call("assetclaw_matting.skills.comfyui_skills", "queue_status")
        comfy["queue"] = queue
        if comfy.get("status") == "RUNNING":
            queue_count = len(queue.get("running") or []) + len(queue.get("pending") or []) if queue.get("ok") else None
            if queue_count == 0 and _age_seconds(comfy.get("updated_at")) >= 60:
                findings.append(
                    {
                        "level": "warning",
                        "topic": "comfyui_worker_stalled",
                        "message": "ComfyUI run is marked RUNNING, but the native queue is empty and the DB has not updated recently.",
                        "run_id": comfy.get("run_id"),
                    }
                )
                next_actions.append({"skill": "comfyui.run_resume", "arguments": {"run_id": comfy.get("run_id")}})
            elif queue_count and queue_count > 0:
                findings.append(
                    {
                        "level": "ok",
                        "topic": "comfyui_queue_active",
                        "message": "ComfyUI native queue has active or pending work.",
                        "run_id": comfy.get("run_id"),
                    }
                )
            else:
                findings.append(
                    {
                        "level": "info",
                        "topic": "comfyui_recently_updated",
                        "message": "ComfyUI run was updated recently; avoid duplicate resume unless it stays unchanged.",
                        "run_id": comfy.get("run_id"),
                    }
                )
        elif comfy.get("status") == "PAUSED":
            findings.append(
                {
                    "level": "warning",
                    "topic": "comfyui_paused",
                    "message": "ComfyUI run is paused.",
                    "run_id": comfy.get("run_id"),
                }
            )
            next_actions.append({"skill": "comfyui.run_resume", "arguments": {"run_id": comfy.get("run_id")}})
        elif comfy.get("status") in {"FAILED", "DONE_WITH_ERRORS"}:
            findings.append(
                {
                    "level": "error",
                    "topic": "comfyui_failed",
                    "message": "Latest ComfyUI run has failures.",
                    "run_id": comfy.get("run_id"),
                }
            )

    cherry = snapshot.get("cherry") or {}
    if cherry:
        if cherry.get("status") == "RUNNING" and _age_seconds(cherry.get("updated_at")) >= 120:
            findings.append(
                {
                    "level": "info",
                    "topic": "cherry_running",
                    "message": "Cherry is marked RUNNING; check progress before starting another smoothing job.",
                    "run_id": cherry.get("run_id"),
                }
            )
            next_actions.append({"skill": "cherry.run_status", "arguments": {"run_id": cherry.get("run_id")}})
        elif cherry.get("status") in {"FAILED", "DONE_WITH_ERRORS"}:
            findings.append(
                {
                    "level": "error",
                    "topic": "cherry_failed",
                    "message": "Latest Cherry run has failures.",
                    "run_id": cherry.get("run_id"),
                }
            )

    confirmations = snapshot.get("pending_confirmations") or []
    if confirmations:
        findings.append(
            {
                "level": "info",
                "topic": "pending_confirmation",
                "message": f"There are {len(confirmations)} pending confirmations; the user may need to confirm or cancel one.",
            }
        )

    errors = snapshot.get("recent_errors") or []
    if errors:
        latest = errors[0]
        findings.append(
            {
                "level": "info",
                "topic": "recent_error",
                "message": f"Recent skill error: {latest.get('skill')} - {_short_text(latest.get('error'), 160)}",
            }
        )

    if not findings:
        findings.append({"level": "ok", "topic": "no_obvious_blocker", "message": "No obvious active-task blocker found."})

    snapshot["findings"] = findings
    snapshot["next_actions"] = _dedupe_actions(next_actions)
    return snapshot


def _latest_comfyui() -> dict[str, Any]:
    row = _latest_row(
        "comfyui_runs",
        "id, status, workflow_path, input_dir, output_dir, total, options_json, created_at, updated_at",
    )
    if not row:
        return {}
    options = _loads(row["options_json"])
    prompt_map = options.get("prompt_map") or []
    completed = sum(1 for item in prompt_map if not item.get("error") and Path(str(item.get("dst_path") or "")).exists())
    failed = sum(1 for item in prompt_map if item.get("error"))
    return {
        "run_id": row["id"],
        "status": row["status"],
        "workflow_name": Path(row["workflow_path"]).name,
        "workflow_path": row["workflow_path"],
        "input_dir": row["input_dir"],
        "output_dir": row["output_dir"],
        "total": int(row["total"] or 0),
        "completed": completed,
        "failed": failed,
        "updated_at": row["updated_at"],
        "created_at": row["created_at"],
    }


def _latest_cherry() -> dict[str, Any]:
    row = _latest_row(
        "cherry_runs",
        "id, status, input_dir, output_dir, total, completed, failed, error, created_at, updated_at",
    )
    if not row:
        return {}
    return {
        "run_id": row["id"],
        "status": row["status"],
        "input_dir": row["input_dir"],
        "output_dir": row["output_dir"],
        "total": int(row["total"] or 0),
        "completed": int(row["completed"] or 0),
        "failed": int(row["failed"] or 0),
        "error": row["error"] or "",
        "updated_at": row["updated_at"],
        "created_at": row["created_at"],
    }


def _latest_frame() -> dict[str, Any]:
    row = _latest_row(
        "frame_runs",
        "id, status, download_dir, export_dir, total_records, processed_records, error, created_at, updated_at",
    )
    if not row:
        return {}
    return {
        "run_id": row["id"],
        "status": row["status"],
        "download_dir": row["download_dir"],
        "export_dir": row["export_dir"],
        "total_records": int(row["total_records"] or 0),
        "processed_records": int(row["processed_records"] or 0),
        "error": row["error"] or "",
        "updated_at": row["updated_at"],
        "created_at": row["created_at"],
    }


def _latest_pipeline() -> dict[str, Any]:
    row = _latest_row(
        "pipeline_runs",
        "id, status, current_step, input_dir, frame_output_dir, matte_output_dir, smooth_output_dir, frame_run_id, comfyui_run_id, cherry_run_id, error, created_at, updated_at",
    )
    if not row:
        return {}
    return {
        "run_id": row["id"],
        "status": row["status"],
        "current_step": row["current_step"],
        "input_dir": row["input_dir"],
        "frame_output_dir": row["frame_output_dir"],
        "matte_output_dir": row["matte_output_dir"],
        "smooth_output_dir": row["smooth_output_dir"],
        "frame_run_id": row["frame_run_id"],
        "comfyui_run_id": row["comfyui_run_id"],
        "cherry_run_id": row["cherry_run_id"],
        "error": row["error"] or "",
        "updated_at": row["updated_at"],
        "created_at": row["created_at"],
    }


def _direct_video_runs(limit: int = 8) -> list[dict[str, Any]]:
    from assetclaw_matting.config import settings

    root = Path(settings.storage_dir) / "direct_video_runs"
    items = []
    count_cache: dict[str, int] = {}
    for path in _status_files(root, "VID_*", limit=limit * 2):
        run = _load_status_file(path)
        if not run:
            continue
        videos = [_video_item_summary(run, item, count_cache) for item in run.get("videos") or []]
        items.append(
            {
                "kind": "video",
                "run_id": run.get("id"),
                "status": run.get("status"),
                "stage": run.get("stage"),
                "run_label": run.get("run_label"),
                "created_at": run.get("created_at"),
                "updated_at": run.get("updated_at"),
                "items": videos,
            }
        )
        if len(items) >= limit:
            break
    return items


def _direct_image_runs(limit: int = 8) -> list[dict[str, Any]]:
    from assetclaw_matting.config import settings

    root = Path(settings.storage_dir) / "direct_image_runs"
    items = []
    count_cache: dict[str, int] = {}
    for path in _status_files(root, "IMG_*", limit=limit * 2):
        run = _load_status_file(path)
        if not run:
            continue
        images = [_image_item_summary(run, item, count_cache) for item in run.get("images") or []]
        items.append(
            {
                "kind": "image",
                "run_id": run.get("id"),
                "status": run.get("status"),
                "stage": run.get("stage"),
                "run_label": run.get("run_label"),
                "created_at": run.get("created_at"),
                "updated_at": run.get("updated_at"),
                "items": images,
            }
        )
        if len(items) >= limit:
            break
    return items


def _status_files(root: Path, pattern: str, limit: int) -> list[Path]:
    if not root.exists():
        return []
    return sorted(root.glob(f"{pattern}/status.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:limit]


def _filter_media_runs(
    runs: list[dict[str, Any]],
    date_start: str | None,
    date_end: str | None,
    query: str | None,
    include_finished: bool,
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    query_text = str(query or "").strip().lower()
    for run in runs:
        if not include_finished and str(run.get("status") or "") in DONE_STATUSES:
            continue
        if not _run_in_date_range(run, date_start, date_end):
            continue
        items = run.get("items") or []
        if query_text:
            if _matches_run(run, query_text):
                matched_items = items
            else:
                matched_items = [item for item in items if _matches_item(item, query_text)]
            if not matched_items:
                continue
            run = dict(run)
            run["items"] = matched_items
        filtered.append(run)
    return filtered


def _run_in_date_range(run: dict[str, Any], date_start: str | None, date_end: str | None) -> bool:
    if not date_start and not date_end:
        return True
    dates = [_date_text(run.get("created_at")), _date_text(run.get("updated_at"))]
    dates.extend(_date_text(item.get("updated_at")) for item in run.get("items") or [])
    dates = [item for item in dates if item]
    if not dates:
        return False
    start = str(date_start or "0000-00-00")
    end = str(date_end or "9999-99-99")
    return any(start <= item <= end for item in dates)


def _date_text(value: Any) -> str:
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else ""


def _matches_run(run: dict[str, Any], query: str) -> bool:
    haystack = " ".join(str(run.get(key) or "") for key in ("run_id", "run_label", "status", "stage")).lower()
    return query in haystack


def _matches_item(item: dict[str, Any], query: str) -> bool:
    haystack = " ".join(str(item.get(key) or "") for key in ("name", "run_id", "run_label", "status", "output_size", "comfyui_run_id", "cherry_run_id")).lower()
    return query in haystack


def _load_status_file(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _video_item_summary(run: dict[str, Any], item: dict[str, Any], count_cache: dict[str, int] | None = None) -> dict[str, Any]:
    frame_total = int(item.get("frame_count") or _count_pngs(Path(str(item.get("frame_dir") or "")), count_cache))
    matte_done = _count_pngs(Path(str(item.get("matte_dir") or "")), count_cache)
    smooth_done = _count_pngs(Path(str(item.get("smooth_dir") or "")), count_cache)
    children = run.get("children") if isinstance(run.get("children"), dict) else {}
    return {
        "run_id": run.get("id"),
        "run_status": run.get("status"),
        "run_stage": run.get("stage"),
        "run_label": run.get("run_label"),
        "updated_at": run.get("updated_at"),
        "name": _display_name(item),
        "status": _media_item_status(str(run.get("status") or ""), str(run.get("stage") or ""), frame_total, matte_done, smooth_done),
        "frame_done": frame_total,
        "matte_done": matte_done,
        "smooth_done": smooth_done,
        "total": frame_total,
        "aspect": item.get("aspect") or "",
        "output_size": item.get("cherry_output_size") or "",
        "comfyui_run_id": children.get("comfyui_run_id") or "",
        "cherry_run_id": children.get("cherry_run_id") or "",
        "last_log": (run.get("log") or [{}])[-1].get("message", ""),
    }


def _image_item_summary(run: dict[str, Any], item: dict[str, Any], count_cache: dict[str, int] | None = None) -> dict[str, Any]:
    matte_done = _count_pngs(Path(str(item.get("matte_dir") or "")), count_cache)
    smooth_done = _count_pngs(Path(str(item.get("smooth_dir") or "")), count_cache)
    total = 1
    children = run.get("children") if isinstance(run.get("children"), dict) else {}
    return {
        "run_id": run.get("id"),
        "run_status": run.get("status"),
        "run_stage": run.get("stage"),
        "run_label": run.get("run_label"),
        "updated_at": run.get("updated_at"),
        "name": _display_name(item),
        "status": _media_item_status(str(run.get("status") or ""), str(run.get("stage") or ""), total, matte_done, smooth_done),
        "frame_done": total,
        "matte_done": matte_done,
        "smooth_done": smooth_done,
        "total": total,
        "aspect": item.get("aspect") or "",
        "output_size": item.get("cherry_output_size") or "",
        "comfyui_run_id": children.get("comfyui_run_id") or "",
        "cherry_run_id": children.get("cherry_run_id") or "",
        "last_log": (run.get("log") or [{}])[-1].get("message", ""),
    }


def _display_name(item: dict[str, Any]) -> str:
    for key in ("name", "source_name"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    for key in ("source_path", "original_path"):
        value = str(item.get(key) or "").strip()
        if value:
            return Path(value).name
    return "未命名素材"


def _media_item_status(run_status: str, stage: str, total: int, matte_done: int, smooth_done: int) -> str:
    if run_status == "DONE":
        return "完成"
    if run_status in {"FAILED", "CANCELED", "DONE_WITH_ERRORS"}:
        return {"FAILED": "失败", "CANCELED": "已取消", "DONE_WITH_ERRORS": "部分完成"}.get(run_status, run_status)
    if stage in {"queued", "pending"}:
        return "排队中"
    if stage == "extract_frames":
        return "抽帧中" if total else "等待抽帧"
    if stage == "matting":
        if total and matte_done >= total:
            return "等待后处理"
        if matte_done == 0:
            return "排队抠图" if total else "等待抠图"
        return "抠图中"
    if stage == "postprocess":
        if total and smooth_done >= total:
            return "等待打包"
        if smooth_done == 0:
            return "排队后处理"
        return "后处理中"
    if stage == "zip":
        return "打包中"
    return stage or run_status or "处理中"


def _count_pngs(path: Path, cache: dict[str, int] | None = None) -> int:
    key = str(path)
    if cache is not None and key in cache:
        return cache[key]
    if not path.exists():
        if cache is not None:
            cache[key] = 0
        return 0
    try:
        count = sum(1 for item in path.rglob("*.png") if item.is_file())
    except OSError:
        count = 0
    if cache is not None:
        cache[key] = count
    return count


def _pending_confirmations(limit: int = 5) -> list[dict[str, Any]]:
    from assetclaw_matting.db.sqlite import get_connection

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, skill, arguments_json, status, created_at, expires_at
            FROM pending_confirmations
            WHERE status = 'PENDING'
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "skill": row["skill"],
            "arguments": _loads(row["arguments_json"]),
            "status": row["status"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
        }
        for row in rows
    ]


def _recent_errors(limit: int = 5) -> list[dict[str, Any]]:
    from assetclaw_matting.db.sqlite import get_connection

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT skill, error, created_at
            FROM skill_calls
            WHERE ok = 0 AND error != ''
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        {"skill": row["skill"], "error": _short_text(row["error"], 220), "created_at": row["created_at"]}
        for row in rows
    ]


def _latest_row(table: str, columns: str) -> Any:
    from assetclaw_matting.db.sqlite import get_connection

    with get_connection() as conn:
        active = conn.execute(
            f"""
            SELECT {columns}
            FROM {table}
            WHERE status NOT IN ('DONE', 'DONE_WITH_ERRORS', 'FAILED', 'CANCELED')
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 1
            """
        ).fetchone()
        if active:
            return active
        return conn.execute(
            f"""
            SELECT {columns}
            FROM {table}
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 1
            """
        ).fetchone()


def _safe_call(module_name: str, fn_name: str) -> dict[str, Any]:
    try:
        module = __import__(module_name, fromlist=[fn_name])
        return getattr(module, fn_name)()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _loads(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _age_seconds(raw: str | None) -> float:
    if not raw:
        return 999999.0
    try:
        return max(0.0, time.time() - datetime.fromisoformat(raw).timestamp())
    except Exception:
        return 999999.0


def _dedupe_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for action in actions:
        key = json.dumps(action, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        result.append(action)
    return result


def _short_text(value: Any, limit: int) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."
