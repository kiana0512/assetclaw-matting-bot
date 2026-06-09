from __future__ import annotations

import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ALLOWED_STEP_SKILLS = {
    "feishu_table.export_json",
    "frame.run_preview",
    "frame.run_start",
    "frame.run_status",
    "comfyui.run_preview",
    "comfyui.run_start",
    "comfyui.run_status",
    "comfyui.run_update",
    "cherry.run_preview",
    "cherry.run_start",
    "cherry.run_status",
    "pipeline.run_preview",
    "pipeline.run_start",
    "p4.status",
    "p4.check",
    "p4.preview",
    "p4.report",
    "p4.shelve_ui_import",
    "speech.transcribe",
    "speech.synthesize",
    "memory.context_pack",
}

_RUN_THREADS: set[str] = set()
_CANCEL_REQUESTS: set[str] = set()


def module_catalog() -> dict[str, Any]:
    return {
        "ok": True,
        "modules": [
            {
                "id": "feishu_table",
                "name": "飞书表格读取",
                "skills": ["feishu_table.export_json"],
                "parameters": {
                    "table_url": "飞书多维表格 URL，可留空使用默认配置",
                    "output_path": "表格导出 JSON 路径",
                    "config_path": "飞书工具配置路径",
                },
            },
            {
                "id": "frame",
                "name": "抽帧 + 剔除关键帧",
                "skills": ["frame.run_preview", "frame.run_start", "frame.run_status"],
                "parameters": {
                    "download_dir": "视频下载目录",
                    "export_dir": "抽帧输出目录",
                    "fps": "抽帧 FPS",
                    "max_frames": "最多帧数，0 表示不限",
                    "diff_threshold": "关键帧/去重差异阈值",
                    "dedup_enabled": "是否启用去重/剔关键帧",
                    "dedup_renumber": "剔除后是否重新编号",
                    "notify_interval_seconds": "通知间隔秒",
                },
            },
            {
                "id": "comfyui",
                "name": "ComfyUI 抠图管线",
                "skills": ["comfyui.run_preview", "comfyui.run_start", "comfyui.run_update", "comfyui.run_status"],
                "parameters": {
                    "workflow_path": "工作流 JSON 路径",
                    "input_dir": "输入图片目录",
                    "output_dir": "输出目录",
                    "input_node_id": "指定 LoadImage 节点 ID",
                    "input_name": "节点输入字段名",
                    "max_images": "最多处理图片数",
                    "recursive": "递归子目录",
                    "preserve_structure": "保留目录结构",
                    "skip_existing": "跳过已有输出",
                    "notify_interval_seconds": "通知间隔秒",
                },
            },
            {
                "id": "cherry",
                "name": "Cherry 时序平滑",
                "skills": ["cherry.run_preview", "cherry.run_start", "cherry.run_status"],
                "parameters": {
                    "input_dir": "输入目录",
                    "output_dir": "输出目录",
                    "recursive": "递归子目录",
                    "skip_existing": "跳过已有输出",
                    "max_images": "最多处理图片数",
                    "use_denoise": "启用 Alpha 去噪",
                    "denoise_threshold": "去噪阈值",
                    "denoise_radius": "去噪半径",
                    "use_smooth": "启用时序平滑",
                    "smooth_window": "平滑窗口",
                    "smooth_sigma": "平滑强度",
                    "min_alpha": "最小 Alpha",
                    "sync_rgb": "同步 RGB 边缘",
                    "use_resize": "启用缩放",
                    "resize_width": "输出宽度",
                    "resize_height": "输出高度",
                    "use_sharpen": "启用锐化",
                    "sharpen_amount": "锐化强度",
                    "sharpen_radius": "锐化半径",
                    "sharpen_threshold": "锐化阈值",
                    "sharpen_shrink": "锐化缩小倍率",
                    "notify_interval_seconds": "通知间隔秒",
                },
            },
            {
                "id": "pipeline",
                "name": "动画大 Pipeline",
                "skills": ["pipeline.run_preview", "pipeline.run_start"],
                "parameters": {
                    "input_dir": "视频输入/下载目录",
                    "frame_output_dir": "抽帧输出目录",
                    "matte_output_dir": "ComfyUI 抠图输出目录",
                    "smooth_output_dir": "Cherry 平滑输出目录",
                    "workflow_path": "ComfyUI 工作流路径",
                    "fps": "抽帧 FPS",
                    "max_frames": "最多帧数，0 表示不限",
                    "diff_threshold": "关键帧/去重差异阈值",
                    "dedup_enabled": "是否启用去重/剔关键帧",
                    "dedup_renumber": "剔除后是否重新编号",
                    "use_smooth": "启用时序平滑",
                    "resize_width": "后处理输出宽度",
                    "resize_height": "后处理输出高度",
                    "notify_interval_seconds": "通知间隔秒",
                },
            },
            {
                "id": "p4",
                "name": "P4 Shelve-only",
                "skills": ["p4.status", "p4.check", "p4.preview", "p4.report", "p4.shelve_ui_import"],
                "parameters": {
                    "desc": "CL 描述，后端会补 Shelve-only 信息",
                    "workflow": "可选 workflow 覆盖",
                    "workspace": "可选 workspace 覆盖",
                    "cl": "可选已有 CL",
                    "force": "是否允许覆盖已有 shelf",
                    "yes": "一键 shelve 确认参数",
                },
            },
            {
                "id": "speech",
                "name": "ASR / TTS",
                "skills": ["speech.transcribe", "speech.synthesize"],
                "parameters": {
                    "audio_path": "ASR 音频路径",
                    "language": "语音语言",
                    "prompt": "ASR 提示词",
                    "text": "TTS 文本",
                    "output_path": "TTS 输出路径",
                    "voice": "音色/提示音频",
                    "engine": "语音引擎",
                    "rate": "语速",
                },
            },
        ],
    }


def save_definition(
    name: str,
    steps: list[dict[str, Any]],
    description: str = "",
    variables: dict[str, Any] | None = None,
    overwrite: bool = True,
) -> dict[str, Any]:
    safe_name = _safe_name(name)
    if not safe_name:
        raise ValueError("name is required")
    normalized_steps = _normalize_steps(steps)
    root = _definitions_dir()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{safe_name}.json"
    if path.exists() and not overwrite:
        return {"ok": False, "error": "definition exists", "name": safe_name, "path": str(path)}
    payload = {
        "name": safe_name,
        "description": description,
        "variables": variables or {},
        "steps": normalized_steps,
        "updated_at": _now(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "name": safe_name, "path": str(path), "step_count": len(normalized_steps)}


def list_definitions() -> dict[str, Any]:
    items = []
    for path in sorted(_definitions_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        items.append({
            "name": data.get("name") or path.stem,
            "description": data.get("description") or "",
            "step_count": len(data.get("steps") or []),
            "updated_at": data.get("updated_at") or "",
            "path": str(path),
        })
    return {"ok": True, "count": len(items), "items": items}


def get_definition(name: str) -> dict[str, Any]:
    data = _load_definition(name)
    return {"ok": True, "definition": data}


def preview_definition(name: str = "", definition: dict[str, Any] | None = None, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    data = definition or _load_definition(name)
    merged_vars = {**(data.get("variables") or {}), **(variables or {})}
    steps = []
    for step in data.get("steps") or []:
        item = dict(step)
        item["arguments"] = _render_template(item.get("arguments") or {}, merged_vars)
        steps.append(item)
    return {"ok": True, "name": data.get("name") or name, "variables": merged_vars, "steps": steps}


def run_start(name: str = "", definition: dict[str, Any] | None = None, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    preview = preview_definition(name=name, definition=definition, variables=variables)
    run_id = "CPIPE_" + uuid.uuid4().hex[:12].upper()
    run = {
        "run_id": run_id,
        "name": preview.get("name") or name or "ad_hoc",
        "status": "RUNNING",
        "current_step": "",
        "steps": preview["steps"],
        "results": [],
        "error": "",
        "created_at": _now(),
        "updated_at": _now(),
    }
    _save_run(run)
    _start_thread(run_id)
    return {"ok": True, "run_id": run_id, "status": "RUNNING", "step_count": len(preview["steps"])}


def run_status(run_id: str | None = None) -> dict[str, Any]:
    run = _load_run(run_id)
    if not run:
        return {"ok": False, "error": "custom pipeline run not found"}
    return {"ok": True, **run}


def run_list(limit: int = 10, include_finished: bool = True) -> dict[str, Any]:
    items = []
    for path in sorted(_runs_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not include_finished and run.get("status") in {"DONE", "FAILED", "CANCELED"}:
            continue
        items.append(run)
        if len(items) >= max(1, min(limit, 50)):
            break
    return {"ok": True, "count": len(items), "items": items}


def run_cancel(run_id: str) -> dict[str, Any]:
    if not run_id:
        raise ValueError("run_id is required")
    _CANCEL_REQUESTS.add(run_id)
    run = _load_run(run_id)
    if run:
        run["status"] = "CANCELING"
        run["updated_at"] = _now()
        _save_run(run)
    return {"ok": True, "run_id": run_id, "status": "CANCELING"}


def _start_thread(run_id: str) -> None:
    if run_id in _RUN_THREADS:
        return
    _RUN_THREADS.add(run_id)
    threading.Thread(target=_run_worker, args=(run_id,), daemon=True).start()


def _run_worker(run_id: str) -> None:
    from assetclaw_matting.skills.registry import call_skill

    try:
        run = _load_run(run_id)
        if not run:
            return
        for index, step in enumerate(run.get("steps") or []):
            if run_id in _CANCEL_REQUESTS:
                run["status"] = "CANCELED"
                run["updated_at"] = _now()
                _save_run(run)
                return
            if step.get("enabled") is False:
                continue
            skill = step.get("skill")
            run["current_step"] = step.get("id") or f"step_{index + 1}"
            run["updated_at"] = _now()
            _save_run(run)
            result = call_skill(skill, step.get("arguments") or {}, requested_by="custom_pipeline")
            run.setdefault("results", []).append({"step": run["current_step"], "skill": skill, "result": result, "finished_at": _now()})
            run["updated_at"] = _now()
            _save_run(run)
            if not result.get("ok"):
                run["status"] = "FAILED"
                run["error"] = str(result.get("error") or result)
                run["updated_at"] = _now()
                _save_run(run)
                return
            _wait_for_child_if_needed(run, result)
        run["status"] = "DONE"
        run["current_step"] = ""
        run["updated_at"] = _now()
        _save_run(run)
    except Exception as exc:
        run = _load_run(run_id) or {"run_id": run_id}
        run["status"] = "FAILED"
        run["error"] = str(exc)
        run["updated_at"] = _now()
        _save_run(run)
    finally:
        _RUN_THREADS.discard(run_id)
        _CANCEL_REQUESTS.discard(run_id)


def _wait_for_child_if_needed(run: dict[str, Any], result: dict[str, Any]) -> None:
    payload = result.get("result") or {}
    run_id = payload.get("run_id")
    skill = result.get("skill") or ""
    status_skill = ""
    if skill.startswith("frame."):
        status_skill = "frame.run_status"
    elif skill.startswith("comfyui."):
        status_skill = "comfyui.run_status"
    elif skill.startswith("cherry."):
        status_skill = "cherry.run_status"
    elif skill.startswith("pipeline."):
        status_skill = "pipeline.run_status"
    if not run_id or not status_skill or not skill.endswith(".run_start"):
        return

    from assetclaw_matting.skills.registry import call_skill

    for _ in range(720):
        if run["run_id"] in _CANCEL_REQUESTS:
            return
        args = {"run_id": run_id}
        if status_skill in {"comfyui.run_status", "cherry.run_status"}:
            args["include_gpu"] = False
        status = call_skill(status_skill, args, requested_by="custom_pipeline")
        run.setdefault("child_status", {})[run_id] = status
        run["updated_at"] = _now()
        _save_run(run)
        child = status.get("result") or {}
        if child.get("status") in {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED"}:
            if child.get("status") in {"FAILED", "CANCELED"}:
                raise RuntimeError(f"child run {run_id} ended as {child.get('status')}: {child.get('error') or ''}")
            return
        time.sleep(5)


def _normalize_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not steps:
        raise ValueError("steps are required")
    result = []
    for index, step in enumerate(steps):
        skill = str(step.get("skill") or "").strip()
        if skill not in ALLOWED_STEP_SKILLS:
            raise ValueError(f"unsupported custom pipeline skill: {skill}")
        result.append({
            "id": str(step.get("id") or f"step_{index + 1}"),
            "name": str(step.get("name") or skill),
            "skill": skill,
            "arguments": step.get("arguments") or {},
            "enabled": bool(step.get("enabled", True)),
        })
    return result


def _render_template(value: Any, variables: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {k: _render_template(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [_render_template(v, variables) for v in value]
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            return str(variables.get(match.group(1), match.group(0)))
        return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", replace, value)
    return value


def _load_definition(name: str) -> dict[str, Any]:
    path = _definitions_dir() / f"{_safe_name(name)}.json"
    if not path.exists():
        raise FileNotFoundError(str(path))
    return json.loads(path.read_text(encoding="utf-8"))


def _load_run(run_id: str | None = None) -> dict[str, Any] | None:
    if run_id:
        path = _runs_dir() / f"{_safe_name(run_id)}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    runs = sorted(_runs_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return json.loads(runs[0].read_text(encoding="utf-8")) if runs else None


def _save_run(run: dict[str, Any]) -> None:
    _runs_dir().mkdir(parents=True, exist_ok=True)
    (_runs_dir() / f"{_safe_name(run['run_id'])}.json").write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")


def _definitions_dir() -> Path:
    from assetclaw_matting.config import settings
    return Path(settings.storage_dir) / "custom_pipelines"


def _runs_dir() -> Path:
    from assetclaw_matting.config import settings
    return Path(settings.storage_dir) / "custom_pipeline_runs"


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", "_", str(name or "").strip())[:80]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
