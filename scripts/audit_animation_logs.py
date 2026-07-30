from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterable


LOCAL_TZ = timezone(timedelta(hours=8))
TERMINAL = {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED", "BLOCKED"}
SUCCESS = {"DONE", "DONE_WITH_ERRORS"}


def parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 1e12:
            number /= 1000
        return datetime.fromtimestamp(number, timezone.utc)
    text = str(value).strip()
    if text.isdigit() and 10 <= len(text) <= 16:
        number = float(text)
        if len(text) >= 13:
            number /= 1000
        return datetime.fromtimestamp(number, timezone.utc)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LOCAL_TZ)
    return parsed.astimezone(timezone.utc)


def seconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return (end - start).total_seconds()


def nonnegative(value: float | None) -> float | None:
    if value is None or value < 0:
        return None
    return value


def percentile(values: Iterable[float | None], ratio: float) -> float | None:
    clean = sorted(float(value) for value in values if value is not None and math.isfinite(float(value)))
    if not clean:
        return None
    index = (len(clean) - 1) * ratio
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return clean[lower]
    return clean[lower] + (clean[upper] - clean[lower]) * (index - lower)


def stats(values: Iterable[float | None]) -> dict[str, float | int | None]:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return {
        "n": len(clean),
        "min": min(clean) if clean else None,
        "p50": percentile(clean, 0.5),
        "p90": percentile(clean, 0.9),
        "p95": percentile(clean, 0.95),
        "max": max(clean) if clean else None,
        "mean": statistics.fmean(clean) if clean else None,
    }


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def all_cherry_runs(raw: dict[str, Any]) -> list[dict[str, Any]]:
    children = raw.get("children") if isinstance(raw.get("children"), dict) else {}
    runs: list[dict[str, Any]] = []
    mapped = children.get("cherry_runs")
    if isinstance(mapped, dict):
        runs.extend(value for value in mapped.values() if isinstance(value, dict))
    single = children.get("cherry")
    if isinstance(single, dict):
        runs.append(single)
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for run in runs:
        run_id = str(run.get("run_id") or run.get("id") or id(run))
        if run_id in seen:
            continue
        seen.add(run_id)
        unique.append(run)
    return unique


def cherry_attempts(raw: dict[str, Any]) -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for run in all_cherry_runs(raw):
        options = run.get("options") if isinstance(run.get("options"), dict) else {}
        candidates = []
        if isinstance(options.get("html_attempts"), list):
            candidates.extend(options["html_attempts"])
        if isinstance(run.get("html_attempts"), list):
            candidates.extend(run["html_attempts"])
        for attempt in candidates:
            if not isinstance(attempt, dict):
                continue
            key = (
                str(attempt.get("started_at") or ""),
                str(attempt.get("finished_at") or ""),
                str(attempt.get("input_dir") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            attempts.append(attempt)
    return attempts


def source_mtime(raw: dict[str, Any]) -> datetime | None:
    candidates: list[datetime] = []
    for key in ("videos", "images"):
        for item in raw.get(key) or []:
            if not isinstance(item, dict):
                continue
            path = Path(str(item.get("source_path") or ""))
            try:
                candidates.append(datetime.fromtimestamp(path.stat().st_mtime, timezone.utc))
            except OSError:
                pass
    return max(candidates) if candidates else None


def output_bytes(raw: dict[str, Any]) -> int | None:
    delivery = raw.get("delivery") if isinstance(raw.get("delivery"), dict) else {}
    if delivery.get("file_size") is not None:
        try:
            return int(delivery["file_size"])
        except (TypeError, ValueError):
            pass
    path = Path(str(raw.get("zip_path") or ""))
    try:
        return path.stat().st_size
    except OSError:
        return None


def declared_cherry_ids(raw: dict[str, Any]) -> list[str]:
    children = raw.get("children") if isinstance(raw.get("children"), dict) else {}
    values = []
    if isinstance(children.get("cherry_run_ids"), list):
        values.extend(children["cherry_run_ids"])
    values.append(children.get("cherry_run_id"))
    return list(dict.fromkeys(str(value) for value in values if value))


def direct_record(
    path: Path,
    module: str,
    comfy_db: dict[str, dict[str, Any]],
    cherry_db: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    raw = load_json(path)
    if not raw:
        return None
    task_id = str(raw.get("id") or path.parent.name)
    status = str(raw.get("status") or "UNKNOWN").upper()
    created = parse_time(raw.get("created_at"))
    updated = parse_time(raw.get("updated_at"))
    delivery = raw.get("delivery") if isinstance(raw.get("delivery"), dict) else {}
    drive_file = raw.get("drive_file") if isinstance(raw.get("drive_file"), dict) else {}
    delivered = parse_time(
        drive_file.get("create_time")
        or delivery.get("delivered_at")
        or delivery.get("sent_at")
        or delivery.get("completed_at")
    )
    end = delivered or (updated if status in TERMINAL else None)
    items = raw.get("videos") if module == "video" else raw.get("images")
    items = items if isinstance(items, list) else []
    volume = sum(int(item.get("frame_count") or 0) for item in items if isinstance(item, dict)) if module == "video" else len(items)
    package_as_sequence = bool(raw.get("package_as_sequence"))
    category = "video_direct" if module == "video" else ("sequence_zip" if package_as_sequence else "image_direct")

    children = raw.get("children") if isinstance(raw.get("children"), dict) else {}
    comfy = children.get("comfyui") if isinstance(children.get("comfyui"), dict) else {}
    backend_raw = str(comfy.get("backend") or raw.get("matting_backend") or "").lower()
    if backend_raw == "gpu_control" or "cluster" in backend_raw or "remote" in backend_raw:
        backend = "cluster"
    elif backend_raw or comfy:
        backend = "local"
    else:
        backend = "unknown"

    handshake = comfy.get("backend_handshake") if isinstance(comfy.get("backend_handshake"), dict) else {}
    capacity = handshake.get("capacity") if isinstance(handshake.get("capacity"), dict) else {}
    comfy_run_id = str(comfy.get("run_id") or children.get("comfyui_run_id") or "")
    db_comfy = comfy_db.get(comfy_run_id, {})
    db_options = db_comfy.get("options") if isinstance(db_comfy.get("options"), dict) else {}
    matting_start = parse_time(
        handshake.get("checked_at")
        or comfy.get("started_at")
        or comfy.get("created_at")
        or db_comfy.get("created_at")
    )
    matting_end = parse_time(
        comfy.get("finished_at")
        or comfy.get("completed_at")
        or comfy.get("updated_at")
        or db_comfy.get("updated_at")
    )
    attempts = cherry_attempts(raw)
    post_starts = [parse_time(item.get("started_at")) for item in attempts]
    post_ends = [parse_time(item.get("finished_at")) for item in attempts]
    valid_post_starts = [item for item in post_starts if item]
    valid_post_ends = [item for item in post_ends if item]
    db_cherry_rows = [cherry_db[run_id] for run_id in declared_cherry_ids(raw) if run_id in cherry_db]
    db_cherry_starts = [parse_time(row.get("created_at")) for row in db_cherry_rows]
    db_cherry_ends = [parse_time(row.get("updated_at")) for row in db_cherry_rows]
    valid_post_starts.extend(item for item in db_cherry_starts if item)
    valid_post_ends.extend(item for item in db_cherry_ends if item)
    post_start = min(valid_post_starts) if valid_post_starts else None
    post_end = max(valid_post_ends) if valid_post_ends else None
    attempt_sum = sum(
        max(0.0, value)
        for value in (seconds(parse_time(item.get("started_at")), parse_time(item.get("finished_at"))) for item in attempts)
        if value is not None
    )
    gpu_control = db_options.get("gpu_control") if isinstance(db_options.get("gpu_control"), dict) else {}
    node_distribution = comfy.get("node_distribution") if isinstance(comfy.get("node_distribution"), dict) else {}
    if isinstance(gpu_control.get("node_distribution"), dict):
        node_distribution = gpu_control["node_distribution"]
    dist_total = sum(int(value or 0) for value in node_distribution.values())

    remote_created = parse_time(gpu_control.get("created_at"))
    remote_started = parse_time(gpu_control.get("started_at"))
    remote_finished = parse_time(gpu_control.get("finished_at"))
    remote_published = parse_time(gpu_control.get("published_at"))
    db_comfy_created = parse_time(db_comfy.get("created_at"))
    prompt_map = db_options.get("prompt_map") if isinstance(db_options.get("prompt_map"), list) else []
    prompt_durations = []
    for item in prompt_map:
        if not isinstance(item, dict):
            continue
        try:
            duration = float(item.get("duration_seconds"))
        except (TypeError, ValueError):
            continue
        if duration >= 0 and math.isfinite(duration):
            prompt_durations.append(duration)

    prepare_s = nonnegative(seconds(created, matting_start))
    matting_s = nonnegative(seconds(matting_start, matting_end))
    handoff_s = nonnegative(seconds(matting_end, post_start))
    postprocess_s = nonnegative(seconds(post_start, post_end))
    postprocess_idle_gap_s = (
        max(0.0, postprocess_s - attempt_sum)
        if postprocess_s is not None and attempt_sum
        else None
    )
    delivery_s = nonnegative(seconds(post_end or matting_end, delivered or end))
    e2e_s = nonnegative(seconds(created, end))
    download_to_create_s = nonnegative(seconds(source_mtime(raw), created))
    measured = sum(value or 0 for value in (prepare_s, matting_s, handoff_s, postprocess_s, delivery_s))
    ordered_points = [point for point in (created, matting_start, matting_end, post_start, post_end, end) if point]
    timeline_consistent = all(left <= right for left, right in zip(ordered_points, ordered_points[1:]))
    timeline_overrun_s = max(0.0, measured - e2e_s) if e2e_s is not None else None

    dimensions = Counter()
    for item in items:
        if not isinstance(item, dict):
            continue
        width = item.get("width")
        height = item.get("height")
        size = item.get("cherry_output_size")
        if width and height:
            dimensions[f"{width}x{height}"] += 1
        elif size:
            dimensions[str(size)] += 1

    return {
        "task_id": task_id,
        "category": category,
        "status": status,
        "stage": str(raw.get("stage") or ""),
        "name": str(raw.get("run_label") or task_id),
        "created_at": created.isoformat() if created else "",
        "end_at": end.isoformat() if end else "",
        "delivered": bool(delivered),
        "delivery_method": str(delivery.get("delivery_method") or drive_file.get("delivery_method") or ""),
        "volume": volume,
        "backend": backend,
        "backend_reason": str(comfy.get("backend_selection_reason") or ""),
        "comfy_run_id": comfy_run_id,
        "comfy_status": str(comfy.get("status") or ""),
        "comfy_db_available": bool(db_comfy),
        "queue_depth_at_handshake": int(capacity.get("queue_depth") or 0),
        "active_batches_at_handshake": int(capacity.get("active_batches") or 0),
        "idle_nodes_at_handshake": int(capacity.get("idle_nodes") or 0),
        "online_nodes_at_handshake": int(capacity.get("online_nodes") or 0),
        "node_count_used": len([value for value in node_distribution.values() if int(value or 0) > 0]),
        "node_distribution": json.dumps(node_distribution, ensure_ascii=False, sort_keys=True),
        "node_distribution_total": dist_total,
        "input_dimensions": ";".join(f"{key}:{value}" for key, value in dimensions.most_common()),
        "output_bytes": output_bytes(raw),
        "source_download_to_task_create_s": download_to_create_s,
        "prepare_or_extract_s": prepare_s,
        "matting_combined_s": matting_s,
        "cluster_client_prepare_upload_s": nonnegative(seconds(db_comfy_created, remote_created)),
        "cluster_remote_queue_s": nonnegative(seconds(remote_created, remote_started)),
        "cluster_gpu_execution_s": nonnegative(seconds(remote_started, remote_finished)),
        "cluster_result_return_publish_s": nonnegative(seconds(remote_finished, remote_published)),
        "cluster_gpu_items_per_min": (
            volume * 60 / nonnegative(seconds(remote_started, remote_finished))
            if volume and nonnegative(seconds(remote_started, remote_finished))
            else None
        ),
        "cluster_workflow_version": str(gpu_control.get("workflow_version") or ""),
        "cluster_pipeline_commit": str(gpu_control.get("pipeline_commit") or "")[:12],
        "local_prompt_samples": len(prompt_durations),
        "local_prompt_latency_p50_s": percentile(prompt_durations, 0.5),
        "local_prompt_latency_p90_s": percentile(prompt_durations, 0.9),
        "local_prompt_latency_mean_s": statistics.fmean(prompt_durations) if prompt_durations else None,
        "local_gpu_items_per_min": (60 / statistics.fmean(prompt_durations)) if prompt_durations else None,
        "handoff_s": handoff_s,
        "postprocess_envelope_s": postprocess_s,
        "postprocess_attempt_sum_s": attempt_sum or None,
        "postprocess_idle_gap_s": postprocess_idle_gap_s,
        "postprocess_attempts": len(attempts),
        "delivery_after_post_s": delivery_s,
        "e2e_task_s": e2e_s,
        "measured_coverage": min(1.0, measured / e2e_s) if e2e_s else None,
        "timeline_consistent": timeline_consistent,
        "timeline_overrun_s": timeline_overrun_s,
        "matting_items_per_min": (volume * 60 / matting_s) if volume and matting_s else None,
        "postprocess_items_per_min": (volume * 60 / postprocess_s) if volume and postprocess_s else None,
        "postprocess_active_items_per_min": (volume * 60 / attempt_sum) if volume and attempt_sum else None,
        "error": str(raw.get("error") or comfy.get("last_error") or "").replace("\n", " ")[:500],
        "status_path": str(path.relative_to(path.parents[2])),
    }


def flow_record(path: Path) -> dict[str, Any] | None:
    raw = load_json(path)
    if not raw:
        return None
    created = parse_time(raw.get("created_at"))
    updated = parse_time(raw.get("updated_at"))
    stages = raw.get("stages") if isinstance(raw.get("stages"), list) else []
    timed = 0
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        if parse_time(stage.get("started_at") or stage.get("created_at")) and parse_time(
            stage.get("finished_at") or stage.get("completed_at") or stage.get("updated_at")
        ):
            timed += 1
    return {
        "task_id": str(raw.get("id") or path.stem),
        "status": str(raw.get("status") or "UNKNOWN").upper(),
        "current_stage": str(raw.get("current_stage") or ""),
        "created_at": created.isoformat() if created else "",
        "updated_at": updated.isoformat() if updated else "",
        "lifetime_s": nonnegative(seconds(created, updated)),
        "stage_count": len(stages),
        "timed_stage_count": timed,
        "child_count": len(raw.get("children") or {}),
        "error": str(raw.get("error") or "").replace("\n", " ")[:1000],
        "workflow_path": str(raw.get("workflow_path") or ""),
        "is_debug_workflow": "storage/debug/current_animation_workflow.json" in str(raw.get("workflow_path") or "").replace("\\", "/").lower(),
        "status_path": str(path.relative_to(path.parents[2])),
    }


def robust_outliers(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    metrics = [
        "e2e_task_s",
        "prepare_or_extract_s",
        "matting_combined_s",
        "handoff_s",
        "postprocess_envelope_s",
        "delivery_after_post_s",
    ]
    result: list[dict[str, Any]] = []
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        if row["status"] in SUCCESS:
            groups[(row["category"], row["backend"])].append(row)
    for (category, backend), rows in groups.items():
        for metric in metrics:
            normalized = []
            for row in rows:
                value = row.get(metric)
                volume = row.get("volume") or 0
                if value is not None and volume > 0:
                    normalized.append((row, float(value) / volume))
            if len(normalized) < 5:
                continue
            values = [value for _, value in normalized]
            median = statistics.median(values)
            deviations = [abs(value - median) for value in values]
            mad = statistics.median(deviations)
            p95 = percentile(values, 0.95) or 0
            for row, value in normalized:
                robust_z = (0.6745 * (value - median) / mad) if mad > 0 else 0.0
                if robust_z >= 3.5 or (value >= p95 and value > median * 2):
                    result.append({
                        "task_id": row["task_id"],
                        "name": row["name"],
                        "category": category,
                        "backend": backend,
                        "metric": metric,
                        "value_s_per_item": value,
                        "group_median_s_per_item": median,
                        "multiple_of_median": value / median if median > 0 else None,
                        "robust_z": robust_z,
                        "reason": "robust_z>=3.5" if robust_z >= 3.5 else "p95_and_2x_median",
                    })
    result.sort(key=lambda item: (item.get("multiple_of_median") or 0), reverse=True)
    return result


def database_inventory(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return {"available": False}
    result: dict[str, Any] = {"available": True, "path": str(db_path), "tables": {}}
    with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as conn:
        table_names = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for name in table_names:
            try:
                result["tables"][name] = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            except sqlite3.DatabaseError:
                result["tables"][name] = None
        if "comfyui_runs" in table_names:
            result["comfyui_statuses"] = dict(conn.execute("SELECT status, COUNT(*) FROM comfyui_runs GROUP BY status"))
        if "brain_messages" in table_names:
            result["brain_message_time_range"] = conn.execute(
                "SELECT MIN(created_at), MAX(created_at), COUNT(*) FROM brain_messages"
            ).fetchone()
    return result


def load_run_db_maps(db_path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    comfy: dict[str, dict[str, Any]] = {}
    cherry: dict[str, dict[str, Any]] = {}
    if not db_path.exists():
        return comfy, cherry
    with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute("SELECT id, status, total, options_json, created_at, updated_at FROM comfyui_runs"):
            item = dict(row)
            try:
                item["options"] = json.loads(item.pop("options_json") or "{}")
            except json.JSONDecodeError:
                item["options"] = {}
            comfy[str(item["id"])] = item
        for row in conn.execute("SELECT id, status, total, created_at, updated_at FROM cherry_runs"):
            item = dict(row)
            cherry[str(item["id"])] = item
    return comfy, cherry


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def grouped_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        groups["all"].append(row)
        groups[f"category:{row['category']}"] .append(row)
        groups[f"backend:{row['backend']}"] .append(row)
        groups[f"category_backend:{row['category']}:{row['backend']}"] .append(row)
    for name, rows in sorted(groups.items()):
        successful = [row for row in rows if row["status"] in SUCCESS]
        stage_successful = [row for row in successful if row.get("timeline_consistent")]
        result[name] = {
            "tasks": len(rows),
            "status_counts": dict(Counter(row["status"] for row in rows)),
            "successful": len(successful),
            "delivered": sum(bool(row["delivered"]) for row in rows),
            "volume": sum(int(row["volume"] or 0) for row in rows),
            "e2e_task_s": stats(row.get("e2e_task_s") for row in successful),
            "stage_consistent_successful": len(stage_successful),
            "source_download_to_task_create_s": stats(row.get("source_download_to_task_create_s") for row in stage_successful),
            "prepare_or_extract_s": stats(row.get("prepare_or_extract_s") for row in stage_successful),
            "matting_combined_s": stats(row.get("matting_combined_s") for row in stage_successful),
            "matting_items_per_min": stats(row.get("matting_items_per_min") for row in stage_successful),
            "cluster_client_prepare_upload_s": stats(row.get("cluster_client_prepare_upload_s") for row in stage_successful),
            "cluster_remote_queue_s": stats(row.get("cluster_remote_queue_s") for row in stage_successful),
            "cluster_gpu_execution_s": stats(row.get("cluster_gpu_execution_s") for row in stage_successful),
            "cluster_result_return_publish_s": stats(row.get("cluster_result_return_publish_s") for row in stage_successful),
            "cluster_gpu_items_per_min": stats(row.get("cluster_gpu_items_per_min") for row in stage_successful),
            "local_prompt_latency_mean_s": stats(row.get("local_prompt_latency_mean_s") for row in stage_successful),
            "local_gpu_items_per_min": stats(row.get("local_gpu_items_per_min") for row in stage_successful),
            "handoff_s": stats(row.get("handoff_s") for row in stage_successful),
            "postprocess_envelope_s": stats(row.get("postprocess_envelope_s") for row in stage_successful),
            "postprocess_attempt_sum_s": stats(row.get("postprocess_attempt_sum_s") for row in stage_successful),
            "postprocess_idle_gap_s": stats(row.get("postprocess_idle_gap_s") for row in stage_successful),
            "postprocess_items_per_min": stats(row.get("postprocess_items_per_min") for row in stage_successful),
            "postprocess_active_items_per_min": stats(row.get("postprocess_active_items_per_min") for row in stage_successful),
            "delivery_after_post_s": stats(row.get("delivery_after_post_s") for row in stage_successful),
            "measured_coverage": stats(row.get("measured_coverage") for row in stage_successful),
        }
    return result


def data_quality(records: list[dict[str, Any]], flows: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [row for row in records if row["status"] in SUCCESS]
    return {
        "direct_status_files": len(records),
        "direct_successful": len(successful),
        "direct_delivered": sum(row["delivered"] for row in records),
        "missing_e2e_on_success": sum(row.get("e2e_task_s") is None for row in successful),
        "missing_matting_split_on_success": sum(row.get("matting_combined_s") is None for row in successful),
        "missing_postprocess_split_on_success": sum(row.get("postprocess_envelope_s") is None for row in successful),
        "missing_delivery_receipt_on_success": sum(not row["delivered"] for row in successful),
        "inconsistent_success_timelines": sum(not row.get("timeline_consistent") for row in successful),
        "timelines_with_stage_overrun": sum((row.get("timeline_overrun_s") or 0) > 1 for row in successful),
        "cluster_rows": sum(row["backend"] == "cluster" for row in records),
        "cluster_rows_with_node_distribution": sum(
            row["backend"] == "cluster" and row["node_distribution_total"] > 0 for row in records
        ),
        "cluster_rows_with_three_nodes_used": sum(
            row["backend"] == "cluster" and row["node_count_used"] >= 3 for row in records
        ),
        "flows": len(flows),
        "flows_with_any_timed_stage": sum(row["timed_stage_count"] > 0 for row in flows),
        "flow_status_counts": dict(Counter(row["status"] for row in flows)),
    }


def ui_cohort_summary(records: list[dict[str, Any]], flows: list[dict[str, Any]]) -> dict[str, Any]:
    images = sorted(
        (row for row in records if row["category"] != "video_direct"),
        key=lambda row: row["created_at"],
        reverse=True,
    )[:20]
    videos = sorted(
        (row for row in records if row["category"] == "video_direct"),
        key=lambda row: row["created_at"],
        reverse=True,
    )[:20]
    visible_flows = sorted(
        (row for row in flows if not row["is_debug_workflow"]),
        key=lambda row: row["created_at"],
        reverse=True,
    )[:20]
    direct = images + videos
    successful = [row for row in direct if row["status"] in SUCCESS]
    return {
        "explanation": "Matches the dashboard's hard limit: latest 20 image parents + latest 20 video parents + non-debug flow files.",
        "direct_tasks": len(direct),
        "visible_flows": len(visible_flows),
        "sample_count": len(direct) + len(visible_flows),
        "successful_direct_tasks": len(successful),
        "direct_status_counts": dict(Counter(row["status"] for row in direct)),
        "flow_status_counts": dict(Counter(row["status"] for row in visible_flows)),
        "e2e_task_s": stats(row.get("e2e_task_s") for row in successful),
        "oldest_image_created_at": images[-1]["created_at"] if images else "",
        "oldest_video_created_at": videos[-1]["created_at"] if videos else "",
    }


def speed_comparison(records: list[dict[str, Any]]) -> dict[str, Any]:
    local = [
        row for row in records
        if row["category"] == "video_direct"
        and row["status"] in SUCCESS
        and row.get("local_gpu_items_per_min") is not None
    ]
    cluster = [
        row for row in records
        if row["category"] == "video_direct"
        and row["status"] in SUCCESS
        and row.get("cluster_gpu_items_per_min") is not None
    ]
    local_values = [row["local_gpu_items_per_min"] for row in local]
    cluster_values = [row["cluster_gpu_items_per_min"] for row in cluster]
    local_stats = stats(local_values)
    cluster_stats = stats(cluster_values)
    result: dict[str, Any] = {
        "local_4070ti": local_stats,
        "cluster_pure_execution": cluster_stats,
        "median_speedup": (
            cluster_stats["p50"] / local_stats["p50"]
            if cluster_stats["p50"] and local_stats["p50"]
            else None
        ),
        "mean_speedup": (
            cluster_stats["mean"] / local_stats["mean"]
            if cluster_stats["mean"] and local_stats["mean"]
            else None
        ),
        "local_task_ids": [row["task_id"] for row in local],
        "cluster_task_ids": [row["task_id"] for row in cluster],
    }
    for nodes in (1, 2, 3):
        subset = [row["cluster_gpu_items_per_min"] for row in cluster if row["node_count_used"] == nodes]
        result[f"cluster_{nodes}_nodes"] = stats(subset)
    versions: dict[str, list[float]] = defaultdict(list)
    for row in cluster:
        versions[row.get("cluster_workflow_version") or "unknown"].append(row["cluster_gpu_items_per_min"])
    result["cluster_by_workflow_version"] = {key: stats(values) for key, values in versions.items()}
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit animation-manager task logs and state snapshots.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    root = args.root.resolve()
    out = (args.out or (root / "docs" / "animation-log-audit" / "generated")).resolve()
    out.mkdir(parents=True, exist_ok=True)

    comfy_db, cherry_db = load_run_db_maps(root / "data" / "assetclaw.db")
    records: list[dict[str, Any]] = []
    for module, directory in (("video", root / "storage" / "direct_video_runs"), ("image", root / "storage" / "direct_image_runs")):
        for path in sorted(directory.glob("*/status.json")):
            row = direct_record(path, module, comfy_db, cherry_db)
            if row:
                records.append(row)
    records.sort(key=lambda row: (row["created_at"], row["task_id"]))

    flows = [row for path in sorted((root / "storage" / "animation_flow_runs").glob("AFLOW_*.json")) if (row := flow_record(path))]
    flows.sort(key=lambda row: (row["created_at"], row["task_id"]))
    outliers = robust_outliers(records)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "method_version": "1.1",
        "data_quality": data_quality(records, flows),
        "groups": grouped_summary(records),
        "dashboard_ui_cohort": ui_cohort_summary(records, flows),
        "speed_comparison": speed_comparison(records),
        "database": database_inventory(root / "data" / "assetclaw.db"),
        "important_limitations": [
            "Task created_at starts after Feishu attachment download/routing; receive-to-download timing is not independently persisted.",
            "Parent-task JSON combines the cluster phases; this audit splits them only where comfyui_runs.options_json.gpu_control has remote timestamps.",
            "Animation-flow stage objects generally lack started_at/finished_at timestamps.",
            "Filesystem and JSON snapshots are final-state evidence, not append-only event history.",
        ],
    }

    write_csv(out / "direct_tasks.csv", records)
    write_csv(out / "animation_flows.csv", flows)
    write_csv(out / "robust_outliers.csv", outliers)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({
        "direct_tasks": len(records),
        "flows": len(flows),
        "outliers": len(outliers),
        "output": str(out),
        "data_quality": summary["data_quality"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
