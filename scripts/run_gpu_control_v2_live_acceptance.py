from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from assetclaw_matting.config import settings
from assetclaw_matting.services.gpu_control_batch import (
    GpuControlBatchClient,
    GpuControlError,
    TERMINAL_BATCH_STATUSES,
    build_input_batch,
    compact_remote_state,
    result_artifact,
    verify_and_publish_result,
)


ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass(frozen=True)
class Scenario:
    name: str
    source_root: Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _request_id(session: str, scenario: str, operation: str) -> str:
    return f"am-{session}-{scenario}-{operation}"[:64]


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".json.part")
    partial.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    partial.replace(path)


def _source_files(root: Path, limit: int) -> list[Path]:
    if not root.is_dir():
        raise FileNotFoundError(f"source root does not exist: {root}")
    files = sorted(
        (path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in ALLOWED_SUFFIXES),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    if len(files) < limit:
        raise RuntimeError(f"source root has only {len(files)} usable images, needs {limit}: {root}")
    return files[:limit]


def _prepare(
    session_root: Path,
    session: str,
    scenario: Scenario,
    frame_count: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    files = _source_files(scenario.source_root, frame_count)
    external_batch_id = f"assetclaw-live:{scenario.name}:{session}:g1"
    scenario_root = session_root / scenario.name
    prepared = build_input_batch(
        f"LIVE_{scenario.name.upper()}_{session.upper()}",
        scenario.source_root,
        files,
        scenario_root / "handoff",
        preserve_structure=True,
        external_batch_id=external_batch_id,
        parameters={},
    )
    entry = {
        "scenario": scenario.name,
        "source_root": str(scenario.source_root.resolve()),
        "frame_count": frame_count,
        "external_batch_id": external_batch_id,
        "input_archive": prepared["archive_path"],
        "input_manifest": prepared["manifest_path"],
        "input_manifest_sha256": prepared["manifest_sha256"],
        "input_bytes": Path(prepared["archive_path"]).stat().st_size,
        "output_root": str((scenario_root / "published").resolve()),
        "status": "PREPARED",
        "status_history": [],
    }
    return prepared, entry


def _create_one(client: GpuControlBatchClient, session: str, scenario: str, prepared: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    payload = client.create_batch(
        Path(prepared["archive_path"]),
        prepared["manifest"],
        idempotency_key=prepared["idempotency_key"],
        request_id=_request_id(session, scenario, "create"),
    )
    payload["_client_elapsed_seconds"] = round(time.monotonic() - started, 3)
    return payload


def _diagnostic_manifest(client: GpuControlBatchClient, session: str, scenario: str, batch_id: str) -> dict[str, Any]:
    try:
        return client.get_batch_manifest(
            batch_id,
            offset=0,
            limit=500,
            request_id=_request_id(session, scenario, "manifest"),
        )
    except Exception as exc:
        return {"diagnostic_error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Submit four isolated GPU Control V2 real acceptance batches.")
    parser.add_argument("--frames-per-task", type=int, default=6)
    parser.add_argument("--poll-seconds", type=float, default=3.0)
    parser.add_argument("--timeout-seconds", type=int, default=7200)
    parser.add_argument("--session", default="")
    parser.add_argument("--prepare-only", action="store_true", help="Build and validate four handoffs without network calls.")
    parser.add_argument("--api-key", default="", help="Process-only override; never writes .env.")
    parser.add_argument("--ca-bundle", default="", help="Process-only CA bundle override; never writes .env.")
    parser.add_argument("--allow-ca-without-key-usage", action="store_true")
    parser.add_argument("--direct-image-root", type=Path, default=Path("storage/direct_image_runs/IMG_BD87116ADA5C/original_images"))
    parser.add_argument("--direct-video-root", type=Path, default=Path("storage/direct_video_runs/VID_FB060F557EB0/frames/video_01"))
    parser.add_argument("--sequence-zip-root", type=Path, default=Path("storage/direct_image_imports/兴奋跳动关键帧_b1ffbe99aa"))
    parser.add_argument("--one-click-root", type=Path, default=Path(r"C:\animation_auto\2026-07-21\scene\frames\heather-say_hi"))
    args = parser.parse_args()
    if not 1 <= args.frames_per_task <= 5000:
        parser.error("--frames-per-task must be 1-5000")
    if args.api_key:
        settings.gpu_control_api_key = args.api_key
    if args.ca_bundle:
        ca_path = Path(args.ca_bundle).resolve()
        if not ca_path.is_file():
            parser.error(f"CA bundle does not exist: {ca_path}")
        settings.gpu_control_ca_bundle = str(ca_path)
        settings.gpu_control_verify_tls = True
    if args.allow_ca_without_key_usage:
        settings.gpu_control_allow_ca_without_key_usage = True

    session = args.session or f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
    session_root = Path(settings.storage_dir).resolve() / "gpu_control_live_tests" / session
    report_path = session_root / "report.json"
    scenarios = [
        Scenario("direct-image", args.direct_image_root.resolve()),
        Scenario("direct-video", args.direct_video_root.resolve()),
        Scenario("sequence-zip", args.sequence_zip_root.resolve()),
        Scenario("one-click-animation", args.one_click_root.resolve()),
    ]
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "session": session,
        "started_at": _now(),
        "base_url": settings.gpu_control_base_url,
        "tls_verify": True,
        "api_identity": "api_key" if settings.gpu_control_api_key else "source_ip",
        "local_backend_touched": False,
        "application_database_touched": False,
        "scenarios": {},
    }

    prepared_by_name: dict[str, dict[str, Any]] = {}
    for scenario in scenarios:
        prepared, entry = _prepare(session_root, session, scenario, args.frames_per_task)
        prepared_by_name[scenario.name] = prepared
        report["scenarios"][scenario.name] = entry
    if len({item["external_batch_id"] for item in report["scenarios"].values()}) != len(scenarios):
        raise RuntimeError("external_batch_id collision across scenarios")
    _write_report(report_path, report)
    print(f"REPORT {report_path}", flush=True)
    if args.prepare_only:
        report["finished_at"] = _now()
        report["result"] = "PREPARED"
        _write_report(report_path, report)
        print(f"FINISHED result=PREPARED report={report_path}", flush=True)
        return 0

    client = GpuControlBatchClient()
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="gpu-control-live") as pool:
        futures = {
            pool.submit(_create_one, client, session, name, prepared): name
            for name, prepared in prepared_by_name.items()
        }
        for future in as_completed(futures):
            name = futures[future]
            entry = report["scenarios"][name]
            try:
                created = future.result()
                batch_id = str(created.get("batch_id") or "")
                if not batch_id:
                    raise GpuControlError("create response has no batch_id")
                if str(created.get("external_batch_id") or "") != entry["external_batch_id"]:
                    raise GpuControlError("create response external_batch_id mismatch")
                entry["batch_id"] = batch_id
                entry["create"] = created
                entry["status"] = str(created.get("status") or "QUEUED").upper()
                print(f"CREATED scenario={name} batch_id={batch_id} status={entry['status']}", flush=True)
            except Exception as exc:
                entry["status"] = "CLIENT_CREATE_FAILED"
                entry["error"] = str(exc)
                print(f"CREATE_FAILED scenario={name} error={exc}", flush=True)
            _write_report(report_path, report)

    active = {
        name for name, entry in report["scenarios"].items()
        if entry.get("batch_id") and entry.get("status") not in TERMINAL_BATCH_STATUSES
    }
    deadline = time.monotonic() + args.timeout_seconds
    last_summary = ""
    while active and time.monotonic() < deadline:
        for name in list(active):
            entry = report["scenarios"][name]
            try:
                payload = client.get_batch(entry["batch_id"], request_id=_request_id(session, name, "poll"))
                if str(payload.get("batch_id") or "") != entry["batch_id"]:
                    raise GpuControlError("status response batch_id mismatch")
                if str(payload.get("external_batch_id") or "") != entry["external_batch_id"]:
                    raise GpuControlError("status response external_batch_id mismatch")
                compact = compact_remote_state(payload)
                entry["status"] = compact["status"]
                entry["latest_status"] = compact
                history_key = [compact["status"], compact["counts"], compact["node_distribution"]]
                if not entry["status_history"] or entry["status_history"][-1].get("key") != history_key:
                    entry["status_history"].append({"at": _now(), "key": history_key, **compact})
                if compact["status"] in TERMINAL_BATCH_STATUSES:
                    active.remove(name)
            except Exception as exc:
                entry["last_poll_error"] = str(exc)
        _write_report(report_path, report)
        summary = " | ".join(
            f"{name}:{entry.get('status')}:{entry.get('latest_status', {}).get('counts', {})}"
            for name, entry in report["scenarios"].items()
        )
        if summary != last_summary:
            print(f"STATUS {summary}", flush=True)
            last_summary = summary
        if active:
            time.sleep(max(0.5, args.poll_seconds))

    if active:
        for name in active:
            report["scenarios"][name]["status"] = "CLIENT_TIMEOUT"
            report["scenarios"][name]["error"] = "acceptance polling timeout; remote batch was not cancelled"

    failures = 0
    for name, entry in report["scenarios"].items():
        if entry.get("status") != "SUCCEEDED":
            failures += 1
            if entry.get("batch_id"):
                entry["diagnostic_manifest"] = _diagnostic_manifest(client, session, name, entry["batch_id"])
            continue
        prepared = prepared_by_name[name]
        payload = client.get_batch(entry["batch_id"], request_id=_request_id(session, name, "final"))
        artifact = result_artifact(payload)
        result_zip = session_root / name / "result.zip"
        download = client.download_artifact(artifact, result_zip, request_id=_request_id(session, name, "download"))
        published = verify_and_publish_result(
            result_zip,
            str(artifact["sha256"]),
            prepared,
            Path(entry["output_root"]),
            f"LIVE_{name.upper()}_{session.upper()}",
            expected_batch_id=entry["batch_id"],
            expected_external_batch_id=entry["external_batch_id"],
        )
        if [item["ordinal"] for item in published] != list(range(entry["frame_count"])):
            raise GpuControlError(f"published ordinal mismatch for {name}")
        entry["artifact"] = artifact
        entry["download"] = download
        entry["published_count"] = len(published)
        entry["published_items"] = published
        entry["validation"] = {
            "artifact_metadata_equals_header_sha": artifact["sha256"] == download["header_sha256"],
            "artifact_metadata_equals_bytes_sha": artifact["sha256"] == download["sha256"],
            "exact_order": True,
            "exact_paths": True,
            "all_png_have_alpha": True,
            "atomic_publish": True,
            "cross_task_isolation": True,
        }
        print(f"VERIFIED scenario={name} batch_id={entry['batch_id']} frames={len(published)} nodes={entry.get('latest_status', {}).get('node_distribution', {})} sha256={download['sha256']}", flush=True)
        _write_report(report_path, report)

    report["finished_at"] = _now()
    report["result"] = "PASSED" if failures == 0 else "FAILED"
    _write_report(report_path, report)
    print(f"FINISHED result={report['result']} report={report_path}", flush=True)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted locally; already-created remote batches were not cancelled.", file=sys.stderr)
        raise SystemExit(130)
