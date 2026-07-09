from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from assetclaw_matting.config import settings
from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import init_db
from assetclaw_matting.runtime_context import set_runtime_context
from assetclaw_matting.services.notification_service import send_text
from assetclaw_matting.skills import p4_skills
from assetclaw_matting.skills.comfyui_skills import run_start as comfy_start
from assetclaw_matting.skills.comfyui_skills import run_status as comfy_status
from assetclaw_matting.skills.unity_import_skills import run_import
from tools.animation_automation.core import build_unity_ready
from assetclaw_matting.skills.animation_flow_skills import (
    _format_p4_preview_stage,
    _format_p4_reconcile_stage,
    _format_unity_import_stage,
    _format_unity_ready_stage,
    _unity_ready_p4_paths,
)


TERMINAL = {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _notify(chat_id: str, text: str) -> None:
    print(text, flush=True)
    if chat_id:
        send_text(chat_id, text)


def _archive_dir(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archived = path.with_name(f"{path.name}_archived_before_scene_rerun_{stamp}")
    shutil.move(str(path), str(archived))
    return archived


def _poll_comfy(run_id: str, status_file: Path, payload: dict[str, Any], poll_seconds: int) -> dict[str, Any]:
    while True:
        status = comfy_status(run_id, include_gpu=True)
        payload["updated_at"] = _now()
        payload["comfyui"] = status
        _write(status_file, payload)
        if str(status.get("status") or "").upper() in TERMINAL:
            return status
        time.sleep(max(5, poll_seconds))


def main() -> int:
    parser = argparse.ArgumentParser(description="Rerun scene animation flow from existing frames.")
    parser.add_argument("--frames-dir", required=True)
    parser.add_argument("--workflow-path", default="")
    parser.add_argument("--date-root", default="")
    parser.add_argument("--unity-project", default="D:/Spark/Client")
    parser.add_argument("--unity-import-mode", default="iteration", choices=["import", "iteration"])
    parser.add_argument("--p4-stream", default="//streams/rel_0.0.1")
    parser.add_argument("--status-file", required=True)
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--notify-interval-seconds", type=int, default=60)
    parser.add_argument("--skip-p4", action="store_true")
    args = parser.parse_args()

    settings.ensure_dirs()
    init_db(settings.data_db_path)
    create_tables()

    chat_id = settings.feishu_default_notify_chat_id or ""
    if chat_id:
        set_runtime_context(channel="feishu", chat_id=chat_id)

    run_id = "SCENE_RERUN_" + uuid.uuid4().hex[:12].upper()
    frames_dir = Path(args.frames_dir).resolve()
    scene_root = frames_dir.parent
    date_root = Path(args.date_root).resolve() if args.date_root else scene_root.parent
    matte_dir = scene_root / "matte"
    workflow = Path(args.workflow_path or settings.comfyui_workflow_path).resolve()
    status_file = Path(args.status_file).resolve()
    payload: dict[str, Any] = {
        "ok": True,
        "run_id": run_id,
        "status": "RUNNING",
        "current_stage": "matting",
        "created_at": _now(),
        "updated_at": _now(),
        "date_root": str(date_root),
        "frames_dir": str(frames_dir),
        "matte_dir": str(matte_dir),
        "workflow_path": str(workflow),
        "unity_project": str(Path(args.unity_project).resolve()),
        "unity_import_mode": args.unity_import_mode,
        "p4_stream": args.p4_stream,
        "error": "",
        "children": {},
    }
    _write(status_file, payload)

    try:
        if not frames_dir.is_dir():
            raise FileNotFoundError(f"frames dir not found: {frames_dir}")
        frame_count = sum(1 for _ in frames_dir.rglob("*.png"))
        if frame_count <= 0:
            raise RuntimeError(f"frames dir has no png: {frames_dir}")
        archived = _archive_dir(matte_dir)
        matte_dir.mkdir(parents=True, exist_ok=True)
        payload["archived_matte"] = str(archived) if archived else ""
        _write(status_file, payload)
        _notify(
            chat_id,
            "Scene 动画自动化重跑已启动：从 frames 开始，不重新下载/抽帧"
            f"\n任务：{run_id}"
            f"\n输入：{frames_dir}"
            f"\n帧数：{frame_count}"
            f"\n旧 matte 归档：{archived or '无'}"
            f"\n输出：{matte_dir}"
            f"\n输出校验：必须是最终透明 PNG；失败会等待并重试，拒绝黑底/白 mask 中间图。",
        )

        comfy = comfy_start(
            workflow_path=str(workflow),
            input_dir=str(frames_dir),
            output_dir=str(matte_dir),
            recursive=True,
            preserve_structure=True,
            max_images=50000,
            skip_existing=False,
            notify_interval_seconds=args.notify_interval_seconds,
        )
        payload["children"]["comfyui_run_id"] = comfy["run_id"]
        _write(status_file, payload)
        status = _poll_comfy(str(comfy["run_id"]), status_file, payload, args.poll_seconds)
        if status.get("status") not in {"DONE", "DONE_WITH_ERRORS"} or int(status.get("failed") or 0) > 0:
            raise RuntimeError(f"ComfyUI rerun failed: {status.get('status')} failed={status.get('failed')} error={status.get('error')}")
        _notify(
            chat_id,
            "步骤3 Scene 抠图重跑完成："
            f"\n任务：{run_id}"
            f"\nComfyUI：{comfy['run_id']}"
            f"\n完成：{status.get('completed', 0)}/{status.get('total', 0)}"
            f"\n输出：{matte_dir}",
        )

        payload["current_stage"] = "unity_ready"
        _write(status_file, payload)
        ready_report = build_unity_ready(date_root, overwrite=True, copy_mode="copy")
        payload["children"]["unity_ready_report"] = ready_report
        _write(status_file, payload)
        _notify(chat_id, _format_unity_ready_stage(ready_report, date_root / "unity_ready"))

        payload["current_stage"] = "unity_import"
        _write(status_file, payload)
        _notify(chat_id, f"步骤5 Unity 资源迭代/替换开始：{date_root / 'unity_ready'}")
        unity = run_import(
            str(date_root / "unity_ready"),
            unity_project=args.unity_project,
            package="scene",
            mode=args.unity_import_mode,
        )
        payload["children"]["unity_import"] = unity
        _write(status_file, payload)
        if not unity.get("ok"):
            raise RuntimeError(f"Unity import failed: {unity.get('error')}")
        _notify(chat_id, _format_unity_import_stage(unity))

        payload["current_stage"] = "p4_shelve"
        _write(status_file, payload)
        if args.skip_p4:
            payload["status"] = "DONE"
            payload["updated_at"] = _now()
            _write(status_file, payload)
            _notify(chat_id, f"Scene 重跑完成：{run_id}\nP4：已按参数跳过")
            return 0

        target_paths = _unity_ready_p4_paths(date_root / "unity_ready", args.unity_import_mode)
        target_paths = [p for p in target_paths if "CharacterAnim/" in p]
        _notify(
            chat_id,
            "步骤6 P4 开始：switch/get latest/check/preview/create CL/reconcile/shelve/report"
            f"\nStream：{args.p4_stream}"
            "\nSubmit：disabled",
        )
        switched = p4_skills.switch_stream(stream=args.p4_stream, preview=False, yes=True)
        latest = p4_skills.get_latest(scope="managed", yes=True)
        check = p4_skills.check()
        if not check.get("ok"):
            raise RuntimeError(f"P4 check failed: {check.get('error') or check.get('safety', {}).get('errors')}")
        preview = p4_skills.preview(paths=target_paths)
        if not preview.get("ok"):
            raise RuntimeError(f"P4 preview failed: {preview.get('error') or preview.get('safety', {}).get('errors')}")
        _notify(chat_id, _format_p4_preview_stage(preview, target_paths))
        desc = f"[Shelve-only] Animation UI scene {args.unity_import_mode} {date_root.name} ({run_id})"
        created = p4_skills.create_cl(desc=desc, yes=True)
        if not created.get("ok"):
            raise RuntimeError(f"P4 create CL failed: {created.get('error')}")
        cl = created.get("changelist_id") or created.get("cl") or created.get("changelist")
        _notify(chat_id, f"步骤6 P4 changelist 已创建：{cl}")
        reconciled = p4_skills.reconcile(cl=cl, paths=target_paths, yes=True)
        if not reconciled.get("ok"):
            raise RuntimeError(f"P4 reconcile failed: {reconciled.get('error') or reconciled.get('safety', {}).get('errors')}")
        _notify(chat_id, _format_p4_reconcile_stage(reconciled, cl))
        shelved = p4_skills.shelve(cl=cl, force=True, yes=True)
        if not shelved.get("ok"):
            raise RuntimeError(f"P4 shelve failed: {shelved.get('error') or shelved.get('safety', {}).get('errors')}")
        report = p4_skills.report(cl=cl, unity_ready_manifest=str(date_root / "unity_ready" / "manifest.json"))
        payload["children"]["p4"] = {
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
        payload["status"] = "DONE"
        payload["updated_at"] = _now()
        _write(status_file, payload)
        _notify(chat_id, f"Scene 动画自动化重跑完成：{run_id}\nCL/Shelf：{cl}\nSubmit：disabled")
        return 0
    except Exception as exc:
        payload["status"] = "FAILED"
        payload["error"] = str(exc)
        payload["updated_at"] = _now()
        _write(status_file, payload)
        _notify(chat_id, f"Scene 动画自动化重跑失败：{run_id}\n{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
