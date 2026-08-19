from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

from assetclaw_matting.skills.media_skills import IMAGE_EXTS
from assetclaw_matting.skills.security import validate_path


_WORKER_RUNS: set[str] = set()
_MONITORING_RUNS: set[str] = set()


class CherryBatchCapacityError(RuntimeError):
    """The HTML renderer ran out of per-session memory for a multi-file batch."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_id() -> str:
    return "CHERRY_" + uuid.uuid4().hex[:12].upper()


def info() -> dict[str, Any]:
    source = _tool_source_path()
    runtime: dict[str, Any] = {}
    runtime_error = ""
    try:
        runtime = _require_html_runtime()
    except Exception as exc:
        runtime_error = str(exc)
    return {
        "ok": True,
        "name": "Cherry HTML 帧序列处理工具",
        "source_path": str(source),
        "exists": source.exists(),
        "engine": "headless_chrome_html",
        "runtime_ready": not runtime_error,
        "runtime_error": runtime_error,
        "browser_path": runtime.get("browser_path", ""),
        "steps": ["fringe", "hairinset", "feather(rect only)", "blur", "resize2", "colormatch(final-2)", "align(final)"],
        "position_alignment": "required_with_character_reference",
        "temporal_smooth": "controlled_by_html_default_off",
        "defaults": _default_options(),
        "presets": {"auto": preset_options("auto"), "full": preset_options("full"), "half": preset_options("half")},
    }


def run_preview(
    input_dir: str,
    output_dir: str,
    recursive: bool = True,
    max_images: int = 10000,
    reference_path: str | None = None,
    **options: Any,
) -> dict[str, Any]:
    src = validate_path(input_dir, must_exist=True)
    dst = validate_path(output_dir, must_exist=False)
    if not src.is_dir():
        raise ValueError("input_dir must be a directory")
    files = _collect_images(src, recursive=recursive, max_images=max_images)
    groups = _group_sequences(src, files)
    reference = _require_color_reference(reference_path) if reference_path else None
    if reference:
        expected_profile, expected_width, expected_height = _require_expected_output_profile(options)
        merge_input = dict(options)
        merge_input.setdefault("profile", expected_profile)
        preview_options = _merge_options(merge_input)
        _lock_expected_output(preview_options, expected_profile, expected_width, expected_height)
    else:
        preview_options = _merge_options(options)
        preview_options["html_modules"] = [step for step in preview_options.get("html_modules") or [] if step not in {"colormatch", "align"}]
        preview_options["html_colormatch_enabled"] = False
        preview_options["html_align_enabled"] = False
    return {
        "ok": True,
        "input_dir": str(src),
        "output_dir": str(dst),
        "total": len(files),
        "sequence_count": len(groups),
        "sample_inputs": [str(path.relative_to(src)) for path in files[:8]],
        "recursive": recursive,
        "preserve_structure": True,
        "reference_path": str(reference) if reference else "",
        "reference_ready": bool(reference),
        "options": preview_options,
    }


def run_start(
    input_dir: str,
    output_dir: str,
    recursive: bool = True,
    max_images: int = 10000,
    skip_existing: bool = False,
    notify_interval_seconds: int = 60,
    reference_path: str | None = None,
    reference_sha256: str | None = None,
    **options: Any,
) -> dict[str, Any]:
    from assetclaw_matting.config import settings
    from assetclaw_matting.db.sqlite import get_connection
    from assetclaw_matting.runtime_context import get_runtime_context

    _require_html_runtime()
    src = validate_path(input_dir, must_exist=True)
    dst = validate_path(output_dir, must_exist=False)
    if not src.is_dir():
        raise ValueError("input_dir must be a directory")
    reference_steps_required = bool(options.get("color_match_required") or options.get("reference_postprocess_required"))
    reference = _require_color_reference(reference_path) if str(reference_path or "").strip() else None
    if reference_steps_required and reference is None:
        raise ValueError("reference_path is required for Cherry color matching and alignment")
    reference_steps_enabled = reference is not None
    expected_output: tuple[str, int, int] | None = None
    if reference_steps_enabled:
        expected_output = _require_expected_output_profile(options)
    actual_reference_sha256 = _sha256_path(reference) if reference else ""
    expected_reference_sha256 = str(reference_sha256 or actual_reference_sha256).lower()
    if reference and actual_reference_sha256.lower() != expected_reference_sha256:
        raise ValueError("reference_path SHA-256 does not match the frozen character reference")
    files = _collect_images(src, recursive=recursive, max_images=max_images)
    if not files:
        raise ValueError("input_dir has no supported images")
    if skip_existing:
        files = [path for path in files if not _output_target(src, dst, path).exists()]
    dst.mkdir(parents=True, exist_ok=True)

    run_id = _run_id()
    pinned_source, pinned_source_sha256 = _pin_html_for_run(run_id, _tool_source_path(), Path(settings.storage_dir))
    ctx = get_runtime_context()
    if reference_steps_enabled:
        assert expected_output is not None
        merge_input = dict(options)
        merge_input.setdefault("profile", expected_output[0])
        opts = _merge_options(merge_input)
        _lock_expected_output(opts, *expected_output)
    else:
        opts = _merge_options(options)
        opts["html_modules"] = [step for step in opts.get("html_modules") or [] if step not in {"colormatch", "align"}]
        opts["html_colormatch_enabled"] = False
        opts["html_align_enabled"] = False
    opts.update(
        {
            "recursive": recursive,
            "skip_existing": skip_existing,
            "notify_interval_seconds": max(30, min(int(notify_interval_seconds), 3600)),
            "reference_path": str(reference) if reference else "",
            "reference_sha256_expected": expected_reference_sha256,
            "reference_postprocess_required": reference_steps_enabled,
            "colormatch_required": reference_steps_enabled,
            "position_alignment_enabled": reference_steps_enabled,
            "source_path": str(pinned_source),
            "source_sha256_pinned": pinned_source_sha256,
            "chat_id": (ctx.get("chat_id") or "") if ctx.get("channel") == "feishu" else "",
            "archived": False,
            "processed": [],
            "errors": [],
        }
    )
    created_at = _now()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO cherry_runs
            (id, status, input_dir, output_dir, total, completed, failed, files_json, options_json, error, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                "DONE" if not files else "RUNNING",
                str(src),
                str(dst),
                len(files),
                0,
                0,
                json.dumps([str(path) for path in files], ensure_ascii=False),
                json.dumps(opts, ensure_ascii=False),
                "",
                created_at,
                created_at,
            ),
        )
    if opts.get("chat_id") and files:
        _notify(run_id, f"Cherry 帧序列任务已启动：{len(files)} 张\n输入：{src}\n输出：{dst}")
        _start_progress_monitor(run_id)
    if files:
        _start_run_worker(run_id)
    return {
        "ok": True,
        "run_id": run_id,
        "status": "RUNNING",
        "input_dir": str(src),
        "output_dir": str(dst),
        "total": len(files),
        "sequence_count": len(_group_sequences(src, files)),
        "skip_existing": skip_existing,
        "options": opts,
    }


def run_status(run_id: str | None = None, include_gpu: bool = True) -> dict[str, Any]:
    from assetclaw_matting.skills.status_skills import gpu_status

    row = _get_run(run_id)
    if not row:
        return {"ok": False, "error": "cherry run not found"}
    options = json.loads(row["options_json"] or "{}")
    processed = options.get("processed") or []
    errors = options.get("errors") or []
    total = int(row["total"] or 0)
    completed = int(row["completed"] or len(processed))
    failed = int(row["failed"] or len(errors))
    elapsed = max(0.0, time.time() - datetime.fromisoformat(row["created_at"]).timestamp())
    eta_seconds = _eta(elapsed, completed, total)
    payload = {
        "ok": True,
        "run_id": row["id"],
        "status": row["status"],
        "input_dir": row["input_dir"],
        "output_dir": row["output_dir"],
        "total": total,
        "completed": completed,
        "failed": failed,
        "running_or_pending": max(0, total - completed - failed),
        "progress_percent": round((completed + failed) * 100 / total, 1) if total else 0,
        "eta_seconds": eta_seconds,
        "last_completed": processed[-1]["rel_path"] if processed else "",
        "last_completed_detail": _path_detail(str(processed[-1]["rel_path"])) if processed else {},
        "error": row["error"] or "",
        "options": {k: v for k, v in options.items() if k not in {"processed", "errors", "chat_id"}},
    }
    if include_gpu:
        try:
            payload["gpu"] = gpu_status()
        except Exception:
            payload["gpu"] = {}
    return payload


def run_list(limit: int = 10, include_archived: bool = False, include_finished: bool = False) -> dict[str, Any]:
    from assetclaw_matting.db.sqlite import get_connection

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, status, input_dir, output_dir, total, completed, failed, options_json, created_at, updated_at
            FROM cherry_runs
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, min(limit, 50)),),
        ).fetchall()
    items = []
    for row in rows:
        options = json.loads(row["options_json"] or "{}")
        if options.get("archived") and not include_archived:
            continue
        if not include_finished and row["status"] in {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED"}:
            continue
        items.append(
            {
                "run_id": row["id"],
                "status": row["status"],
                "input_dir": row["input_dir"],
                "output_dir": row["output_dir"],
                "total": int(row["total"] or 0),
                "completed": int(row["completed"] or 0),
                "failed": int(row["failed"] or 0),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
    return {"ok": True, "count": len(items), "items": items}


def run_cancel(run_id: str | None = None, notify: bool = True) -> dict[str, Any]:
    row = _get_run(run_id)
    if not row:
        return {"ok": False, "error": "cherry run not found"}
    _set_run_status(row["id"], "CANCELED")
    if notify:
        _notify(row["id"], f"Cherry 任务已终止：{row['id']}")
    return {"ok": True, "run_id": row["id"], "status": "CANCELED"}


def run_delete(run_id: str | None = None) -> dict[str, Any]:
    from assetclaw_matting.db.sqlite import get_connection

    row = _get_run(run_id)
    if not row:
        return {"ok": False, "error": "cherry run not found"}
    if row["status"] not in {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED"}:
        return {"ok": False, "error": "任务还在运行中。先终止或等它结束，再删除记录。", "run_id": row["id"], "status": row["status"]}
    options = json.loads(row["options_json"] or "{}")
    options["archived"] = True
    with get_connection() as conn:
        conn.execute(
            "UPDATE cherry_runs SET options_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(options, ensure_ascii=False), _now(), row["id"]),
        )
    return {"ok": True, "run_id": row["id"], "status": "ARCHIVED"}


def preview_run_start_confirmation(arguments: dict[str, Any], confirmation_id: str) -> str:
    from assetclaw_matting.config import settings

    try:
        preview = run_preview(
            input_dir=arguments.get("input_dir") or str(settings.default_batch_output_dir),
            output_dir=arguments.get("output_dir") or str(settings.storage_dir / "cherry_output"),
            recursive=bool(arguments.get("recursive", True)),
            max_images=int(arguments.get("max_images") or 10000),
            **{k: v for k, v in arguments.items() if k not in {"input_dir", "output_dir", "recursive", "max_images"}},
        )
        opts = preview.get("options") or {}
        steps = _steps_text(opts)
        lines = [
            "请确认是否开始 Cherry 帧序列处理：",
            f"输入：{preview.get('input_dir')}",
            f"输出：{preview.get('output_dir')}",
            f"图片：{preview.get('total')} 张，序列：{preview.get('sequence_count')} 组",
            f"处理：{steps}",
        ]
        samples = preview.get("sample_inputs") or []
        if samples:
            lines.append("示例：" + "、".join(samples[:3]))
        lines.append(f"回复：确认执行 {confirmation_id}")
        return "\n".join(lines)
    except Exception as exc:
        return f"需要确认：cherry.run_start\n预检查失败：{exc}\n回复：确认执行 {confirmation_id}"


def _run_worker(run_id: str) -> None:
    try:
        row = _get_run(run_id)
        if not row:
            return
        from assetclaw_matting.config import settings

        if not settings.cherry_html_runner_enabled:
            raise RuntimeError("Cherry HTML runner is disabled; Python fallback is not permitted")
        _require_html_runtime()
        _run_worker_html(run_id, row)
        return
    except Exception as exc:
        _save_progress(run_id, error=str(exc))
        _set_run_status(run_id, "FAILED")
        _notify(run_id, f"Cherry 任务异常：{exc}")
    finally:
        _WORKER_RUNS.discard(run_id)


def _run_worker_html(run_id: str, row: Any) -> None:
    from assetclaw_matting.config import settings

    src = Path(row["input_dir"])
    dst = Path(row["output_dir"])
    files = [Path(path) for path in json.loads(row["files_json"] or "[]")]
    options = json.loads(row["options_json"] or "{}")
    reference_steps_required = bool(options.get("reference_postprocess_required"))
    reference_path = _require_color_reference(options.get("reference_path")) if str(options.get("reference_path") or "").strip() else None
    if reference_steps_required and reference_path is None:
        raise RuntimeError("Cherry reference post-processing is enabled without a reference image")
    expected_reference_sha256 = str(options.get("reference_sha256_expected") or "").lower()
    if reference_steps_required and (
        not expected_reference_sha256
        or reference_path is None
        or _sha256_path(reference_path).lower() != expected_reference_sha256
    ):
        raise RuntimeError("frozen character reference changed before Cherry processing")
    expected_output = _require_expected_output_profile(options) if reference_steps_required else None
    processed = options.get("processed") or []
    errors = options.get("errors") or []
    done = {item.get("src_path") for item in processed}
    groups = _group_sequences(src, files)
    options.setdefault("engine", "headless_chrome_html")
    source_path = Path(str(options.get("source_path") or ""))
    expected_source_sha256 = str(options.get("source_sha256_pinned") or "")
    if not source_path.is_file() or not expected_source_sha256:
        source_path, expected_source_sha256 = _pin_html_for_run(run_id, _tool_source_path(), Path(settings.storage_dir))
        options["source_path"] = str(source_path)
        options["source_sha256_pinned"] = expected_source_sha256
    if _sha256_path(source_path) != expected_source_sha256:
        raise RuntimeError("pinned Cherry HTML snapshot changed before processing")
    saved_group_transforms = options.setdefault("sequence_alignment_transforms", {})
    saved_group_color_transforms = options.setdefault("sequence_color_transforms", {})

    for group_files in groups:
        latest = _get_run(run_id)
        if not latest or latest["status"] == "CANCELED":
            return
        group_key = str(group_files[0].parent.relative_to(src)).replace("\\", "/") if group_files else "."
        group_alignment_transform = saved_group_transforms.get(group_key)
        group_color_transform = saved_group_color_transforms.get(group_key)
        pending = [path for path in group_files if str(path) not in done]
        if not pending:
            continue
        missing_frozen_sequence_state = not group_alignment_transform or (
            str(options.get("color_api") or "") == "sequence_transform_v2" and not group_color_transform
        )
        if reference_steps_required and missing_frozen_sequence_state and len(pending) != len(group_files):
            # A pre-upgrade/interrupted run may have partial outputs without
            # the frozen transform. Re-run this one group so all frames share
            # one verifiable transform instead of mixing two alignments.
            group_paths = {str(path) for path in group_files}
            processed = [item for item in processed if str(item.get("src_path") or "") not in group_paths]
            done.difference_update(group_paths)
            pending = list(group_files)
        if reference_steps_required:
            anchor = _sequence_alignment_anchor(pending)
            pending = [anchor, *(path for path in pending if path != anchor)]
        queue = _chunk_html_files(
            pending,
            max_files=int(settings.cherry_html_batch_max_files),
            max_pixels=int(settings.cherry_html_batch_max_pixels),
        )
        options["html_batch_policy"] = {
            "max_files": max(1, int(settings.cherry_html_batch_max_files)),
            "max_pixels": max(1, int(settings.cherry_html_batch_max_pixels)),
        }
        while queue:
            batch = queue.pop(0)
            latest = _get_run(run_id)
            if not latest or latest["status"] == "CANCELED":
                return
            try:
                result = _run_html_group_with_retries(
                    run_id,
                    src,
                    dst,
                    batch,
                    options,
                    reference_path=reference_path,
                    alignment_transform=group_alignment_transform,
                    color_transform=group_color_transform,
                    html_path=source_path,
                    expected_source_sha256=expected_source_sha256,
                    expected_reference_sha256=expected_reference_sha256,
                    reference_steps_required=reference_steps_required,
                    expected_output=expected_output,
                )
                _validate_html_result(
                    result,
                    reference_steps_required=reference_steps_required,
                    expected_profile=expected_output[0] if expected_output else None,
                    expected_width=expected_output[1] if expected_output else None,
                    expected_height=expected_output[2] if expected_output else None,
                )
                if reference_steps_required and group_alignment_transform is None:
                    group_alignment_transform = dict(result.alignment_transform or {})
                    saved_group_transforms[group_key] = group_alignment_transform
                if (
                    reference_steps_required
                    and str(result.color_api or "") == "sequence_transform_v2"
                    and group_color_transform is None
                ):
                    group_color_transform = dict(result.color_transform or {})
                    saved_group_color_transforms[group_key] = group_color_transform
                width, height = _parse_resize(result.resize)
                if width and height:
                    options["resize_width"] = width
                    options["resize_height"] = height
                options["inferred_profile"] = result.profile
                options["html_feather_enabled"] = result.feather_enabled
                options["html_configured_steps"] = result.steps
                options["html_steps"] = result.executed_steps
                options["source_sha256"] = result.source_sha256
                options["reference_loaded"] = result.reference_loaded
                options["reference_sha256"] = result.reference_sha256
                options["color_match_stats"] = result.color_match_stats
                options["color_api"] = result.color_api
                options["sequence_color_transform"] = group_color_transform
                options["position_alignment_enabled"] = result.alignment_enabled
                options["sequence_alignment_transform"] = group_alignment_transform
                options.setdefault("html_runs", []).append(
                    {
                        "input_dir": str(batch[0].parent),
                        "count": len(batch),
                        "profile": result.profile,
                        "resize": result.resize,
                        "feather_enabled": result.feather_enabled,
                        "configured_steps": result.steps,
                        "executed_steps": result.executed_steps,
                        "skipped_no_ref": result.skipped_no_ref,
                        "reference_loaded": result.reference_loaded,
                        "reference_sha256": result.reference_sha256,
                        "color_match_stats": result.color_match_stats,
                        "color_api": result.color_api,
                        "color_transform": result.color_transform,
                        "position_alignment_enabled": result.alignment_enabled,
                        "alignment_transform": result.alignment_transform,
                        "source_sha256": result.source_sha256,
                    }
                )
                for image_path in batch:
                    target = _output_target(src, dst, image_path)
                    if not target.exists():
                        raise FileNotFoundError(str(target))
                    processed.append(
                        {
                            "src_path": str(image_path),
                            "dst_path": str(target),
                            "rel_path": str(image_path.relative_to(src)),
                        }
                    )
                    done.add(str(image_path))
                options["processed"] = processed
                _save_progress(run_id, completed=len(processed), failed=len(errors), options=options, error="")
            except CherryBatchCapacityError as exc:
                if len(batch) <= 1:
                    raise
                midpoint = max(1, len(batch) // 2)
                left, right = batch[:midpoint], batch[midpoint:]
                options.setdefault("html_batch_splits", []).append(
                    {
                        "input_dir": str(batch[0].parent),
                        "count": len(batch),
                        "split_counts": [len(left), len(right)],
                        "error": str(exc),
                        "created_at": _now(),
                    }
                )
                _save_progress(run_id, completed=len(processed), failed=len(errors), options=options, error="")
                queue[0:0] = [left, right]
            except Exception as exc:
                for image_path in batch:
                    errors.append({"src_path": str(image_path), "rel_path": str(image_path.relative_to(src)), "error": str(exc)})
                options["errors"] = errors
                _save_progress(run_id, completed=len(processed), failed=len(errors), options=options, error=str(exc))
                _set_run_status(run_id, "FAILED")
                _notify(run_id, f"Cherry HTML 后处理失败：{batch[0].parent}\n{exc}")
                return

    final_status = "DONE_WITH_ERRORS" if errors else "DONE"
    _set_run_status(run_id, final_status)


def _run_html_group_with_retries(
    run_id: str,
    src: Path,
    dst: Path,
    pending: list[Path],
    options: dict[str, Any],
    *,
    reference_path: Path | None,
    alignment_transform: dict[str, Any] | None = None,
    color_transform: dict[str, Any] | None = None,
    html_path: Path,
    expected_source_sha256: str,
    expected_reference_sha256: str,
    reference_steps_required: bool,
    expected_output: tuple[str, int, int] | None = None,
    attempts: int = 3,
):
    from assetclaw_matting.config import settings
    from assetclaw_matting.services.cherry_html_runner import run_cherry_html

    failures: list[str] = []
    for attempt in range(1, max(1, attempts) + 1):
        latest = _get_run(run_id)
        if not latest or latest["status"] == "CANCELED":
            raise RuntimeError("Cherry retry cancelled")
        started_at = _now()
        try:
            result = run_cherry_html(
                html_path,
                src,
                dst,
                pending,
                reference_path=reference_path,
                reference_steps_required=reference_steps_required,
                alignment_transform=alignment_transform,
                color_transform=color_transform,
                expected_profile=expected_output[0] if expected_output else None,
                expected_width=expected_output[1] if expected_output else None,
                expected_height=expected_output[2] if expected_output else None,
                chrome_path=Path(settings.cherry_browser_path) if settings.cherry_browser_path else None,
                timeout_seconds=int(settings.cherry_html_timeout_seconds),
                storage_dir=Path(settings.storage_dir),
            )
            if result.source_sha256 != expected_source_sha256:
                raise RuntimeError("Cherry HTML source changed within one task")
            if reference_steps_required and result.reference_sha256.lower() != expected_reference_sha256.lower():
                raise RuntimeError("Cherry used a different character reference than the frozen task binding")
            options.setdefault("html_attempts", []).append(
                {
                    "input_dir": str(pending[0].parent),
                    "attempt": attempt,
                    "status": "SUCCEEDED",
                    "started_at": started_at,
                    "finished_at": _now(),
                }
            )
            _save_progress(run_id, options=options, error="")
            return result
        except Exception as exc:
            failures.append(str(exc))
            options.setdefault("html_attempts", []).append(
                {
                    "input_dir": str(pending[0].parent),
                    "attempt": attempt,
                    "status": "FAILED",
                    "started_at": started_at,
                    "finished_at": _now(),
                    "error": str(exc),
                }
            )
            capacity_error = _is_html_capacity_error(exc) and len(pending) > 1
            retrying = attempt < attempts and not capacity_error
            message = f"Cherry HTML 第 {attempt}/{attempts} 次失败"
            if retrying:
                message += "，将使用全新浏览器会话自动重试"
            _save_progress(run_id, options=options, error=f"{message}: {exc}")
            if capacity_error:
                raise CherryBatchCapacityError(str(exc)) from exc
            if retrying:
                time.sleep(min(2**(attempt - 1), 4))
            else:
                break
    raise RuntimeError(
        f"Cherry HTML failed after {attempts} attempts: {failures[-1] if failures else 'unknown error'}"
    )


def _chunk_html_files(files: list[Path], *, max_files: int, max_pixels: int) -> list[list[Path]]:
    file_limit = max(1, int(max_files))
    pixel_limit = max(1, int(max_pixels))
    batches: list[list[Path]] = []
    current: list[Path] = []
    current_pixels = 0
    for path in files:
        pixels = _image_pixel_count(path, fallback_pixels=pixel_limit)
        if current and (len(current) >= file_limit or current_pixels + pixels > pixel_limit):
            batches.append(current)
            current = []
            current_pixels = 0
        current.append(path)
        current_pixels += pixels
    if current:
        batches.append(current)
    return batches


def _sequence_alignment_anchor(files: list[Path]) -> Path:
    """Mirror the HTML's smallest trailing-number rule over the whole sequence."""

    if not files:
        raise ValueError("sequence has no files")
    ranked: list[tuple[int, int, str, Path]] = []
    for index, path in enumerate(files):
        numbers = re.findall(r"\d+", path.name)
        number = int(numbers[-1]) if numbers else math.inf
        ranked.append((0 if numbers else 1, number, f"{index:09d}:{path.name.casefold()}", path))
    return min(ranked, key=lambda item: item[:3])[3]


def _pin_html_for_run(run_id: str, source: Path, storage_dir: Path) -> tuple[Path, str]:
    source = Path(source).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Cherry algorithm HTML not found: {source}")
    snapshot_dir = Path(storage_dir) / "cherry_run_snapshots" / run_id
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot = snapshot_dir / "cherry-postprocess.html"
    if not snapshot.is_file():
        shutil.copy2(source, snapshot)
    return snapshot.resolve(), _sha256_path(snapshot)


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _image_pixel_count(path: Path, *, fallback_pixels: int) -> int:
    try:
        with Image.open(path) as image:
            return max(1, int(image.width) * int(image.height))
    except OSError:
        # Let the HTML runner report the actual decode error. Treat an image
        # with unknown dimensions as a full batch so it cannot amplify memory
        # pressure on otherwise valid files.
        return max(1, int(fallback_pixels))


def _is_html_capacity_error(exc: Exception) -> bool:
    message = str(exc).casefold()
    return any(
        token in message
        for token in (
            "array buffer allocation failed",
            "arraybuffer allocation failed",
            "out of memory",
            "not enough memory",
            "renderer process crashed",
            "render process gone",
        )
    )


def _start_run_worker(run_id: str) -> None:
    if run_id in _WORKER_RUNS:
        return
    _WORKER_RUNS.add(run_id)
    threading.Thread(target=_run_worker, args=(run_id,), daemon=True).start()


def _start_progress_monitor(run_id: str) -> None:
    if run_id in _MONITORING_RUNS:
        return
    _MONITORING_RUNS.add(run_id)
    threading.Thread(target=_monitor_run, args=(run_id,), daemon=True).start()


def _monitor_run(run_id: str) -> None:
    try:
        last_completed = -1
        while True:
            row = _get_run(run_id)
            if not row:
                return
            options = json.loads(row["options_json"] or "{}")
            if not options.get("chat_id"):
                return
            status = run_status(run_id, include_gpu=True)
            completed = int(status.get("completed") or 0)
            if completed != last_completed or status.get("status") in {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED"}:
                _notify(run_id, _format_progress_notification(status))
                last_completed = completed
            if status.get("status") in {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED"}:
                if status.get("status") in {"DONE", "DONE_WITH_ERRORS"}:
                    _notify(run_id, f"Cherry 帧序列任务完成：{status.get('completed', 0)}/{status.get('total', 0)} 张\n输出：{status.get('output_dir')}")
                return
            time.sleep(max(30, int(options.get("notify_interval_seconds") or 60)))
    finally:
        _MONITORING_RUNS.discard(run_id)


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


def _format_progress_notification(status: dict[str, Any]) -> str:
    lines = [
        f"Cherry 进度：{status.get('completed', 0)}/{status.get('total', 0)} ({status.get('progress_percent', 0)}%)",
        f"状态：{status.get('status')}",
    ]
    eta = status.get("eta_seconds")
    if isinstance(eta, int):
        lines.append(f"预计剩余：{_format_duration(eta)}")
    if status.get("last_completed"):
        lines.append(f"刚完成：{status.get('last_completed')}")
    detail = status.get("last_completed_detail") or {}
    if detail:
        lines.append(f"角色/情绪/帧：{detail.get('role')}/{detail.get('emotion')}/{detail.get('frame')}")
    gpu = status.get("gpu") or {}
    gpus = gpu.get("gpus") or []
    if gpus:
        first = gpus[0]
        lines.append(
            "GPU：显存 "
            f"{first.get('memory_used_mb')}/{first.get('memory_total_mb')} MB，"
            f"利用率 {first.get('utilization_gpu_percent')}%"
        )
    return "\n".join(lines)


def _path_detail(rel_path: str) -> dict[str, str]:
    parts = [part for part in rel_path.replace("\\", "/").split("/") if part]
    if len(parts) >= 4 and parts[-2].lower().startswith("video_"):
        role = parts[-4]
        emotion = parts[-3]
    else:
        role = parts[-3] if len(parts) >= 3 else (parts[-2] if len(parts) >= 2 else "")
        emotion = parts[-2] if len(parts) >= 2 else ""
    frame = parts[-1] if parts else ""
    return {"role": role, "emotion": emotion, "frame": frame, "rel_path": rel_path}


def _get_run(run_id: str | None = None):
    from assetclaw_matting.db.sqlite import get_connection

    with get_connection() as conn:
        if run_id:
            return conn.execute("SELECT * FROM cherry_runs WHERE id = ?", (run_id,)).fetchone()
        row = conn.execute(
            """
            SELECT * FROM cherry_runs
            WHERE status IN ('RUNNING')
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
        if row:
            return row
        return conn.execute("SELECT * FROM cherry_runs ORDER BY created_at DESC LIMIT 1").fetchone()


def _set_run_status(run_id: str, status: str) -> None:
    from assetclaw_matting.db.sqlite import get_connection

    with get_connection() as conn:
        conn.execute("UPDATE cherry_runs SET status = ?, updated_at = ? WHERE id = ?", (status, _now(), run_id))


def _save_progress(
    run_id: str,
    completed: int | None = None,
    failed: int | None = None,
    options: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    from assetclaw_matting.db.sqlite import get_connection

    updates = ["updated_at = ?"]
    values: list[Any] = [_now()]
    if completed is not None:
        updates.append("completed = ?")
        values.append(completed)
    if failed is not None:
        updates.append("failed = ?")
        values.append(failed)
    if options is not None:
        updates.append("options_json = ?")
        values.append(json.dumps(options, ensure_ascii=False))
    if error is not None:
        updates.append("error = ?")
        values.append(error)
    values.append(run_id)
    with get_connection() as conn:
        conn.execute(f"UPDATE cherry_runs SET {', '.join(updates)} WHERE id = ?", values)


def _tool_source_path() -> Path:
    from assetclaw_matting.config import settings
    from assetclaw_matting.services.cherry_html_runner import verified_cherry_html_path

    return verified_cherry_html_path(
        settings.resolved_cherry_postprocess_html_path,
        Path(settings.storage_dir),
    )


def _require_html_runtime() -> dict[str, str]:
    from assetclaw_matting.config import settings
    from assetclaw_matting.services.cherry_html_runner import validate_cherry_html_runtime

    if not settings.cherry_html_runner_enabled:
        raise RuntimeError("Cherry HTML runner is disabled")
    return validate_cherry_html_runtime(
        _tool_source_path(),
        Path(settings.cherry_browser_path) if settings.cherry_browser_path else None,
    )


def _collect_images(root: Path, recursive: bool, max_images: int) -> list[Path]:
    iterator = root.rglob("*") if recursive else root.iterdir()
    files = [path for path in iterator if path.is_file() and path.suffix.lower() in IMAGE_EXTS]
    return sorted(files, key=lambda path: str(path.relative_to(root)).lower())[: max(1, min(max_images, 50000))]


def _group_sequences(root: Path, files: list[Path]) -> list[list[Path]]:
    groups: dict[Path, list[Path]] = defaultdict(list)
    for path in files:
        groups[path.parent.relative_to(root)].append(path)
    return [sorted(paths, key=lambda path: path.name.lower()) for _rel, paths in sorted(groups.items(), key=lambda item: str(item[0]).lower())]


def _output_target(src: Path, dst: Path, image_path: Path) -> Path:
    return dst / image_path.relative_to(src).with_suffix(".png")


def _parse_resize(value: str) -> tuple[int | None, int | None]:
    try:
        left, right = str(value).lower().split("x", 1)
        return int(left), int(right)
    except Exception:
        return None, None


def _require_color_reference(reference_path: str | Path | None) -> Path:
    if not str(reference_path or "").strip():
        raise ValueError("reference_path is required for Cherry color matching")
    reference = validate_path(str(reference_path), must_exist=True)
    if not reference.is_file():
        raise ValueError("reference_path must be an image file")
    if reference.suffix.lower() not in {".png", ".webp", ".jpg", ".jpeg"}:
        raise ValueError(f"unsupported Cherry color reference format: {reference.suffix}")
    try:
        with Image.open(reference) as image:
            image.verify()
    except Exception as exc:
        raise ValueError(f"invalid Cherry color reference image: {reference}") from exc
    return reference


def _require_expected_output_profile(options: dict[str, Any]) -> tuple[str, int, int]:
    """Resolve the immutable output contract for a reference-backed run.

    Reference color/alignment assets are body-profile specific.  Falling back
    to Cherry's input-shape auto detection can therefore pair a full-body
    reference with a half-body output (or the reverse), so these runs must
    name the profile explicitly.
    """

    raw_profile = options.get("expected_profile")
    if raw_profile is None:
        raw_profile = options.get("profile")
    if raw_profile is None:
        raw_profile = options.get("preset")
    profile = str(raw_profile or "").strip().lower()
    if profile not in {"full", "half"}:
        raise ValueError("reference-enabled Cherry run requires explicit profile='full' or profile='half'; auto is forbidden")
    width, height = (384, 512) if profile == "full" else (256, 256)
    supplied_width = options.get("expected_width", options.get("target_width"))
    supplied_height = options.get("expected_height", options.get("target_height"))
    if supplied_width is not None and int(supplied_width) != width:
        raise ValueError(f"expected_width conflicts with Cherry {profile} profile ({width}x{height})")
    if supplied_height is not None and int(supplied_height) != height:
        raise ValueError(f"expected_height conflicts with Cherry {profile} profile ({width}x{height})")
    return profile, width, height


def _lock_expected_output(options: dict[str, Any], profile: str, width: int, height: int) -> None:
    """Pin all persisted/output-facing fields to one canonical profile."""

    options["profile"] = profile
    options["auto_profile_by_size"] = False
    options["expected_profile"] = profile
    options["expected_width"] = int(width)
    options["expected_height"] = int(height)
    options["expected_resize"] = f"{int(width)}x{int(height)}"
    options["use_resize2"] = True
    options["resize2_width"] = int(width)
    options["resize2_height"] = int(height)
    options["use_resize"] = True
    options["resize_width"] = int(width)
    options["resize_height"] = int(height)
    options["html_feather_enabled"] = profile == "full"
    modules = [str(step) for step in (options.get("html_modules") or []) if str(step) != "feather"]
    if profile == "full":
        insertion = modules.index("blur") if "blur" in modules else max(0, len(modules) - 2)
        modules.insert(insertion, "feather")
    options["html_modules"] = modules


def _validate_html_result(
    result: Any,
    *,
    reference_steps_required: bool = True,
    expected_profile: str | None = None,
    expected_width: int | None = None,
    expected_height: int | None = None,
) -> None:
    executed = [str(step) for step in (result.executed_steps or [])]
    if expected_profile is not None:
        expected_resize = f"{int(expected_width or 0)}x{int(expected_height or 0)}"
        if str(result.profile) != expected_profile or str(result.resize) != expected_resize:
            raise RuntimeError(
                f"Cherry output profile mismatch: expected {expected_profile} {expected_resize}, "
                f"got {result.profile} {result.resize}"
            )
    if not reference_steps_required:
        if result.alignment_enabled or "align" in executed or "colormatch" in executed:
            raise RuntimeError("Cherry unexpectedly executed reference-dependent steps")
        return
    if not result.reference_loaded:
        raise RuntimeError("Cherry returned output without a loaded color reference")
    if result.skipped_no_ref:
        raise RuntimeError(f"Cherry skipped reference-dependent steps: {', '.join(result.skipped_no_ref)}")
    if not result.alignment_enabled or "align" not in executed:
        raise RuntimeError("Cherry reference alignment was not executed")
    if executed[-2:] != ["colormatch", "align"]:
        raise RuntimeError("Cherry color matching and alignment were not the final executed steps in order")
    color_stats = result.color_match_stats or {}
    calls = int(color_stats.get("calls") or 0)
    applied = int(color_stats.get("applied") or 0)
    insufficient = int(color_stats.get("insufficient") or 0)
    if insufficient or calls != int(result.total) or applied != calls:
        raise RuntimeError("Cherry color matching did not apply to every frame")
    if str(getattr(result, "color_api", "") or "") == "sequence_transform_v2":
        color_transform = result.color_transform or {}
        if str(color_transform.get("method") or "") not in {"rgb", "lab", "lab_L"}:
            raise RuntimeError("Cherry returned an invalid sequence color transform")
        for key in ("A", "B"):
            values = color_transform.get(key)
            if not isinstance(values, (list, tuple)) or len(values) != 3:
                raise RuntimeError("Cherry returned an invalid sequence color transform")
            try:
                if not all(math.isfinite(float(value)) for value in values):
                    raise RuntimeError("Cherry returned an invalid sequence color transform")
            except (TypeError, ValueError) as exc:
                raise RuntimeError("Cherry returned an invalid sequence color transform") from exc
    transform = result.alignment_transform or {}
    for key in ("s", "tx", "ty"):
        try:
            value = float(transform.get(key))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Cherry returned an invalid alignment transform") from exc
        if not math.isfinite(value) or (key == "s" and value <= 0):
            raise RuntimeError("Cherry returned an invalid alignment transform")


def _default_options() -> dict[str, Any]:
    return preset_options("auto")


def preset_options(profile: str = "full", use_smooth: bool = False) -> dict[str, Any]:
    normalized = str(profile or "full").lower()
    auto_profile = normalized in {"auto", "adaptive", "size"}
    is_half = normalized in {"half", "emoji", "square"}
    width, height = (256, 256) if is_half else (384, 512)
    return {
        "engine": "headless_chrome_html",
        "source": "cherry-postprocess.html",
        "profile": "auto" if auto_profile else ("half" if is_half else "full"),
        "auto_profile_by_size": auto_profile,
        "use_denoise": True,
        "denoise_threshold": 1.0,
        "denoise_radius": 9,
        "use_shadow": not is_half,
        "shadow_gray_limit": 0.35,
        "shadow_protect_radius": -70,
        "shadow_alpha_boost": 1.0,
        "shadow_blur_radius": 2,
        "shadow_blur_sigma": 2.4,
        "use_blur": True,
        "blur_radius": 14,
        "blur_sigma": 12.0,
        "use_resize1": False,
        "resize1_width": width,
        "resize1_height": height,
        "use_sharp1": False,
        "sharp1_amount": 0.3,
        "sharp1_radius": 2,
        "sharp1_threshold": 0.02,
        "sharp1_shrink": 11,
        "use_resize2": True,
        "resize2_width": width,
        "resize2_height": height,
        "use_sharp2": False,
        "sharp2_amount": 0.3,
        "sharp2_radius": 2,
        "sharp2_threshold": 0.02,
        "sharp2_shrink": 11,
        "use_smooth": bool(use_smooth),
        "smooth_window": 7,
        "smooth_sigma": 1.5,
        "min_alpha": 0.05,
        "sync_rgb": False,
        "ring_width": 10,
        "smooth_method": "html_default_off",
        "fill_gap": True,
        "bg_thresh": 0.02,
        "use_resize": True,
        "resize_width": width,
        "resize_height": height,
        "use_sharpen": False,
        "sharpen_amount": 0.3,
        "sharpen_radius": 2,
        "sharpen_threshold": 0.02,
        "sharpen_shrink": 11,
        "html_modules": ["fringe", "hairinset", *([] if is_half else ["feather"]), "blur", "resize2", "colormatch", "align"],
        "html_feather_enabled": not is_half,
        "html_colormatch_enabled": True,
        "html_align_enabled": True,
    }


def _merge_options(options: dict[str, Any]) -> dict[str, Any]:
    profile = options.get("profile") or options.get("preset")
    if profile is not None:
        merged = preset_options(str(profile), use_smooth=bool(options.get("use_smooth", False)))
    else:
        merged = _default_options()
    aliases = {
        "preset": "profile",
        "auto_size_profile": "auto_profile_by_size",
        "window_size": "smooth_window",
        "sigma": "smooth_sigma",
        "use_clean": "use_denoise",
        "use_denoise_alpha": "use_denoise",
        "use_shadowsep": "use_shadow",
        "denoise_thresh": "denoise_threshold",
        "dn_thresh": "denoise_threshold",
        "denoise_smooth_radius": "denoise_radius",
        "dn_radius": "denoise_radius",
        "use_item_shadow": "use_shadow",
        "sep_gray": "shadow_gray_limit",
        "sep_protect": "shadow_protect_radius",
        "sep_boost": "shadow_alpha_boost",
        "shadow_gray_upper": "shadow_gray_limit",
        "shadow_protect": "shadow_protect_radius",
        "shadow_boost": "shadow_alpha_boost",
        "resize_w": "resize_width",
        "resize_h": "resize_height",
        "use_resize_1": "use_resize1",
        "resize1_w": "resize1_width",
        "resize1_h": "resize1_height",
        "use_resize_2": "use_resize2",
        "resize2_w": "resize2_width",
        "resize2_h": "resize2_height",
        "use_sharp_1": "use_sharp1",
        "sharp1_thresh": "sharp1_threshold",
        "use_sharp_2": "use_sharp2",
        "sharp2_thresh": "sharp2_threshold",
        "sharp_amount": "sharpen_amount",
        "sharp_radius": "sharpen_radius",
        "sharp_thresh": "sharpen_threshold",
        "sharp_shrink": "sharpen_shrink",
    }
    for key, value in options.items():
        normalized = aliases.get(key, key)
        if normalized in merged and value is not None:
            merged[normalized] = value
    if "use_resize" in options:
        merged["use_resize1"] = bool(options.get("use_resize"))
        merged["use_resize2"] = bool(options.get("use_resize"))
    if "use_sharpen" in options:
        merged["use_sharp1"] = bool(options.get("use_sharpen"))
        merged["use_sharp2"] = bool(options.get("use_sharpen"))
    if "resize_width" in options:
        merged["resize2_width"] = int(options.get("resize_width"))
    if "resize_height" in options:
        merged["resize2_height"] = int(options.get("resize_height"))
    if "sharpen_amount" in options:
        merged["sharp2_amount"] = float(options.get("sharpen_amount"))
    if "sharpen_radius" in options:
        merged["sharp2_radius"] = int(options.get("sharpen_radius"))
    if "sharpen_threshold" in options:
        merged["sharp2_threshold"] = float(options.get("sharpen_threshold"))
    if "sharpen_shrink" in options:
        merged["sharp2_shrink"] = int(options.get("sharpen_shrink"))
    for key in (
        "use_denoise",
        "use_blur",
        "use_resize1",
        "use_sharp1",
        "use_shadow",
        "use_resize2",
        "use_sharp2",
        "use_smooth",
        "sync_rgb",
        "fill_gap",
        "use_resize",
        "use_sharpen",
        "auto_profile_by_size",
    ):
        merged[key] = bool(merged[key])
    for key in (
        "denoise_radius",
        "blur_radius",
        "resize1_width",
        "resize1_height",
        "sharp1_radius",
        "sharp1_shrink",
        "shadow_protect_radius",
        "shadow_blur_radius",
        "resize2_width",
        "resize2_height",
        "sharp2_radius",
        "sharp2_shrink",
        "smooth_window",
        "ring_width",
        "resize_width",
        "resize_height",
        "sharpen_radius",
        "sharpen_shrink",
    ):
        merged[key] = int(merged[key])
    for key in (
        "denoise_threshold",
        "blur_sigma",
        "sharp1_amount",
        "sharp1_threshold",
        "shadow_gray_limit",
        "shadow_alpha_boost",
        "shadow_blur_sigma",
        "sharp2_amount",
        "sharp2_threshold",
        "smooth_sigma",
        "min_alpha",
        "bg_thresh",
        "sharpen_amount",
        "sharpen_threshold",
    ):
        merged[key] = float(merged[key])
    modules = [str(step) for step in (merged.get("html_modules") or []) if str(step) not in {"align", "colormatch"}]
    merged["html_modules"] = [*modules, "colormatch", "align"]
    merged["html_colormatch_enabled"] = True
    merged["html_align_enabled"] = True
    return merged


def _steps_text(options: dict[str, Any]) -> str:
    if options.get("engine") == "headless_chrome_html":
        modules = options.get("html_modules") or ["fringe", "hairinset", "feather", "blur", "resize2", "colormatch", "align"]
        feather = "开" if options.get("html_feather_enabled") else "关"
        return f"HTML 默认预设，输出 {options.get('resize_width')}x{options.get('resize_height')}，feather {feather}，模块 {'/'.join(modules)}"
    steps = []
    if options.get("use_denoise"):
        steps.append(f"去噪 阈值{options.get('denoise_threshold')} 半径{options.get('denoise_radius')}")
    if options.get("use_shadow"):
        steps.append(
            f"阴影分离 灰度{options.get('shadow_gray_limit')} 保护{options.get('shadow_protect_radius')} 增强{options.get('shadow_alpha_boost')}"
        )
    if options.get("use_blur"):
        steps.append(f"模糊自叠加 半径{options.get('blur_radius')} 强度{options.get('blur_sigma')}")
    if options.get("use_resize1"):
        steps.append(f"缩小① {options.get('resize1_width')}x{options.get('resize1_height')}")
    if options.get("use_sharp1"):
        steps.append(f"锐化① 强度{options.get('sharp1_amount')}")
    if options.get("use_resize2"):
        steps.append(f"缩小② {options.get('resize2_width')}x{options.get('resize2_height')}")
    if options.get("use_sharp2"):
        steps.append(f"锐化② 强度{options.get('sharp2_amount')}")
    if options.get("use_smooth"):
        steps.append(f"时序平滑 窗口{options.get('smooth_window')} 强度{options.get('smooth_sigma')}")
    return "、".join(steps) or "无"


def _eta(elapsed: float, completed: int, total: int) -> int | None:
    if completed <= 0 or total <= completed:
        return None
    return int((elapsed / completed) * (total - completed))


def _format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h {minutes % 60}m"
