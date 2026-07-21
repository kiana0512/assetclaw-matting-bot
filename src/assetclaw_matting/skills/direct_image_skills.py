from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

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
    package_as_sequence: bool = False,
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
                "source_name": name,
                "original_path": str(target),
                "name": target.name,
                "matte_dir": str(matte_dir / f"image_{index:02d}"),
                "smooth_dir": str(smooth_dir / f"image_{index:02d}"),
                "width": width,
                "height": height,
                "aspect": aspect,
                "cherry_profile": "half" if aspect == "square" else "full",
                "cherry_output_size": _cherry_output_size("half" if aspect == "square" else "full"),
                "matte_result_path": "",
                "postprocessed_result_path": "",
                "comparison_path": "",
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
        "package_as_sequence": bool(package_as_sequence or len(items) > 1),
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
        if bool(run.get("package_as_sequence")) or len(run.get("images") or []) > 1:
            _notify(run, f"序列帧完成：{run['id']}，共 {len(run.get('images') or [])} 帧，已按顺序发回 1 个 ZIP{suffix}。")
        else:
            _notify(run, f"图片完成：{run['id']}，已发回抠图、后处理、三联对比 3 份结果{suffix}。")
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

    items = list(run.get("images") or [])
    prepared = _prepare_result_files(run)
    if bool(run.get("package_as_sequence")) or len(items) > 1:
        zip_path = _make_sequence_zip(run)
        drive_file = feishu_client.send_file_to_chat(chat_id, zip_path, zip_path.name) or {}
        if drive_file:
            run["drive_file"] = drive_file
        run["sequence_zip_path"] = str(zip_path)
        _append_log(run, f"序列帧结果已打包发送：{zip_path.name}，共 {len(items)} 帧")
        _save(run)
        return [str(zip_path)]

    sent: list[str] = []
    for item, (matte, processed, comparison) in zip(items, prepared):
        base_name = Path(str(item.get("source_name") or item.get("name") or processed.name)).stem
        matte_send_name = f"{base_name}_matte{matte.suffix.lower()}"
        processed_send_name = f"{base_name}_processed{processed.suffix.lower()}"
        feishu_client.send_file_to_chat(chat_id, matte, matte_send_name)
        feishu_client.send_file_to_chat(chat_id, processed, processed_send_name)
        feishu_client.send_image_to_chat(chat_id, comparison)
        sent.extend([str(matte), str(processed), str(comparison)])
        _append_log(run, f"已发送三份图片结果：{matte.name}、{processed.name}、{comparison.name}")
        _save(run)
    return sent


def _prepare_result_files(run: dict[str, Any]) -> list[tuple[Path, Path, Path]]:
    prepared: list[tuple[Path, Path, Path]] = []
    for item in run.get("images") or []:
        matte_dir = Path(str(item["matte_dir"]))
        smooth_dir = Path(str(item["smooth_dir"]))
        original = Path(str(item.get("original_path") or ""))
        matte = _latest_image(matte_dir)
        processed = _latest_image(smooth_dir)
        if not original.is_file():
            raise RuntimeError(f"original image is missing: {original}")
        if not matte:
            raise RuntimeError(f"matte_dir has no result image: {matte_dir}")
        if not processed:
            raise RuntimeError(f"smooth_dir has no result image: {smooth_dir}")

        base_name = Path(str(item.get("source_name") or item.get("name") or processed.name)).stem
        comparison = _run_dir(run) / "comparison" / f"{base_name}_comparison.png"
        if not comparison.is_file():
            _create_comparison_image(original, matte, processed, comparison)

        item["matte_result_path"] = str(matte)
        item["postprocessed_result_path"] = str(processed)
        item["comparison_path"] = str(comparison)
        item["result_path"] = str(processed)
        prepared.append((matte, processed, comparison))
    _save(run)
    return prepared


def package_and_send(run_id: str | None = None, package_name: str = "", **_: Any) -> dict[str, Any]:
    run = _load(run_id)
    if not run:
        return {"ok": False, "error": "direct image run not found"}
    if not run.get("chat_id"):
        return {"ok": False, "error": "run has no Feishu chat_id"}
    from assetclaw_matting.feishu.client import feishu_client

    _prepare_result_files(run)
    zip_path = _make_sequence_zip(run, package_name=package_name)
    drive_file = feishu_client.send_file_to_chat(str(run["chat_id"]), zip_path, zip_path.name) or {}
    run["sequence_zip_path"] = str(zip_path)
    if drive_file:
        run["drive_file"] = drive_file
    run["status"] = "DONE"
    run["stage"] = "done"
    run["error"] = ""
    _append_log(run, f"序列帧压缩包已补发：{zip_path.name}，共 {len(run.get('images') or [])} 帧")
    _save(run)
    return {
        "ok": True,
        "run_id": run["id"],
        "zip_path": str(zip_path),
        "zip_name": zip_path.name,
        "frame_count": len(run.get("images") or []),
        "drive_file": drive_file,
    }


def _make_sequence_zip(run: dict[str, Any], package_name: str = "") -> Path:
    items = sorted(run.get("images") or [], key=lambda item: int(item.get("index") or 0))
    if not items:
        raise RuntimeError("sequence run contains no images")
    if package_name:
        zip_name = _safe_name(package_name)
        if not zip_name.lower().endswith(".zip"):
            zip_name += ".zip"
    else:
        label = str(run.get("run_label") or "").strip()
        if not label or "、" in label or label.lower().startswith("feishu_image"):
            label = f"序列帧_{len(items)}张"
        zip_name = f"{Path(_safe_name(label)).stem}_animation_processed.zip"
    zip_path = _run_dir(run) / zip_name
    manifest = {"run_id": run.get("id"), "frame_count": len(items), "ordered": True, "files": []}
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as archive:
        for position, item in enumerate(items):
            original = Path(str(item.get("original_path") or ""))
            matte = Path(str(item.get("matte_result_path") or ""))
            processed = Path(str(item.get("postprocessed_result_path") or ""))
            comparison = Path(str(item.get("comparison_path") or ""))
            paths = {"frames": original, "matte": matte, "smooth": processed, "comparison": comparison}
            missing = [f"{kind}:{path}" for kind, path in paths.items() if not path.is_file()]
            if missing:
                raise RuntimeError("sequence package missing result files: " + ", ".join(missing))
            stem = f"{position:04d}"
            entries: dict[str, str] = {}
            for kind, path in paths.items():
                entry = f"{kind}/{stem}{path.suffix.lower() or '.png'}"
                archive.write(path, entry)
                entries[kind] = entry
            manifest["files"].append(
                {"index": position, "source_name": item.get("source_name") or item.get("name") or "", **entries}
            )
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return zip_path


def _create_comparison_image(original_path: Path, matte_path: Path, processed_path: Path, output_path: Path) -> Path:
    """Create a readable original/matte/post-process triptych for Feishu preview."""
    labels = ("原图", "抠图结果", "后处理结果")
    paths = (original_path, matte_path, processed_path)
    panel_width = 480
    image_height = 520
    label_height = 64
    outer_margin = 24
    gap = 18
    panel_height = label_height + image_height
    canvas_width = outer_margin * 2 + panel_width * 3 + gap * 2
    canvas_height = outer_margin * 2 + panel_height
    canvas = Image.new("RGB", (canvas_width, canvas_height), (25, 27, 32))
    draw = ImageDraw.Draw(canvas)
    font = _comparison_font(28)

    for index, (label, path) in enumerate(zip(labels, paths)):
        left = outer_margin + index * (panel_width + gap)
        top = outer_margin
        draw.rounded_rectangle(
            (left, top, left + panel_width - 1, top + panel_height - 1),
            radius=12,
            fill=(245, 246, 248),
            outline=(73, 77, 87),
            width=2,
        )
        label_box = draw.textbbox((0, 0), label, font=font)
        label_width = label_box[2] - label_box[0]
        draw.text(
            (left + (panel_width - label_width) // 2, top + 14),
            label,
            font=font,
            fill=(34, 37, 44),
        )

        image_top = top + label_height
        checker = _checkerboard((panel_width - 4, image_height - 4))
        canvas.paste(checker, (left + 2, image_top + 2))
        with Image.open(path) as source:
            rgba = ImageOps.exif_transpose(source).convert("RGBA")
            fitted = ImageOps.contain(rgba, (panel_width - 28, image_height - 28), Image.Resampling.LANCZOS)
        x = left + (panel_width - fitted.width) // 2
        y = image_top + (image_height - fitted.height) // 2
        canvas.paste(fitted, (x, y), fitted)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="PNG", optimize=True)
    return output_path


def _checkerboard(size: tuple[int, int], cell: int = 20) -> Image.Image:
    board = Image.new("RGB", size, (235, 235, 235))
    draw = ImageDraw.Draw(board)
    alternate = (207, 207, 207)
    width, height = size
    for y in range(0, height, cell):
        for x in range(0, width, cell):
            if (x // cell + y // cell) % 2:
                draw.rectangle((x, y, min(x + cell - 1, width - 1), min(y + cell - 1, height - 1)), fill=alternate)
    return board


def _comparison_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/msyhbd.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    )
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            return ImageFont.truetype(str(candidate), size=size)
        except OSError:
            continue
    return ImageFont.load_default()


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

    try:
        send_text(chat_id, text)
    except Exception as exc:
        _append_log(run, f"通知发送失败（不影响主任务）：{exc}")
        _save(run)


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
        "package_as_sequence": bool(run.get("package_as_sequence")),
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
        "images": run.get("images") or [],
        "children": run.get("children") or {},
        "sent_files": run.get("sent_files") or [],
        "sequence_zip_path": run.get("sequence_zip_path") or "",
        "drive_file": run.get("drive_file") or {},
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
