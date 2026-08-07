#!/usr/bin/env python3
"""Read-only pre-rollout checks for local and GPU Control task activity."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

import requests


TERMINAL_STATUSES = {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED", "CANCELLED", "ARCHIVED"}


def _status_summary(values: list[str]) -> dict[str, Any]:
    counts = Counter(value.upper() for value in values if value)
    active = {key: value for key, value in counts.items() if key not in TERMINAL_STATUSES}
    return {"counts": dict(sorted(counts.items())), "active": active, "active_total": sum(active.values())}


def _sqlite_statuses(database: Path) -> dict[str, Any]:
    if not database.is_file():
        return {"error": f"missing database: {database}"}
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True, timeout=10)
    try:
        rows = connection.execute("SELECT status FROM comfyui_runs").fetchall()
    finally:
        connection.close()
    return _status_summary([str(row[0] or "") for row in rows])


def _json_statuses(root: Path, pattern: str) -> dict[str, Any]:
    if not root.is_dir():
        return {"counts": {}, "active": {}, "active_total": 0, "missing": True}
    statuses: list[str] = []
    unreadable: list[str] = []
    for path in root.glob(pattern):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            statuses.append(str(payload.get("status") or ""))
        except (OSError, ValueError, TypeError):
            unreadable.append(str(path))
    result = _status_summary(statuses)
    result["unreadable"] = unreadable
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--skip-remote", action="store_true")
    args = parser.parse_args()
    root = args.project_root.resolve()
    report: dict[str, Any] = {
        "project_root": str(root),
        "tasks": {
            "comfyui_runs": _sqlite_statuses(root / "data" / "assetclaw.db"),
            "animation_flows": _json_statuses(root / "storage" / "animation_flow_runs", "AFLOW_*.json"),
            "direct_video": _json_statuses(root / "storage" / "direct_video_runs", "*/status.json"),
            "direct_image": _json_statuses(root / "storage" / "direct_image_runs", "*/status.json"),
        },
    }
    report["local_active_total"] = sum(
        int(item.get("active_total") or 0) for item in report["tasks"].values()
    )
    try:
        response = requests.get("http://127.0.0.1:7865/health", timeout=3)
        report["gateway"] = {"http_status": response.status_code, "payload": response.json()}
    except Exception as exc:
        report["gateway"] = {"error": str(exc)}

    if not args.skip_remote:
        try:
            from assetclaw_matting.services.gpu_control_batch import GpuControlBatchClient

            client = GpuControlBatchClient()
            report["gpu_control"] = {
                "ready": client.health_ready(request_id="assetclaw-pre-rollout-ready"),
                "capacity": client.scheduler_capacity(request_id="assetclaw-pre-rollout-capacity"),
            }
        except Exception as exc:
            report["gpu_control"] = {"error": str(exc)}

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
