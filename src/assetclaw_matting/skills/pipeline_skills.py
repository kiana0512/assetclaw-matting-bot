from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from assetclaw_matting.skills.frame_skills import default_automation_paths
from assetclaw_matting.skills.security import validate_path


_WORKER_RUNS: set[str] = set()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_id() -> str:
    return "PIPE_" + uuid.uuid4().hex[:12].upper()


def run_preview(
    input_dir: str | None = None,
    frame_output_dir: str | None = None,
    matte_output_dir: str | None = None,
    smooth_output_dir: str | None = None,
    workflow_path: str | None = None,
    fps: int = 24,
    max_frames: int = 0,
    diff_threshold: float = 0.2,
    dedup_enabled: bool = False,
    dedup_renumber: bool = False,
    selection_root: str | None = None,
    selection_emotions: list[str] | None = None,
    selection_types: list[str] | None = None,
    progress_include: list[str] | None = None,
    progress_exclude: list[str] | None = None,
    priority_characters: list[str] | None = None,
    use_smooth: bool = False,
    resize_width: int = 384,
    resize_height: int = 512,
    resource_json_enabled: bool = True,
    resource_json_output_path: str | None = None,
    resource_json_types: list[str] | None = None,
    run_cherry: bool = True,
    fake_matting_from_frames: bool = False,
) -> dict[str, Any]:
    paths = _resolve_pipeline_paths(input_dir, frame_output_dir, matte_output_dir, smooth_output_dir)
    frame_options = _frame_options(
        fps,
        max_frames,
        diff_threshold,
        dedup_enabled,
        dedup_renumber,
        selection_root,
        selection_emotions,
        selection_types,
        progress_include,
        progress_exclude,
        priority_characters,
    )
    cherry_options = _cherry_options(use_smooth, resize_width, resize_height)
    resource_json = _resource_json_options(resource_json_enabled, resource_json_output_path, resource_json_types)
    return {
        "ok": True,
        **paths,
        "workflow_path": workflow_path,
        "steps": ["1. 飞书表格下载视频并抽帧", "2. 抽帧结果作为抠图输出" if fake_matting_from_frames else "2. ComfyUI 批量抠图"]
        + (["3. Cherry 帧序列平滑/缩放/锐化"] if run_cherry else []),
        "frame": frame_options,
        "cherry": cherry_options,
        "run_cherry": bool(run_cherry),
        "fake_matting_from_frames": bool(fake_matting_from_frames),
        "resource_json": resource_json,
    }


def run_start(
    input_dir: str | None = None,
    frame_output_dir: str | None = None,
    matte_output_dir: str | None = None,
    smooth_output_dir: str | None = None,
    workflow_path: str | None = None,
    fps: int = 24,
    max_frames: int = 0,
    diff_threshold: float = 0.2,
    dedup_enabled: bool = False,
    dedup_renumber: bool = False,
    selection_root: str | None = None,
    selection_emotions: list[str] | None = None,
    selection_types: list[str] | None = None,
    progress_include: list[str] | None = None,
    progress_exclude: list[str] | None = None,
    priority_characters: list[str] | None = None,
    use_smooth: bool = False,
    resize_width: int = 384,
    resize_height: int = 512,
    resource_json_enabled: bool = True,
    resource_json_output_path: str | None = None,
    resource_json_types: list[str] | None = None,
    notify_interval_seconds: int = 60,
    run_cherry: bool = True,
    fake_matting_from_frames: bool = False,
) -> dict[str, Any]:
    from assetclaw_matting.db.sqlite import get_connection
    from assetclaw_matting.runtime_context import get_runtime_context

    preview = run_preview(
        input_dir,
        frame_output_dir,
        matte_output_dir,
        smooth_output_dir,
        workflow_path,
        fps,
        max_frames,
        diff_threshold,
        dedup_enabled,
        dedup_renumber,
        selection_root,
        selection_emotions,
        selection_types,
        progress_include,
        progress_exclude,
        priority_characters,
        use_smooth,
        resize_width,
        resize_height,
        resource_json_enabled,
        resource_json_output_path,
        resource_json_types,
        run_cherry,
        fake_matting_from_frames,
    )
    run_id = _run_id()
    ctx = get_runtime_context()
    options = {
        **preview["frame"],
        "cherry": preview["cherry"],
        "run_cherry": bool(preview.get("run_cherry", True)),
        "fake_matting_from_frames": bool(preview.get("fake_matting_from_frames", False)),
        "resource_json": preview["resource_json"],
        "notify_interval_seconds": max(30, min(int(notify_interval_seconds), 3600)),
        "chat_id": (ctx.get("chat_id") or "") if ctx.get("channel") == "feishu" else "",
        "archived": False,
    }
    created = _now()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO pipeline_runs
            (id, status, input_dir, frame_output_dir, matte_output_dir, smooth_output_dir, workflow_path,
             frame_run_id, comfyui_run_id, cherry_run_id, current_step, options_json, error, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                "RUNNING",
                preview["input_dir"],
                preview["frame_output_dir"],
                preview["matte_output_dir"],
                preview["smooth_output_dir"],
                workflow_path or "",
                "",
                "",
                "",
                "frame",
                json.dumps(options, ensure_ascii=False),
                "",
                created,
                created,
            ),
        )
    matting_label = "抽帧当抠图" if options["fake_matting_from_frames"] else "抠图"
    _notify(run_id, "动画自动化流程已启动：抽帧 -> " + matting_label + (" -> 平滑" if options["run_cherry"] else ""))
    _start_worker(run_id)
    return {"ok": True, "run_id": run_id, "status": "RUNNING", **preview}


def run_status(run_id: str | None = None) -> dict[str, Any]:
    row = _get_run(run_id)
    if not row:
        return {"ok": False, "error": "pipeline run not found"}
    payload = {
        "ok": True,
        "run_id": row["id"],
        "status": row["status"],
        "current_step": row["current_step"],
        "workspace_root": _workspace_root_from_paths(row["input_dir"], row["frame_output_dir"], row["matte_output_dir"], row["smooth_output_dir"]),
        "input_dir": row["input_dir"],
        "frame_output_dir": row["frame_output_dir"],
        "matte_output_dir": row["matte_output_dir"],
        "smooth_output_dir": row["smooth_output_dir"],
        "workflow_path": row["workflow_path"],
        "frame_run_id": row["frame_run_id"],
        "comfyui_run_id": row["comfyui_run_id"],
        "cherry_run_id": row["cherry_run_id"],
        "error": row["error"] or "",
    }
    if row["frame_run_id"]:
        from assetclaw_matting.skills.frame_skills import run_status as frame_status

        payload["frame"] = frame_status(row["frame_run_id"])
    if row["comfyui_run_id"]:
        from assetclaw_matting.skills.comfyui_skills import run_status as comfy_status

        payload["comfyui"] = comfy_status(row["comfyui_run_id"], include_gpu=True)
    if row["cherry_run_id"]:
        from assetclaw_matting.skills.cherry_skills import run_status as cherry_status

        payload["cherry"] = cherry_status(row["cherry_run_id"], include_gpu=True)
    payload["detail_lines"] = _detail_lines(payload)
    return payload


def run_list(limit: int = 10, include_finished: bool = False) -> dict[str, Any]:
    from assetclaw_matting.db.sqlite import get_connection

    with get_connection() as conn:
        rows = conn.execute("SELECT id, status FROM pipeline_runs ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 50)),)).fetchall()
    items = []
    for row in rows:
        if row["status"] in {"DONE", "FAILED", "CANCELED"} and not include_finished:
            continue
        items.append(run_status(row["id"]))
    return {"ok": True, "count": len(items), "items": items}


def run_cancel(run_id: str | None = None) -> dict[str, Any]:
    row = _get_run(run_id)
    if not row:
        return {"ok": False, "error": "pipeline run not found"}
    if row["frame_run_id"]:
        from assetclaw_matting.skills.frame_skills import run_cancel

        run_cancel(row["frame_run_id"])
    if row["comfyui_run_id"]:
        from assetclaw_matting.skills.comfyui_skills import run_cancel

        run_cancel(row["comfyui_run_id"], interrupt_current=True)
    if row["cherry_run_id"]:
        from assetclaw_matting.skills.cherry_skills import run_cancel

        run_cancel(row["cherry_run_id"])
    _set_status(row["id"], "CANCELED")
    return {"ok": True, "run_id": row["id"], "status": "CANCELED"}


def preview_run_start_confirmation(arguments: dict[str, Any], confirmation_id: str) -> str:
    preview = run_preview(**arguments)
    lines = [
        "请确认是否开始动画自动化流程：",
        "步骤：抽帧 -> ComfyUI 抠图 -> Cherry 平滑",
        f"工作区：{preview['workspace_root']}",
        f"视频下载：{preview['input_dir']}",
        f"抽帧输出：{preview['frame_output_dir']}",
        f"抠图输出：{preview['matte_output_dir']}",
        f"平滑输出：{preview['smooth_output_dir']}",
        (
            f"fps：{preview['frame']['fps']}，最多帧数：{preview['frame']['max_frames']}，"
            f"剔除关键帧：{'开' if preview['frame']['dedup_enabled'] else '关'}，"
            f"筛选：{preview['frame']['selection_root'] or '全部'}/{','.join(preview['frame']['selection_emotions'] or ['全部'])}"
        ),
        (
            f"后处理：{preview['cherry']['resize_width']}x{preview['cherry']['resize_height']}，"
            f"时序平滑：{'开' if preview['cherry']['use_smooth'] else '关'}"
        ),
        f"回复：确认执行 {confirmation_id}",
    ]
    return "\n".join(lines)


def _resolve_pipeline_paths(
    input_dir: str | None,
    frame_output_dir: str | None,
    matte_output_dir: str | None,
    smooth_output_dir: str | None,
) -> dict[str, str]:
    defaults = default_automation_paths()
    return {
        "workspace_root": str(validate_path(defaults["workspace_root"], must_exist=False)),
        "input_dir": str(validate_path(input_dir or defaults["video_dir"], must_exist=False)),
        "frame_output_dir": str(validate_path(frame_output_dir or defaults["frame_dir"], must_exist=False)),
        "matte_output_dir": str(validate_path(matte_output_dir or defaults["matte_dir"], must_exist=False)),
        "smooth_output_dir": str(validate_path(smooth_output_dir or defaults["smooth_dir"], must_exist=False)),
    }


def _frame_options(
    fps: int,
    max_frames: int,
    diff_threshold: float,
    dedup_enabled: bool,
    dedup_renumber: bool,
    selection_root: str,
    selection_emotions: list[str] | None,
    selection_types: list[str] | None,
    progress_include: list[str] | None,
    progress_exclude: list[str] | None,
    priority_characters: list[str] | None,
) -> dict[str, Any]:
    return {
        "fps": int(fps),
        "max_frames": int(max_frames),
        "diff_threshold": float(diff_threshold),
        "dedup_enabled": bool(dedup_enabled),
        "dedup_renumber": bool(dedup_renumber),
        "selection_root": str(selection_root or ""),
        "selection_emotions": list(selection_emotions or []),
        "selection_types": list(selection_types or []),
        "progress_include": list(progress_include or []),
        "progress_exclude": list(progress_exclude or []),
        "priority_characters": list(priority_characters or []),
    }


def _cherry_options(use_smooth: bool, resize_width: int, resize_height: int) -> dict[str, Any]:
    return {
        "use_denoise": True,
        "use_blur": True,
        "use_resize1": True,
        "resize1_width": max(int(resize_width) * 2, int(resize_width)),
        "resize1_height": max(int(resize_height) * 2, int(resize_height)),
        "use_sharp1": True,
        "use_resize2": True,
        "resize2_width": int(resize_width),
        "resize2_height": int(resize_height),
        "use_sharp2": True,
        "use_smooth": bool(use_smooth),
        "resize_width": int(resize_width),
        "resize_height": int(resize_height),
    }


def _resource_json_options(
    enabled: bool,
    output_path: str | None,
    types: list[str] | None,
) -> dict[str, Any]:
    return {
        "enabled": bool(enabled),
        "output_path": output_path or "",
        "types": list(types or []),
    }


def _workspace_root_from_paths(*paths: str) -> str:
    for raw in paths:
        path = Path(str(raw or ""))
        if path.name.lower() in {"videos", "frames", "matte", "smooth"}:
            return str(path.parent)
    return ""


def _split_route_roots(workspace_root: str) -> list[Path]:
    if not workspace_root:
        return []
    root = Path(workspace_root)
    routes = []
    for asset_kind in ("scene", "emoji"):
        route = root / asset_kind
        if route.exists():
            routes.append(route)
        for variant in ("default", "temporal_smooth"):
            route = root / asset_kind / variant
            if route.exists():
                routes.append(route)
    return routes


def _route_cherry_options(route: Path) -> dict[str, Any] | None:
    parts = [part.lower() for part in route.parts]
    asset_kind = "scene" if "scene" in parts else ("emoji" if "emoji" in parts else "")
    variant = "temporal_smooth" if "temporal_smooth" in parts else "default"
    if not asset_kind:
        return None
    width, height = (384, 512) if asset_kind == "scene" else (256, 256)
    return _cherry_options(variant == "temporal_smooth", width, height)


def _start_worker(run_id: str) -> None:
    if run_id in _WORKER_RUNS:
        return
    _WORKER_RUNS.add(run_id)
    threading.Thread(target=_worker, args=(run_id,), daemon=True).start()


def _worker(run_id: str) -> None:
    try:
        row = _get_run(run_id)
        if not row:
            return
        opts = json.loads(row["options_json"] or "{}")
        token = None
        if opts.get("chat_id"):
            from assetclaw_matting.runtime_context import set_runtime_context

            token = set_runtime_context(channel="feishu", chat_id=opts["chat_id"])
        from assetclaw_matting.skills.frame_skills import run_start as frame_start, run_status as frame_status

        _notify(run_id, "动画自动化流程：开始飞书下载和抽帧")
        frame = frame_start(
            download_dir=row["input_dir"],
            export_dir=row["frame_output_dir"],
            fps=opts["fps"],
            max_frames=opts.get("max_frames", 0),
            diff_threshold=opts["diff_threshold"],
            dedup_enabled=opts.get("dedup_enabled", False),
            dedup_renumber=opts.get("dedup_renumber", False),
            root=opts.get("selection_root") or None,
            emotions=opts.get("selection_emotions") or None,
            types=opts.get("selection_types") or None,
            progress_include=opts.get("progress_include") or None,
            progress_exclude=opts.get("progress_exclude") or None,
            priority_characters=opts.get("priority_characters") or None,
            notify_interval_seconds=opts["notify_interval_seconds"],
        )
        _update_ids(run_id, frame_run_id=frame["run_id"], current_step="frame")
        if not _wait_until_done(lambda: frame_status(frame["run_id"]), run_id):
            return
        status = frame_status(frame["run_id"])
        if status.get("status") != "DONE":
            _fail(run_id, f"抽帧失败：{status.get('error') or status.get('status')}")
            return
        _notify(run_id, _format_frame_stage_summary(row, status))
        route_roots = _split_route_roots(
            _workspace_root_from_paths(row["input_dir"], row["frame_output_dir"], row["matte_output_dir"], row["smooth_output_dir"])
        )
        active_routes = [route for route in route_roots if any((route / "frames").rglob("*.png"))]
        if not any(Path(row["frame_output_dir"]).rglob("*.png")) and not active_routes:
            _fail(run_id, "抽帧没有产出图片。请确认飞书表格里有“动画”视频附件，并且角色/情绪父子记录对应正确。")
            return
        resource_json = opts.get("resource_json") or {}
        if resource_json.get("enabled", True) and (Path(row["frame_output_dir"]) / "_pipeline_manifest.json").exists():
            output_path = resource_json.get("output_path") or str(Path(row["frame_output_dir"]).parent / "animation_resource_manifest.json")
            _export_resource_json(
                Path(row["frame_output_dir"]) / "_pipeline_manifest.json",
                output_path,
                resource_json.get("types") or [],
            )

        comfy_routes = active_routes or [Path(row["frame_output_dir"]).parent]
        detail: dict[str, Any] = {}
        if opts.get("fake_matting_from_frames"):
            _update_ids(run_id, current_step="comfyui")
            _notify(run_id, f"动画自动化流程：抽帧完成，FAKER 抠图开始\n已登记视频：{status.get('manifest_count', 0)} 条")
            total_copied = 0
            for route in comfy_routes:
                frames_dir = route / "frames" if (route / "frames").is_dir() else Path(row["frame_output_dir"])
                matte_dir = route / "matte" if (route / "matte").parent.exists() else Path(row["matte_output_dir"])
                copied = _copy_frames_as_matte(frames_dir, matte_dir)
                total_copied += copied
                _notify(run_id, f"步骤3 FAKER 抠图完成：{route.name}\n输入：{frames_dir}\n输出：{matte_dir}\n复制：{copied} 张")
            if total_copied <= 0:
                _fail(run_id, "FAKER 抠图没有产出图片。请确认抽帧目录存在 PNG。")
                return
            detail = {"role": "faker", "emotion": "frames-as-matte", "frame": f"{total_copied} png"}
        else:
            from assetclaw_matting.skills.comfyui_skills import run_start as comfy_start, run_status as comfy_status

            matte_size = _workflow_output_size(row["workflow_path"])
            size_line = f"本次抠图输出尺寸：{matte_size}" if matte_size else "本次抠图输出尺寸：未能从 workflow 自动识别"
            _notify(run_id, f"动画自动化流程：抽帧完成，开始 ComfyUI 抠图\n已登记视频：{status.get('manifest_count', 0)} 条\n{size_line}\n工作流：{row['workflow_path']}")
            for route in comfy_routes:
                frames_dir = route / "frames" if (route / "frames").is_dir() else Path(row["frame_output_dir"])
                matte_dir = route / "matte" if (route / "matte").parent.exists() else Path(row["matte_output_dir"])
                if not any(frames_dir.rglob("*.png")):
                    continue
                _notify(run_id, f"步骤3 抠图开始：{route.name}\n{size_line}\n输入：{frames_dir}\n输出：{matte_dir}")
                comfy = comfy_start(
                    workflow_path=row["workflow_path"] or None,
                    input_dir=str(frames_dir),
                    output_dir=str(matte_dir),
                    recursive=True,
                    preserve_structure=True,
                    max_images=50000,
                    skip_existing=True,
                    priority_characters=opts.get("priority_characters") or None,
                    notify_interval_seconds=opts["notify_interval_seconds"],
                )
                _update_ids(run_id, comfyui_run_id=str(comfy["run_id"]), current_step="comfyui")
                if not _wait_until_done(lambda rid=str(comfy["run_id"]): comfy_status(rid, include_gpu=False), run_id):
                    return
                status = comfy_status(str(comfy["run_id"]), include_gpu=False)
                if status.get("status") not in {"DONE", "DONE_WITH_ERRORS"}:
                    _fail(run_id, f"抠图失败：{status.get('error') or status.get('status')}")
                    return
                _notify(
                    run_id,
                    "步骤3 抠图完成："
                    f"{status.get('completed', 0)}/{status.get('total', 0)} 张，"
                    f"失败 {status.get('failed', 0)}，状态 {status.get('status')}\n"
                    f"输出：{status.get('output_dir')}",
                )
            detail = status.get("last_completed_detail") or {}
        if not opts.get("run_cherry", True):
            suffix = f"\n最后抠图：{detail.get('role')}/{detail.get('emotion')}/{detail.get('frame')}" if detail else ""
            _set_status(run_id, "DONE")
            _notify(run_id, f"动画自动化流程完成：{run_id}\n最终输出：{row['matte_output_dir']}{suffix}")
            return

        from assetclaw_matting.skills.cherry_skills import run_start as cherry_start, run_status as cherry_status

        suffix = f"\n最后抠图：{detail.get('role')}/{detail.get('emotion')}/{detail.get('frame')}" if detail else ""
        _notify(run_id, f"动画自动化流程：抠图完成，开始 Cherry 平滑{suffix}")
        for route in comfy_routes:
            matte_dir = route / "matte" if (route / "matte").is_dir() else Path(row["matte_output_dir"])
            smooth_dir = route / "smooth" if (route / "smooth").parent.exists() else Path(row["smooth_output_dir"])
            if not any(matte_dir.rglob("*.png")):
                continue
            cherry_options = _route_cherry_options(route) or (opts.get("cherry") or _cherry_options(False, 384, 512))
            _notify(run_id, _format_cherry_stage_start(route, matte_dir, smooth_dir, cherry_options))
            cherry = cherry_start(
                input_dir=str(matte_dir),
                output_dir=str(smooth_dir),
                recursive=True,
                max_images=50000,
                skip_existing=True,
                notify_interval_seconds=opts["notify_interval_seconds"],
                **cherry_options,
            )
            _update_ids(run_id, cherry_run_id=str(cherry["run_id"]), current_step="cherry")
            if not _wait_until_done(lambda rid=str(cherry["run_id"]): cherry_status(rid, include_gpu=False), run_id):
                return
            status = cherry_status(str(cherry["run_id"]), include_gpu=False)
            if status.get("status") not in {"DONE", "DONE_WITH_ERRORS"}:
                _fail(run_id, f"平滑失败：{status.get('error') or status.get('status')}")
                return
            _notify(
                run_id,
                "步骤4 Cherry 完成："
                f"{status.get('completed', 0)}/{status.get('total', 0)} 张，"
                f"失败 {status.get('failed', 0)}，状态 {status.get('status')}\n"
                f"输出：{status.get('output_dir')}",
            )
        _set_status(run_id, "DONE")
        _notify(run_id, f"动画自动化流程完成：{run_id}\n最终输出：{row['smooth_output_dir']}")
    except Exception as exc:
        _fail(run_id, str(exc))
    finally:
        if "token" in locals() and token is not None:
            from assetclaw_matting.runtime_context import reset_runtime_context

            reset_runtime_context(token)
        _WORKER_RUNS.discard(run_id)


def _detail_lines(payload: dict[str, Any]) -> list[str]:
    lines = []
    step = payload.get("current_step")
    if step == "frame":
        frame = payload.get("frame") or {}
        current = frame.get("current_item") or {}
        if current:
            lines.append(f"当前位置：抽帧 {current.get('role')}/{current.get('emotion')}")
        if frame.get("last_log"):
            lines.append(f"抽帧日志：{frame.get('last_log')}")
    elif step == "comfyui":
        detail = (payload.get("comfyui") or {}).get("last_completed_detail") or {}
        if detail:
            lines.append(f"当前位置：抠图刚完成 {detail.get('role')}/{detail.get('emotion')}/{detail.get('frame')}")
    elif step == "cherry":
        detail = (payload.get("cherry") or {}).get("last_completed_detail") or {}
        if detail:
            lines.append(f"当前位置：平滑刚完成 {detail.get('role')}/{detail.get('emotion')}/{detail.get('frame')}")
    return lines


def _copy_frames_as_matte(frames_dir: Path, matte_dir: Path) -> int:
    if not frames_dir.is_dir():
        return 0
    count = 0
    for src in sorted(frames_dir.rglob("*.png")):
        rel = src.relative_to(frames_dir)
        dst = matte_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        count += 1
    return count


def _workflow_output_size(workflow_path: str | None) -> str:
    if not workflow_path:
        return ""
    try:
        workflow = json.loads(Path(workflow_path).read_text(encoding="utf-8"))
    except Exception:
        return ""
    if isinstance(workflow.get("nodes"), list):
        for node in workflow.get("nodes") or []:
            if not isinstance(node, dict) or str(node.get("type") or "") != "CherryPSResize":
                continue
            widgets = node.get("widgets_values") if isinstance(node.get("widgets_values"), list) else []
            if len(widgets) >= 3:
                return f"{int(widgets[1])}x{int(widgets[2])}"
    if isinstance(workflow, dict):
        for node in workflow.values():
            if not isinstance(node, dict) or str(node.get("class_type") or "") != "CherryPSResize":
                continue
            inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
            width = inputs.get("目标宽度") or inputs.get("width") or inputs.get("target_width")
            height = inputs.get("目标高度") or inputs.get("height") or inputs.get("target_height")
            if width and height:
                return f"{int(width)}x{int(height)}"
    return ""


def _format_frame_stage_summary(row: Any, status: dict[str, Any]) -> str:
    root = Path(_workspace_root_from_paths(row["input_dir"], row["frame_output_dir"], row["matte_output_dir"], row["smooth_output_dir"]))
    summary = _source_manifest_summary(root / "source_manifest.json")
    lines = [
        "步骤1/2 飞书下载 + 抽帧完成：",
        f"记录：{status.get('processed_records', 0)}/{status.get('total_records', 0)}，已登记视频 {status.get('manifest_count', 0)} 条",
        f"视频附件：下载 {summary['attachment_count']} 个，跳过 {summary['skipped_count']} 条",
        f"抽帧输出：{status.get('export_dir')}",
    ]
    if summary["skip_reasons"]:
        reasons = "；".join(f"{reason} x{count}" for reason, count in summary["skip_reasons"].items())
        lines.append(f"跳过原因：{reasons}")
    lines.append("状态策略：只跳过 已完成 / 不处理；其他状态均重新下载并抽帧")
    return "\n".join(lines)


def _source_manifest_summary(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"attachment_count": 0, "skipped_count": 0, "skip_reasons": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    records = data.get("records") or []
    skipped = data.get("skipped") or []
    skip_reasons: dict[str, int] = {}
    for item in skipped:
        reason = str(item.get("reason") or item.get("skipReason") or "unknown")
        skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
    attachment_count = 0
    for item in records:
        if item.get("skipped"):
            continue
        attachment_count += len(item.get("attachments") or [])
    return {
        "attachment_count": attachment_count,
        "skipped_count": len(skipped),
        "skip_reasons": skip_reasons,
    }


def _format_cherry_stage_start(route: Path, matte_dir: Path, smooth_dir: Path, options: dict[str, Any]) -> str:
    variant = "时序平滑" if options.get("use_smooth") else "普通后处理"
    size = f"{options.get('resize_width')}x{options.get('resize_height')}"
    return "\n".join(
        [
            f"步骤4 Cherry 开始：{route.name}",
            f"模式：{variant}，分辨率：{size}",
            f"输入：{matte_dir}",
            f"输出：{smooth_dir}",
        ]
    )


def _export_resource_json(manifest_path: Path, output_path: str, types: list[str]) -> None:
    from scripts.export_animation_resource_json import export_from_manifest

    export_from_manifest(manifest_path, Path(output_path), types=types)


def _wait_until_done(fn, pipeline_id: str) -> bool:
    while True:
        row = _get_run(pipeline_id)
        if not row or row["status"] == "CANCELED":
            return False
        status = fn()
        if status.get("status") in {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED"}:
            return True
        time.sleep(5)


def _get_run(run_id: str | None = None):
    from assetclaw_matting.db.sqlite import get_connection

    with get_connection() as conn:
        if run_id:
            return conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
        row = conn.execute("SELECT * FROM pipeline_runs WHERE status = 'RUNNING' ORDER BY created_at DESC LIMIT 1").fetchone()
        return row or conn.execute("SELECT * FROM pipeline_runs ORDER BY created_at DESC LIMIT 1").fetchone()


def _set_status(run_id: str, status: str) -> None:
    from assetclaw_matting.db.sqlite import get_connection

    with get_connection() as conn:
        conn.execute("UPDATE pipeline_runs SET status = ?, updated_at = ? WHERE id = ?", (status, _now(), run_id))


def _update_ids(run_id: str, **updates: str) -> None:
    from assetclaw_matting.db.sqlite import get_connection

    clauses = ["updated_at = ?"]
    values: list[Any] = [_now()]
    for key, value in updates.items():
        clauses.append(f"{key} = ?")
        values.append(value)
    values.append(run_id)
    with get_connection() as conn:
        conn.execute(f"UPDATE pipeline_runs SET {', '.join(clauses)} WHERE id = ?", values)


def _fail(run_id: str, error: str) -> None:
    from assetclaw_matting.db.sqlite import get_connection

    with get_connection() as conn:
        conn.execute("UPDATE pipeline_runs SET status = ?, error = ?, updated_at = ? WHERE id = ?", ("FAILED", error, _now(), run_id))
    _notify(run_id, f"动画自动化流程失败：{error}")


def _notify(run_id: str, text: str) -> None:
    row = _get_run(run_id)
    if not row:
        return
    options = json.loads(row["options_json"] or "{}")
    chat_id = options.get("chat_id")
    if not chat_id:
        return
    from assetclaw_matting.services.notification_service import send_text

    send_text(chat_id, text)
