from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from assetclaw_matting.runtime_context import get_runtime_context, reset_runtime_context, set_runtime_context
from assetclaw_matting.skills.frame_skills import default_automation_paths
from assetclaw_matting.skills.security import validate_path
from tools.animation_automation.core import build_unity_ready


RUN_DIR = Path("E:/assetclaw-matting-bot/storage/animation_flow_runs")
STAGES = [
    ("feishu_download", "1 飞书文档/表格下载视频"),
    ("frame_extract", "2 抽帧"),
    ("matting", "3 抠图"),
    ("cherry", "4 Cherry 后处理"),
    ("unity_ready", "5 unity_ready 整理"),
    ("unity_import", "6 Unity 插件导入引擎"),
    ("p4_shelve", "7 P4 reconcile/changelist/shelve/report"),
]
_WORKERS: set[str] = set()


def run_preview(
    date_root: str | None = None,
    unity_project: str = "D:/Spark/Client",
    p4_workflow: str | None = None,
    p4_workspace: str | None = None,
    p4_stream: str = "//streams/rel_0.0.1",
    package: str = "both",
    **_: Any,
) -> dict[str, Any]:
    root = _date_root(date_root)
    return {
        "ok": True,
        "date_root": str(root),
        "unity_ready": str(root / "unity_ready"),
        "unity_project": str(validate_path(unity_project, must_exist=False)),
        "package": package,
        "stages": _stage_payload(),
        "p4": _p4_plan(p4_workflow, p4_workspace, root, p4_stream),
    }


def run_start(
    date_root: str | None = None,
    workflow_path: str | None = None,
    unity_project: str = "D:/Spark/Client",
    p4_workflow: str | None = None,
    p4_workspace: str | None = None,
    p4_stream: str = "//streams/rel_0.0.1",
    package: str = "both",
    fps: int = 24,
    notify_interval_seconds: int = 60,
    allow_p4_writes: bool = True,
    **_: Any,
) -> dict[str, Any]:
    preview = run_preview(date_root, unity_project, p4_workflow, p4_workspace, p4_stream, package)
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
        "workflow_path": workflow_path or "",
        "fps": int(fps),
        "notify_interval_seconds": max(30, min(int(notify_interval_seconds), 3600)),
        "allow_p4_writes": bool(allow_p4_writes),
        "p4": preview["p4"],
        "chat_id": (ctx.get("chat_id") or "") if ctx.get("channel") == "feishu" else "",
        "stages": _stage_payload("feishu_download"),
        "children": {},
        "error": "",
    }
    _save(run)
    _notify(run, "完整动画自动化流程已启动：1-7 步会按顺序推进，P4 submit 永远禁用。")
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


def run_cancel(run_id: str | None = None, **_: Any) -> dict[str, Any]:
    run = _load(run_id)
    if not run:
        return {"ok": False, "error": "animation flow run not found"}
    pipeline_id = (run.get("children") or {}).get("pipeline_run_id")
    if pipeline_id:
        from assetclaw_matting.skills.pipeline_skills import run_cancel as pipeline_cancel

        pipeline_cancel(pipeline_id)
    _mark(run, "CANCELED", run.get("current_stage") or "feishu_download")
    return {"ok": True, "run_id": run["id"], "status": "CANCELED"}


def preview_run_start_confirmation(arguments: dict[str, Any], confirmation_id: str) -> str:
    preview = run_preview(**arguments)
    lines = ["请确认是否开始完整动画自动化流程：", f"工作区：{preview['date_root']}", "步骤："]
    lines.extend(f"- {stage['label']}" for stage in preview["stages"])
    lines.extend(
        [
            f"Unity Project：{preview['unity_project']}",
            "P4：第 7 步会执行 create CL / reconcile / shelve / report；submit disabled。",
            f"回复：确认执行 {confirmation_id}",
        ]
    )
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
            workflow_path=run.get("workflow_path") or None,
            fps=int(run.get("fps") or 24),
            notify_interval_seconds=int(run.get("notify_interval_seconds") or 60),
        )
        run.setdefault("children", {})["pipeline_run_id"] = pipeline["run_id"]
        _save(run)
        while True:
            current_run = _load(run_id)
            if not current_run or current_run.get("status") == "CANCELED":
                return
            status = pipeline_status(pipeline["run_id"])
            stage = {"frame": "frame_extract", "comfyui": "matting", "cherry": "cherry"}.get(str(status.get("current_step") or ""), "frame_extract")
            _mark(current_run, "RUNNING", stage)
            if status.get("status") in {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED"}:
                if status.get("status") not in {"DONE", "DONE_WITH_ERRORS"}:
                    _fail(current_run, f"pipeline failed: {status.get('error') or status.get('status')}")
                    return
                run = current_run
                break
            time.sleep(5)

        _mark(run, "RUNNING", "unity_ready")
        run.setdefault("children", {})["unity_ready_report"] = build_unity_ready(root, overwrite=True, copy_mode="copy")
        _save(run)

        _mark(run, "RUNNING", "unity_import")
        from assetclaw_matting.skills.unity_import_skills import run_import

        unity = run_import(run["unity_ready"], unity_project=run["unity_project"], package=run.get("package") or "both")
        run.setdefault("children", {})["unity_import"] = unity
        _save(run)
        if not unity.get("ok"):
            _block(run, f"Unity import paused: {unity.get('error')}", "unity_import")
            return

        _mark(run, "RUNNING", "p4_shelve")
        if not run.get("allow_p4_writes", True):
            _block(run, "P4 stage is waiting for allow_p4_writes=true.", "p4_shelve")
            return

        from assetclaw_matting.skills import p4_skills

        p4 = run.get("p4") or {}
        workflow = p4.get("workflow") or None
        workspace = p4.get("workspace") or None
        stream = p4.get("stream") or "//streams/rel_0.0.1"
        target_paths = _unity_ready_p4_paths(root / "unity_ready")
        desc = f"[Shelve-only] Animation UI import {root.name} ({run['id']})"
        switched = p4_skills.switch_stream(workflow=workflow, workspace=workspace, stream=stream, preview=False, yes=True)
        if not switched.get("ok"):
            run.setdefault("children", {})["p4"] = {"switch_stream": switched}
            _save(run)
            _fail(run, f"P4 switch stream failed: {switched.get('error')}")
            return
        latest = p4_skills.get_latest(workflow=workflow, workspace=workspace, scope="managed", yes=True)
        check = p4_skills.check(workflow=workflow, workspace=workspace)
        if not check.get("ok"):
            run.setdefault("children", {})["p4"] = {"switch_stream": switched, "get_latest": latest, "check": check}
            _save(run)
            _fail(run, f"P4 check failed: {check.get('error') or check.get('safety', {}).get('errors')}")
            return
        preview = p4_skills.preview(workflow=workflow, workspace=workspace, paths=target_paths)
        if not preview.get("ok"):
            run.setdefault("children", {})["p4"] = {"switch_stream": switched, "get_latest": latest, "check": check, "preview": preview}
            _save(run)
            _fail(run, f"P4 preview failed: {preview.get('error') or preview.get('safety', {}).get('errors')}")
            return
        created = p4_skills.create_cl(workflow=workflow, workspace=workspace, desc=desc, yes=True)
        if not created.get("ok"):
            run.setdefault("children", {})["p4"] = {"switch_stream": switched, "get_latest": latest, "check": check, "preview": preview, "create_cl": created}
            _save(run)
            _fail(run, f"P4 create changelist failed: {created.get('error')}")
            return
        cl = created.get("changelist_id") or created.get("cl") or created.get("changelist")
        reconciled = p4_skills.reconcile(workflow=workflow, workspace=workspace, cl=cl, paths=target_paths, yes=True)
        if not reconciled.get("ok"):
            run.setdefault("children", {})["p4"] = {"switch_stream": switched, "get_latest": latest, "check": check, "preview": preview, "create_cl": created, "reconcile": reconciled}
            _save(run)
            _fail(run, f"P4 reconcile failed: {reconciled.get('error') or reconciled.get('safety', {}).get('errors')}")
            return
        shelved = p4_skills.shelve(workflow=workflow, workspace=workspace, cl=cl, force=True, yes=True)
        if not shelved.get("ok"):
            run.setdefault("children", {})["p4"] = {"switch_stream": switched, "get_latest": latest, "check": check, "preview": preview, "create_cl": created, "reconcile": reconciled, "shelve": shelved}
            _save(run)
            _fail(run, f"P4 shelve failed: {shelved.get('error') or shelved.get('safety', {}).get('errors')}")
            return
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
    except Exception as exc:
        run = _load(run_id)
        if run:
            _fail(run, str(exc))
    finally:
        if token is not None:
            reset_runtime_context(token)
        _WORKERS.discard(run_id)


def _date_root(date_root: str | None) -> Path:
    if date_root:
        return validate_path(date_root, must_exist=False)
    return validate_path(default_automation_paths()["workspace_root"], must_exist=False)


def _p4_plan(workflow: str | None, workspace: str | None, root: Path, stream: str = "//streams/rel_0.0.1") -> dict[str, Any]:
    desc = f"[Shelve-only] Animation UI import {root.name}"
    return {
        "workflow": workflow or "",
        "workspace": workspace or "",
        "stream": stream,
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


def _unity_ready_p4_paths(unity_ready: Path) -> list[str]:
    paths: set[str] = set()
    scene_manifest = unity_ready / "scene" / "animation_resource_manifest.json"
    emoji_manifest = unity_ready / "emoji" / "animation_resource_manifest.json"
    for task in _manifest_tasks(scene_manifest):
        character = _title_name(str(task.get("character") or task.get("name") or ""))
        if character:
            paths.add(f"Assets/Art/UI/SpritesAnim/CharacterAnim/{character}/charImproting/...")
    for task in _manifest_tasks(emoji_manifest):
        character = _title_name(str(task.get("character") or task.get("name") or ""))
        animation = str(task.get("animation") or task.get("anim") or "idle").lower()
        if character:
            paths.add(f"Assets/Art/UI/SpritesAnim/Emoji/{character}/importing_{animation}/...")
            lower = character.lower()
            paths.add(f"Assets/Art/UI/Animation/Emoji/{character}/anui_emoji_{lower}_{animation}.anim")
            paths.add(f"Assets/Art/UI/Animation/Emoji/{character}/anui_emoji_{lower}_{animation}.anim.meta")
            paths.add(f"Assets/Res/UI/Animator/EmojiOverride/coui_chatemoji_{lower}.overrideController")
            paths.add(f"Assets/Res/UI/Animator/EmojiOverride/coui_chatemoji_{lower}.overrideController.meta")
    return sorted(paths)


def _manifest_tasks(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    tasks = data.get("tasks") if isinstance(data, dict) else data
    return [item for item in (tasks or []) if isinstance(item, dict)]


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
        "unity_project": run.get("unity_project"),
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
    run["stages"] = _stage_payload(stage)
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


def _notify(run: dict[str, Any], text: str) -> None:
    chat_id = run.get("chat_id")
    if not chat_id:
        return
    from assetclaw_matting.services.notification_service import send_text

    send_text(chat_id, text)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
