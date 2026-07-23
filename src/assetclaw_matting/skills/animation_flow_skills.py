from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from assetclaw_matting.config import settings
from assetclaw_matting.runtime_context import get_runtime_context, reset_runtime_context, set_runtime_context
from assetclaw_matting.skills.frame_skills import default_automation_paths
from assetclaw_matting.skills import matting_pipeline_skills
from assetclaw_matting.skills.security import validate_path
from tools.animation_automation.core import ASSET_KINDS
from tools.animation_automation.core import build_unity_ready


RUN_DIR = Path(settings.storage_dir) / "animation_flow_runs"
STAGES = [
    ("feishu_download", "1 飞书文档/表格下载视频"),
    ("frame_extract", "2 抽帧"),
    ("matting", "3 抠图"),
    ("cherry_smooth", "4 Cherry 平滑后处理"),
    ("unity_ready", "5 unity_ready 整理"),
    ("unity_import", "6 Unity 插件导入引擎"),
    ("p4_shelve", "7 P4 reconcile/changelist/shelve/report"),
]
_WORKERS: set[str] = set()


def run_preview(
    date_root: str | None = None,
    unity_project: str | None = None,
    p4_workflow: str | None = None,
    p4_workspace: str | None = None,
    p4_stream: str = "//streams/rel_0.0.1",
    package: str = "both",
    unity_import_mode: str = "import",
    import_mode: str | None = None,
    workflow_path: str | None = None,
    priority_characters: list[str] | None = None,
    **_: Any,
) -> dict[str, Any]:
    root = _date_root(date_root)
    selected_mode = _normalize_unity_import_mode(import_mode or unity_import_mode)
    workflow = _production_workflow_path(workflow_path)
    return {
        "ok": True,
        "date_root": str(root),
        "unity_ready": str(root / "unity_ready"),
        "workflow_path": str(workflow),
        "workflow_name": workflow.name,
        "unity_project": str(validate_path(unity_project or settings.unity_project_dir, must_exist=False)),
        "package": package,
        "unity_import_mode": selected_mode,
        "feishu_progress_policy": {
            "include": ["待抽帧"],
            "download_extract_only": True,
        },
        "priority_characters": list(priority_characters or ["casualheather"]),
        "stages": _stage_payload(),
        "p4": _p4_plan(p4_workflow, p4_workspace, root, p4_stream, selected_mode),
    }


def run_start(
    date_root: str | None = None,
    workflow_path: str | None = None,
    unity_project: str | None = None,
    p4_workflow: str | None = None,
    p4_workspace: str | None = None,
    p4_stream: str = "//streams/rel_0.0.1",
    package: str = "both",
    unity_import_mode: str = "import",
    import_mode: str | None = None,
    fps: int = 24,
    notify_interval_seconds: int = 60,
    allow_p4_writes: bool = True,
    fake_matting_from_frames: bool = False,
    priority_characters: list[str] | None = None,
    **_: Any,
) -> dict[str, Any]:
    selected_mode = _normalize_unity_import_mode(import_mode or unity_import_mode)
    pipeline_notice = ""
    if not workflow_path and Path(settings.comfyui_workflow_path).name == settings.matting_pipeline_workflow_name:
        pipeline = matting_pipeline_skills.ensure_latest_for_task()
        if not pipeline.get("ok"):
            raise RuntimeError(str(pipeline.get("error") or "matting pipeline preflight failed"))
        workflow_path = str(pipeline.get("workflow_path") or "")
        pipeline_notice = str(pipeline.get("message") or "")
    workflow = _production_workflow_path(workflow_path)
    preview = run_preview(
        date_root=date_root,
        unity_project=unity_project,
        p4_workflow=p4_workflow,
        p4_workspace=p4_workspace,
        p4_stream=p4_stream,
        package=package,
        unity_import_mode=selected_mode,
        workflow_path=str(workflow),
        priority_characters=priority_characters,
    )
    ctx = get_runtime_context()
    run_id = "AFLOW_" + uuid.uuid4().hex[:12].upper()
    run = {
        "id": run_id,
        "status": "RUNNING",
        "current_stage": "feishu_download",
        "created_at": _now(),
        "updated_at": _now(),
        "date_root": preview["date_root"],
        "unity_ready": preview["unity_ready"],
        "unity_project": preview["unity_project"],
        "package": package,
        "unity_import_mode": selected_mode,
        "workflow_path": str(workflow),
        "workflow_name": workflow.name,
        "pipeline_notice": pipeline_notice,
        "fps": int(fps),
        "notify_interval_seconds": max(30, min(int(notify_interval_seconds), 3600)),
        "allow_p4_writes": bool(allow_p4_writes),
        "fake_matting_from_frames": bool(fake_matting_from_frames),
        "priority_characters": list(priority_characters or ["casualheather"]),
        "p4": preview["p4"],
        "chat_id": (ctx.get("chat_id") or "") if ctx.get("channel") == "feishu" else "",
        "stages": _stage_payload("feishu_download"),
        "children": {},
        "error": "",
        "worker_pid": os.getpid(),
    }
    _save(run)
    notice = f"\n{pipeline_notice}" if pipeline_notice else ""
    _notify(run, f"完整动画自动化流程已启动：1-7 步会按顺序推进，P4 submit 永远禁用。{notice}")
    _start_worker(run_id)
    return {"ok": True, "run_id": run_id, **_public(run)}


def run_status(run_id: str | None = None, **_: Any) -> dict[str, Any]:
    run = _load(run_id)
    if not run:
        return {"ok": False, "error": "animation flow run not found"}
    payload = {"ok": True, "run_id": run["id"], **_public(run)}
    pipeline_id = (run.get("children") or {}).get("pipeline_run_id")
    if pipeline_id:
        from assetclaw_matting.skills.pipeline_skills import run_status as pipeline_status

        payload["pipeline"] = pipeline_status(pipeline_id)
    cherry_id = (run.get("children") or {}).get("cherry_run_id")
    if cherry_id:
        from assetclaw_matting.skills.cherry_skills import run_status as cherry_status

        payload["cherry"] = cherry_status(cherry_id)
    cherry_ids = (run.get("children") or {}).get("cherry_run_ids") or []
    if cherry_ids:
        from assetclaw_matting.skills.cherry_skills import run_status as cherry_status

        payload["cherry_runs"] = [cherry_status(item) for item in cherry_ids]
    return payload


def run_list(limit: int = 10, include_finished: bool = False, **_: Any) -> dict[str, Any]:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for path in sorted(RUN_DIR.glob("AFLOW_*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        run = json.loads(path.read_text(encoding="utf-8"))
        if run.get("status") in {"DONE", "FAILED", "CANCELED", "BLOCKED"} and not include_finished:
            continue
        items.append({"run_id": run["id"], **_public(run)})
        if len(items) >= max(1, min(int(limit), 50)):
            break
    return {"ok": True, "count": len(items), "items": items}


def recover_incomplete_runs() -> dict[str, Any]:
    """Close flow records whose owning process no longer has a worker."""
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    closed: list[str] = []
    still_running: list[str] = []
    for path in sorted(RUN_DIR.glob("AFLOW_*.json"), key=lambda item: item.stat().st_mtime):
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(run.get("status") or "").upper() != "RUNNING":
            continue
        run_id = str(run.get("id") or path.stem)
        worker_pid = int(run.get("worker_pid") or 0)
        local_worker = run_id in _WORKERS
        remote_worker = worker_pid > 0 and worker_pid != os.getpid() and _process_alive(worker_pid)
        if local_worker or remote_worker:
            still_running.append(run_id)
            continue
        run["status"] = "FAILED"
        run["worker_pid"] = 0
        run["error"] = "检测到动画流程执行进程已退出，已自动清除僵死运行状态。"
        _save(run)
        closed.append(run_id)
    return {"ok": True, "closed": closed, "still_running": still_running}


def run_cancel(run_id: str | None = None, **_: Any) -> dict[str, Any]:
    run = _load(run_id)
    if not run:
        return {"ok": False, "error": "animation flow run not found"}
    pipeline_id = (run.get("children") or {}).get("pipeline_run_id")
    if pipeline_id:
        from assetclaw_matting.skills.pipeline_skills import run_cancel as pipeline_cancel

        pipeline_cancel(pipeline_id)
    cherry_id = (run.get("children") or {}).get("cherry_run_id")
    if cherry_id:
        from assetclaw_matting.skills.cherry_skills import run_cancel as cherry_cancel

        cherry_cancel(cherry_id)
    _mark(run, "CANCELED", run.get("current_stage") or "feishu_download")
    return {"ok": True, "run_id": run["id"], "status": "CANCELED"}


def run_resume(run_id: str | None = None, **_: Any) -> dict[str, Any]:
    run = _load(run_id)
    if not run:
        return {"ok": False, "error": "animation flow run not found"}
    if run["id"] in _WORKERS:
        return {"ok": True, "run_id": run["id"], **_public(run), "message": "完整动画流程已经在运行。"}
    if run.get("status") in {"DONE", "CANCELED"}:
        return {"ok": True, "run_id": run["id"], **_public(run), "message": "任务已经结束，不能继续。"}
    if run.get("current_stage") not in {"unity_import", "p4_shelve"}:
        return {
            "ok": False,
            "run_id": run["id"],
            **_public(run),
            "error": f"当前阶段 {run.get('current_stage')} 不支持从 animation_flow.resume 继续。",
        }
    if run.get("current_stage") == "unity_import":
        unity = ((run.get("children") or {}).get("unity_import") or {})
        recovered = _recover_late_unity_import(unity)
        if not recovered.get("ok"):
            return {
                "ok": False,
                "run_id": run["id"],
                **_public(run),
                "error": "Unity 导入还没有可确认的完成结果，暂时不能继续 P4。",
                "unity_import": recovered,
            }
        run.setdefault("children", {})["unity_import"] = recovered
        run["error"] = ""
        _save(run)
    _start_resume_worker(run["id"])
    return {"ok": True, "run_id": run["id"], **_public(run), "message": "已从当前阶段继续完整动画流程。"}


def preview_run_start_confirmation(arguments: dict[str, Any], confirmation_id: str) -> str:
    preview = run_preview(**arguments)
    lines = ["请确认是否开始完整动画自动化流程：", f"工作区：{preview['date_root']}", "步骤："]
    lines.extend(f"- {stage['label']}" for stage in preview["stages"])
    lines.extend(
        [
            f"ComfyUI 工作流：{preview['workflow_name']}",
            f"工作流路径：{preview['workflow_path']}",
            f"Unity Project：{preview['unity_project']}",
            f"Unity 模式：{'资源迭代/替换' if preview.get('unity_import_mode') == 'iteration' else '新导入'}",
            "飞书状态：仅处理“待抽帧”，其他状态全部跳过。",
            f"后段优先角色：{', '.join(preview.get('priority_characters') or ['无'])}",
            "P4：第 7 步会执行 create CL / reconcile / shelve / report；submit disabled。",
            f"回复：确认执行 {confirmation_id}",
        ]
    )
    return "\n".join(lines)


def preview_run_resume_confirmation(arguments: dict[str, Any], confirmation_id: str) -> str:
    run = _load(arguments.get("run_id"))
    if not run:
        return f"找不到这个完整动画流程任务。\n回复：确认执行 {confirmation_id}"
    lines = [
        "请确认是否继续完整动画自动化流程：",
        f"Run：{run['id']}",
        f"当前阶段：{run.get('current_stage')}，状态：{run.get('status')}",
        f"工作区：{run.get('date_root')}",
    ]
    unity = ((run.get("children") or {}).get("unity_import") or {})
    recovered = _recover_late_unity_import(unity)
    if recovered.get("ok"):
        result = recovered.get("result") or {}
        totals = _unity_import_totals(result)
        lines.append(
            "Unity：已确认"
            f"（任务 {totals['tasks']}，贴图 {totals['textures']}，替换 {totals['replaced']}，跳过 {totals['skipped']}）"
        )
        lines.append("继续内容：只执行第 7 步 P4 create CL / reconcile / shelve / report；submit disabled。")
    else:
        lines.append("Unity：尚未确认完成，确认后也会先拒绝继续，避免错误提交 P4。")
    lines.append(f"回复：确认执行 {confirmation_id}")
    return "\n".join(lines)


def _worker(run_id: str) -> None:
    token = None
    try:
        run = _load(run_id)
        if not run:
            return
        if run.get("chat_id"):
            token = set_runtime_context(channel="feishu", chat_id=run["chat_id"])

        from assetclaw_matting.skills.pipeline_skills import run_start as pipeline_start, run_status as pipeline_status

        root = Path(run["date_root"])
        _mark(run, "RUNNING", "feishu_download")
        pipeline = pipeline_start(
            input_dir=str(root / "videos"),
            frame_output_dir=str(root / "frames"),
            matte_output_dir=str(root / "matte"),
            smooth_output_dir=str(root / "smooth"),
            workflow_path=run.get("workflow_path") or str(settings.comfyui_workflow_path),
            fps=int(run.get("fps") or 24),
            progress_include=["待抽帧"],
            progress_exclude=[],
            notify_interval_seconds=int(run.get("notify_interval_seconds") or 60),
            run_cherry=False,
            fake_matting_from_frames=bool(run.get("fake_matting_from_frames")),
            priority_characters=run.get("priority_characters") or None,
        )
        run.setdefault("children", {})["pipeline_run_id"] = pipeline["run_id"]
        _save(run)
        while True:
            current_run = _load(run_id)
            if not current_run or current_run.get("status") == "CANCELED":
                return
            status = pipeline_status(pipeline["run_id"])
            stage = {"frame": "frame_extract", "comfyui": "matting"}.get(str(status.get("current_step") or ""), "frame_extract")
            _mark(current_run, "RUNNING", stage)
            if status.get("status") in {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED"}:
                if status.get("status") not in {"DONE", "DONE_WITH_ERRORS"}:
                    _fail(current_run, f"pipeline failed: {status.get('error') or status.get('status')}")
                    return
                run = current_run
                break
            time.sleep(5)

        if not _run_cherry_smooth_stage(run_id, root):
            return
        run = _load(run_id)
        if not run:
            return

        _mark(run, "RUNNING", "unity_ready")
        run.setdefault("children", {})["unity_ready_report"] = build_unity_ready(root, overwrite=True, copy_mode="copy")
        _save(run)
        _notify(run, _format_unity_ready_stage(run["children"]["unity_ready_report"], root / "unity_ready"))

        _mark(run, "RUNNING", "unity_import")
        from assetclaw_matting.skills.unity_import_skills import run_import

        _notify(run, f"步骤6 Unity {'资源迭代/替换' if (run.get('unity_import_mode') == 'iteration') else '新导入'}开始：{run['unity_ready']}")
        unity = run_import(
            run["unity_ready"],
            unity_project=run["unity_project"],
            package=run.get("package") or "both",
            mode=run.get("unity_import_mode") or "import",
            timeout_seconds=3600,
        )
        run.setdefault("children", {})["unity_import"] = unity
        _save(run)
        if not unity.get("ok"):
            _block(run, _format_unity_import_issue(unity), "unity_import")
            return
        _notify(run, _format_unity_import_stage(unity))

        _run_p4_shelve_stage(run, root)
    except Exception as exc:
        run = _load(run_id)
        if run:
            _fail(run, str(exc))
    finally:
        if token is not None:
            reset_runtime_context(token)
        latest = _load(run_id)
        if latest and int(latest.get("worker_pid") or 0) == os.getpid():
            latest["worker_pid"] = 0
            _save(latest)
        _WORKERS.discard(run_id)


def _resume_worker(run_id: str) -> None:
    token = None
    try:
        run = _load(run_id)
        if not run:
            return
        if run.get("chat_id"):
            token = set_runtime_context(channel="feishu", chat_id=run["chat_id"])
        root = Path(run["date_root"])
        unity = ((run.get("children") or {}).get("unity_import") or {})
        recovered = _recover_late_unity_import(unity)
        if not recovered.get("ok"):
            _block(run, "Unity 导入还没有可确认的完成结果，已停止继续 P4。", "unity_import")
            return
        run.setdefault("children", {})["unity_import"] = recovered
        run["error"] = ""
        _save(run)
        _notify(run, _format_unity_import_stage(recovered))
        _run_p4_shelve_stage(run, root)
    except Exception as exc:
        run = _load(run_id)
        if run:
            _fail(run, str(exc))
    finally:
        if token is not None:
            reset_runtime_context(token)
        latest = _load(run_id)
        if latest and int(latest.get("worker_pid") or 0) == os.getpid():
            latest["worker_pid"] = 0
            _save(latest)
        _WORKERS.discard(run_id)


def _run_p4_shelve_stage(run: dict[str, Any], root: Path) -> None:
    _mark(run, "RUNNING", "p4_shelve")
    if not run.get("allow_p4_writes", True):
        _block(run, "P4 stage is waiting for allow_p4_writes=true.", "p4_shelve")
        return

    from assetclaw_matting.skills import p4_skills

    p4 = run.get("p4") or {}
    workflow = p4.get("workflow") or None
    workspace = p4.get("workspace") or None
    stream = p4.get("stream") or "//streams/rel_0.0.1"
    target_paths = _unity_ready_p4_paths(root / "unity_ready", run.get("unity_import_mode") or "import")
    desc = f"[Shelve-only] Animation UI {run.get('unity_import_mode') or 'import'} {root.name} ({run['id']})"
    _notify(run, "步骤7 P4 开始：switch/get latest/check/preview/create CL/reconcile/shelve/report\nSubmit：disabled")
    switched = p4_skills.switch_stream(workflow=workflow, workspace=workspace, stream=stream, preview=False, yes=True)
    if not switched.get("ok"):
        run.setdefault("children", {})["p4"] = {"switch_stream": switched}
        _save(run)
        _fail(run, f"P4 switch stream failed: {switched.get('error')}")
        return
    _notify(run, f"步骤7 P4 stream 已切换：{stream}")
    latest = p4_skills.get_latest(workflow=workflow, workspace=workspace, scope="managed", yes=True)
    _notify(run, f"步骤7 P4 get latest 完成：managed scope\nok={latest.get('ok')}")
    check = p4_skills.check(workflow=workflow, workspace=workspace)
    if not check.get("ok"):
        run.setdefault("children", {})["p4"] = {"switch_stream": switched, "get_latest": latest, "check": check}
        _save(run)
        _fail(run, f"P4 check failed: {check.get('error') or check.get('safety', {}).get('errors')}")
        return
    _notify(run, "步骤7 P4 安全检查通过")
    preview = p4_skills.preview(workflow=workflow, workspace=workspace, paths=target_paths)
    if not preview.get("ok"):
        run.setdefault("children", {})["p4"] = {"switch_stream": switched, "get_latest": latest, "check": check, "preview": preview}
        _save(run)
        _fail(run, f"P4 preview failed: {preview.get('error') or preview.get('safety', {}).get('errors')}")
        return
    _notify(run, _format_p4_preview_stage(preview, target_paths))
    created = p4_skills.create_cl(workflow=workflow, workspace=workspace, desc=desc, yes=True)
    if not created.get("ok"):
        run.setdefault("children", {})["p4"] = {"switch_stream": switched, "get_latest": latest, "check": check, "preview": preview, "create_cl": created}
        _save(run)
        _fail(run, f"P4 create changelist failed: {created.get('error')}")
        return
    cl = created.get("changelist_id") or created.get("cl") or created.get("changelist")
    _notify(run, f"步骤7 P4 changelist 已创建：{cl}")
    reconciled = p4_skills.reconcile(workflow=workflow, workspace=workspace, cl=cl, paths=target_paths, yes=True)
    if not reconciled.get("ok"):
        run.setdefault("children", {})["p4"] = {"switch_stream": switched, "get_latest": latest, "check": check, "preview": preview, "create_cl": created, "reconcile": reconciled}
        _save(run)
        _fail(run, f"P4 reconcile failed: {reconciled.get('error') or reconciled.get('safety', {}).get('errors')}")
        return
    _notify(run, _format_p4_reconcile_stage(reconciled, cl))
    shelved = p4_skills.shelve(workflow=workflow, workspace=workspace, cl=cl, force=True, yes=True)
    if not shelved.get("ok"):
        run.setdefault("children", {})["p4"] = {"switch_stream": switched, "get_latest": latest, "check": check, "preview": preview, "create_cl": created, "reconcile": reconciled, "shelve": shelved}
        _save(run)
        _fail(run, f"P4 shelve failed: {shelved.get('error') or shelved.get('safety', {}).get('errors')}")
        return
    _notify(run, f"步骤7 P4 shelve 完成：CL/Shelf {cl}")
    report = p4_skills.report(
        workflow=workflow,
        workspace=workspace,
        cl=cl,
        unity_ready_manifest=str(root / "unity_ready" / "manifest.json"),
    )
    run.setdefault("children", {})["p4"] = {
        "switch_stream": switched,
        "get_latest": latest,
        "check": check,
        "preview": preview,
        "create_cl": created,
        "reconcile": reconciled,
        "shelve": shelved,
        "report": report,
        "changelist_id": str(cl),
        "target_paths": target_paths,
    }
    _mark(run, "DONE", "p4_shelve")
    _notify(run, f"完整动画自动化流程完成：{run['id']}\nCL/Shelf：{cl}\nSubmit：disabled")


def _run_cherry_smooth_stage(run_id: str, root: Path) -> bool:
    from assetclaw_matting.skills.cherry_skills import run_start as cherry_start, run_status as cherry_status

    run = _load(run_id)
    if not run:
        return False
    _mark(run, "RUNNING", "cherry_smooth")
    routes = _cherry_routes(root)
    if not routes:
        _fail(run, f"Cherry smooth failed: no matte png found under {root}")
        return False
    run.setdefault("children", {})["cherry_run_ids"] = []
    _save(run)
    for label, matte_dir, smooth_dir in routes:
        current_run = _load(run_id)
        if not current_run or current_run.get("status") == "CANCELED":
            return False
        _notify(current_run, f"步骤4 Cherry 平滑后处理开始：{label}\n输入：{matte_dir}\n输出：{smooth_dir}\n时序 Alpha 平滑：默认关闭")
        cherry = cherry_start(
            input_dir=str(matte_dir),
            output_dir=str(smooth_dir),
            recursive=True,
            max_images=50000,
            skip_existing=False,
            notify_interval_seconds=int(current_run.get("notify_interval_seconds") or 60),
            use_smooth=False,
        )
        current_run.setdefault("children", {}).setdefault("cherry_run_ids", []).append(cherry["run_id"])
        current_run["children"]["cherry_run_id"] = cherry["run_id"]
        _save(current_run)
        while True:
            latest = _load(run_id)
            if not latest or latest.get("status") == "CANCELED":
                return False
            status = cherry_status(cherry["run_id"], include_gpu=False)
            _mark(latest, "RUNNING", "cherry_smooth")
            if status.get("status") in {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED"}:
                if status.get("status") not in {"DONE", "DONE_WITH_ERRORS"}:
                    _fail(latest, f"Cherry smooth failed: {status.get('error') or status.get('status')}")
                    return False
                _notify(latest, _format_cherry_stage(status, label))
                break
            time.sleep(5)
    return True


def _cherry_routes(root: Path) -> list[tuple[str, Path, Path]]:
    routes: list[tuple[str, Path, Path]] = []
    for asset_kind in ASSET_KINDS:
        matte_dir = root / asset_kind / "matte"
        if matte_dir.is_dir() and any(matte_dir.rglob("*.png")):
            routes.append((asset_kind, matte_dir, root / asset_kind / "smooth"))
    if not routes and (root / "matte").is_dir() and any((root / "matte").rglob("*.png")):
        routes.append(("default", root / "matte", root / "smooth"))
    return routes


def _date_root(date_root: str | None) -> Path:
    if date_root:
        return validate_path(date_root, must_exist=False)
    return validate_path(default_automation_paths()["workspace_root"], must_exist=False)


def _production_workflow_path(workflow_path: str | None) -> Path:
    # The full animation flow must be deterministic: it should not inherit a
    # conversation-scoped comfyui.workflow_select value from an earlier manual run.
    return validate_path(workflow_path or str(settings.comfyui_workflow_path), must_exist=True)


def _normalize_unity_import_mode(value: str | None) -> str:
    raw = (value or "import").strip().lower()
    if raw in {"import", "new", "batch", "导入", "新导入", "批量导入"}:
        return "import"
    if raw in {"iteration", "iterate", "replace", "replacement", "update", "iter", "迭代", "资源迭代", "替换", "贴图迭代", "高清化"}:
        return "iteration"
    raise ValueError("unity_import_mode/import_mode must be import or iteration")


def _p4_plan(workflow: str | None, workspace: str | None, root: Path, stream: str = "//streams/rel_0.0.1", mode: str = "import") -> dict[str, Any]:
    desc = f"[Shelve-only] Animation UI import {root.name}"
    return {
        "workflow": workflow or "",
        "workspace": workspace or "",
        "stream": stream,
        "unity_import_mode": mode,
        "submit": "disabled",
        "next_steps": [
            {"skill": "p4.switch_stream", "arguments": {"workflow": workflow, "workspace": workspace, "stream": stream}},
            {"skill": "p4.get_latest", "arguments": {"workflow": workflow, "workspace": workspace, "scope": "managed"}},
            {"skill": "p4.check", "arguments": {"workflow": workflow, "workspace": workspace}},
            {"skill": "p4.preview", "arguments": {"workflow": workflow, "workspace": workspace, "paths": "<unity_ready-target-paths>"}},
            {"skill": "p4.create_cl", "arguments": {"workflow": workflow, "workspace": workspace, "desc": desc}},
            {"skill": "p4.reconcile", "arguments": {"workflow": workflow, "workspace": workspace, "cl": "<CL>", "paths": "<unity_ready-target-paths>"}},
            {"skill": "p4.shelve", "arguments": {"workflow": workflow, "workspace": workspace, "cl": "<CL>"}},
            {"skill": "p4.report", "arguments": {"workflow": workflow, "workspace": workspace, "cl": "<CL>", "unity_ready_manifest": str(root / "unity_ready" / "manifest.json")}},
        ],
    }


def _unity_ready_p4_paths(unity_ready: Path, mode: str = "import") -> list[str]:
    paths: set[str] = set()
    for task in _manifest_tasks(unity_ready / "scene" / "animation_resource_manifest.json"):
        character = _title_name(str(task.get("character") or task.get("name") or ""))
        if character:
            if mode == "iteration":
                paths.add(f"Assets/Art/UI/SpritesAnim/CharacterAnim/{character}/Common/...")
            else:
                paths.add(f"Assets/Art/UI/SpritesAnim/CharacterAnim/{character}/charImproting/...")
    for package_name, force_story in (("emoji", False), ("story", True)):
        manifest = unity_ready / package_name / "animation_resource_manifest.json"
        for task in _manifest_tasks(manifest):
            _add_emoji_p4_paths(paths, task, mode, force_story=force_story)
    return sorted(paths)


def _add_emoji_p4_paths(paths: set[str], task: dict[str, Any], mode: str, *, force_story: bool = False) -> None:
    character = _title_name(str(task.get("character") or task.get("name") or ""))
    animation = str(task.get("animation") or task.get("anim") or "idle").lower()
    types = [str(item).lower() for item in (task.get("unityCategories") or task.get("types") or [])]
    is_story = force_story or any("剧情" in item or "story" in item or "chat" in item for item in types)
    if character:
        if mode == "iteration":
            paths.add(f"Assets/Art/UI/SpritesAnim/Emoji/{character}/{'Chat' if is_story else 'Common'}/...")
        else:
            paths.add(f"Assets/Art/UI/SpritesAnim/Emoji/{character}/importing_{animation}/...")
            lower = character.lower()
            paths.add(f"Assets/Art/UI/Animation/Emoji/{character}/anui_emoji_{lower}_{animation}.anim")
            paths.add(f"Assets/Art/UI/Animation/Emoji/{character}/anui_emoji_{lower}_{animation}.anim.meta")
            paths.add(f"Assets/Res/UI/Animator/EmojiOverride/coui_chatemoji_{lower}.overrideController")
            paths.add(f"Assets/Res/UI/Animator/EmojiOverride/coui_chatemoji_{lower}.overrideController.meta")


def _manifest_tasks(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("items"), dict):
        tasks: list[dict[str, Any]] = []
        for character, animations in data["items"].items():
            if not isinstance(animations, dict):
                continue
            for animation, meta in animations.items():
                meta = meta if isinstance(meta, dict) else {}
                tasks.append(
                    {
                        "character": character,
                        "animation": animation,
                        "types": meta.get("types") or [],
                        "unityCategories": meta.get("types") or [],
                    }
                )
        return tasks
    tasks = data.get("tasks") if isinstance(data, dict) else data
    return [item for item in (tasks or []) if isinstance(item, dict)]


def _format_cherry_stage(status: dict[str, Any], label: str = "") -> str:
    lines = [
        f"步骤4 Cherry 平滑后处理完成{('：' + label) if label else '：'}{status.get('completed', 0)}/{status.get('total', 0)} 张，失败 {status.get('failed', 0)}",
        f"输出：{status.get('output_dir')}",
    ]
    if status.get("last_completed"):
        lines.append(f"最后处理：{status.get('last_completed')}")
    if status.get("error"):
        lines.append(f"错误：{status.get('error')}")
    return "\n".join(lines)


def _format_unity_ready_stage(report: dict[str, Any], ready_root: Path) -> str:
    lines = ["步骤5 unity_ready 完成："]
    for package_key, label in (("scene", "Scene"), ("emoji", "Emoji"), ("story", "Story")):
        package = (report.get("packages") or {}).get(package_key) or {}
        tasks = package.get("tasks") or []
        frames = sum(int(item.get("frameCount") or 0) for item in tasks)
        lines.append(f"{label}：任务 {len(tasks)}，图片 {frames}")
    warnings = report.get("warnings") or []
    if warnings:
        lines.append(f"Warnings：{len(warnings)} 条，首条：{warnings[0]}")
    lines.append(f"输出：{ready_root}")
    return "\n".join(lines)


def _format_unity_import_stage(payload: dict[str, Any]) -> str:
    mode = payload.get("mode") or payload.get("import_mode") or "import"
    mode_text = "资源迭代/替换" if mode == "iteration" else "新导入"
    result = payload.get("result") or {}
    inferred = bool(result.get("inferredFromDisk") or payload.get("message", "").startswith("Unity result file was late"))
    lines = [f"步骤6 Unity {mode_text}完成{'（磁盘主动确认）' if inferred else ''}："]
    packages = result.get("packages") or payload.get("packages") or []
    for package in packages:
        name = package.get("package") or package.get("name") or "package"
        imported = package.get("importedTextures")
        replaced = package.get("replacedTextures")
        skipped = package.get("skippedTextures")
        task_count = package.get("tasksProcessed") or package.get("task_count") or package.get("tasks") or package.get("taskOk")
        frame_count = package.get("textures") or package.get("frame_count")
        if mode == "iteration":
            lines.append(f"- {name}：任务 {task_count or 0}，替换 {replaced or 0}，跳过 {skipped or 0}")
        else:
            lines.append(f"- {name}：任务 {task_count or 0}，图片 {imported if imported is not None else (frame_count or 0)}")
    if inferred:
        lines.append("说明：Unity result 未及时回传，已按目标资源更新时间确认完成。")
    if result.get("error") or payload.get("error"):
        lines.append(f"错误：{result.get('error') or payload.get('error')}")
    return "\n".join(lines)


def _recover_late_unity_import(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("ok"):
        return payload
    result_path = payload.get("result_path") or ""
    if not result_path:
        return payload
    path = Path(result_path)
    if not path.is_file():
        return payload
    try:
        result = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        recovered = dict(payload)
        recovered["late_result_error"] = str(exc)
        return recovered
    recovered = dict(payload)
    recovered["late_result"] = True
    recovered["result"] = result
    recovered["ok"] = bool(result.get("ok"))
    recovered["error"] = result.get("error") or ("" if recovered["ok"] else payload.get("error") or "unity_result_failed")
    if recovered["ok"]:
        recovered["message"] = "Unity result arrived after Python timeout; resume can continue from P4."
    return recovered


def _unity_import_totals(result: dict[str, Any]) -> dict[str, int]:
    totals = {"tasks": 0, "textures": 0, "replaced": 0, "skipped": 0}
    for package in result.get("packages") or []:
        totals["tasks"] += int(package.get("tasksProcessed") or package.get("tasks") or 0)
        totals["textures"] += int(package.get("textures") or package.get("totalTextures") or package.get("replacedTextures") or 0)
        totals["replaced"] += int(package.get("replacedTextures") or package.get("textures") or 0)
        totals["skipped"] += int(package.get("skippedTextures") or 0)
    return totals


def _format_unity_import_issue(payload: dict[str, Any]) -> str:
    error = payload.get("error") or "unknown"
    if error != "unity_runner_timeout":
        return f"Unity import paused: {error}"
    disk = payload.get("disk_progress") or {}
    status = payload.get("latest_status") or {}
    parts = ["Unity 导入暂停：等待超时，未能确认完成。"]
    if disk.get("supported"):
        parts.append(
            "磁盘进度："
            f"{int(disk.get('replacedTextures') or 0)}/{int(disk.get('replaceableTextures') or 0)} 已更新，"
            f"跳过 {int(disk.get('skippedTextures') or 0)}。"
        )
    if status:
        phase = status.get("phase") or "unknown"
        package = status.get("package") or ""
        character = status.get("character") or ""
        tail = " / ".join(item for item in (str(package), str(character)) if item)
        parts.append(f"Unity 最后状态：{phase}{('，' + tail) if tail else ''}。")
    else:
        parts.append("Unity 未写入状态文件，通常是 runner 未被及时编译/触发；检查 Auto Refresh、编译状态和 MCP。")
    request = payload.get("request")
    if request:
        parts.append(f"请求：{request}")
    return "\n".join(parts)


def _format_p4_preview_stage(payload: dict[str, Any], target_paths: list[str]) -> str:
    counts = _p4_counts(payload)
    lines = [
        "步骤7 P4 preview 完成：",
        f"目标路径：{len(target_paths)} 条",
        f"预览数量：add {counts.get('add', 0)} / edit {counts.get('edit', 0)} / delete {counts.get('delete', 0)} / other {counts.get('other', 0)}",
    ]
    return "\n".join(lines)


def _format_p4_reconcile_stage(payload: dict[str, Any], cl: Any) -> str:
    counts = _p4_counts(payload)
    return (
        f"步骤7 P4 reconcile 完成：CL {cl}\n"
        f"add {counts.get('add', 0)} / edit {counts.get('edit', 0)} / delete {counts.get('delete', 0)} / other {counts.get('other', 0)}"
    )


def _p4_counts(payload: dict[str, Any]) -> dict[str, int]:
    counts = {"add": 0, "edit": 0, "delete": 0, "other": 0}
    candidates = []
    for key in ("files", "items", "opened", "results", "actions"):
        value = payload.get(key)
        if isinstance(value, list):
            candidates.extend(value)
    summary = payload.get("summary")
    if isinstance(summary, dict):
        for key in counts:
            if isinstance(summary.get(key), int):
                counts[key] += int(summary[key])
    for item in candidates:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or item.get("status") or item.get("operation") or "").lower()
        if "add" in action:
            counts["add"] += 1
        elif "edit" in action or "update" in action:
            counts["edit"] += 1
        elif "delete" in action or "remove" in action:
            counts["delete"] += 1
        else:
            counts["other"] += 1
    return counts


def _title_name(value: str) -> str:
    clean = value.strip()
    return clean[:1].upper() + clean[1:] if clean else ""


def _stage_payload(active: str | None = None) -> list[dict[str, str]]:
    seen_active = active is None
    result = []
    for key, label in STAGES:
        if key == active:
            result.append({"key": key, "label": label, "status": "running"})
            seen_active = True
        else:
            result.append({"key": key, "label": label, "status": "pending" if seen_active else "done"})
    return result


def _public(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": run.get("status"),
        "current_stage": run.get("current_stage"),
        "date_root": run.get("date_root"),
        "unity_ready": run.get("unity_ready"),
        "workflow_path": run.get("workflow_path") or "",
        "workflow_name": run.get("workflow_name") or (Path(run["workflow_path"]).name if run.get("workflow_path") else ""),
        "pipeline_notice": run.get("pipeline_notice") or "",
        "unity_project": run.get("unity_project"),
        "unity_import_mode": run.get("unity_import_mode") or "import",
        "priority_characters": run.get("priority_characters") or [],
        "stages": run.get("stages") or [],
        "children": run.get("children") or {},
        "error": run.get("error") or "",
        "p4": run.get("p4") or {},
        "p4_submit": "disabled",
    }


def _path(run_id: str) -> Path:
    return RUN_DIR / f"{run_id}.json"


def _load(run_id: str | None = None) -> dict[str, Any] | None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    if run_id:
        path = _path(run_id)
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    paths = sorted(RUN_DIR.glob("AFLOW_*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    return json.loads(paths[0].read_text(encoding="utf-8")) if paths else None


def _save(run: dict[str, Any]) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    run["updated_at"] = _now()
    _path(run["id"]).write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")


def _mark(run: dict[str, Any], status: str, stage: str) -> None:
    run["status"] = status
    run["current_stage"] = stage
    run["stages"] = [{"key": key, "label": label, "status": "done"} for key, label in STAGES] if status == "DONE" else _stage_payload(stage)
    _save(run)


def _block(run: dict[str, Any], message: str, stage: str) -> None:
    run["status"] = "BLOCKED"
    run["current_stage"] = stage
    run["error"] = message
    run["stages"] = _stage_payload(stage)
    _save(run)
    _notify(run, f"动画自动化流程暂停：{message}")


def _fail(run: dict[str, Any], message: str) -> None:
    run["status"] = "FAILED"
    run["error"] = message
    _save(run)
    _notify(run, f"动画自动化流程失败：{message}")


def _start_worker(run_id: str) -> None:
    if run_id in _WORKERS:
        return
    _WORKERS.add(run_id)
    threading.Thread(target=_worker, args=(run_id,), daemon=True).start()


def _start_resume_worker(run_id: str) -> None:
    if run_id in _WORKERS:
        return
    _WORKERS.add(run_id)
    threading.Thread(target=_resume_worker, args=(run_id,), daemon=True).start()


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, SystemError, ValueError):
        return False


def _notify(run: dict[str, Any], text: str) -> None:
    chat_id = run.get("chat_id")
    if not chat_id:
        return
    from assetclaw_matting.services.notification_service import send_text

    send_text(chat_id, text)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
