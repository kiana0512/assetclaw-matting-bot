from __future__ import annotations

import argparse
import json
import mimetypes
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
STATIC_ROOT = ROOT / "dist" if (ROOT / "dist" / "index.html").exists() else ROOT
DEFAULT_AGENT_URL = "http://127.0.0.1:7865"
ALLOWED_AGENT_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _read_env_value(name: str) -> str:
    env_path = ROOT.parent / ".env"
    if not env_path.exists():
        return ""
    try:
        for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip().upper() == name.upper():
                return value.strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""


def _candidate_agent_urls() -> list[str]:
    values = [
        os.environ.get("ASSETCLAW_AGENT_URL", ""),
        os.environ.get("GATEWAY_BASE_URL", ""),
        _read_env_value("GATEWAY_BASE_URL"),
    ]
    port = os.environ.get("GATEWAY_PORT") or _read_env_value("GATEWAY_PORT")
    if port:
        values.append(f"http://127.0.0.1:{port}")
    values.extend([DEFAULT_AGENT_URL, "http://127.0.0.1:8000"])
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        raw = str(value or "").rstrip("/")
        if raw and raw not in seen:
            seen.add(raw)
            result.append(raw)
    return result


def _validate_agent_url(raw: str) -> str:
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("ASSETCLAW_AGENT_URL must be http(s)")
    if parsed.hostname not in ALLOWED_AGENT_HOSTS:
        raise ValueError("External WebUI proxy only allows localhost Agent backends")
    return raw


def _agent_url() -> str:
    return _validate_agent_url(_candidate_agent_urls()[0])


class Handler(BaseHTTPRequestHandler):
    server_version = "AssetClawWebUI/0.1"

    def do_GET(self) -> None:
        if self.path.startswith("/api/local/animation-flow-runs"):
            self._local_animation_flow_runs()
            return
        if self.path.startswith("/api/local/workspace-summary"):
            self._local_workspace_summary()
            return
        if self.path.startswith("/api/"):
            self._proxy()
            return
        self._serve_static()

    def do_POST(self) -> None:
        if self.path.startswith("/api/"):
            self._proxy()
            return
        self._json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)

    def log_message(self, fmt: str, *args: object) -> None:
        print("%s - %s" % (self.address_string(), fmt % args))

    def _serve_static(self) -> None:
        route = urlparse(self.path).path
        if route == "/":
            route = "/index.html"
        target = (STATIC_ROOT / route.lstrip("/")).resolve()
        if STATIC_ROOT not in target.parents and target != STATIC_ROOT:
            self._json({"ok": False, "error": "invalid path"}, HTTPStatus.BAD_REQUEST)
            return
        if not target.exists() or not target.is_file():
            fallback = (STATIC_ROOT / "index.html").resolve()
            if fallback.exists() and fallback.is_file() and STATIC_ROOT in fallback.parents:
                target = fallback
            else:
                self._json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)
                return
        mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _proxy(self) -> None:
        parsed = urlparse(self.path)
        target_path = self._map_api_path(parsed.path)
        if not target_path:
            self._json({"ok": False, "error": "unknown api route"}, HTTPStatus.NOT_FOUND)
            return

        qs = parse_qs(parsed.query, keep_blank_values=True)
        query = urlencode(qs, doseq=True)
        body = None
        headers = {"Accept": "application/json"}
        if self.command in {"POST", "PUT", "PATCH"}:
            length = int(self.headers.get("Content-Length") or "0")
            body = self.rfile.read(length) if length else b"{}"
            headers["Content-Type"] = self.headers.get("Content-Type", "application/json")

        configured_skill_token = (
            os.environ.get("ASSETCLAW_SKILL_TOKEN")
            or os.environ.get("SKILL_API_TOKEN")
            or _read_env_value("ASSETCLAW_SKILL_TOKEN")
            or _read_env_value("SKILL_API_TOKEN")
        )
        skill_token = configured_skill_token or self.headers.get("X-Skill-Token") or ""
        if skill_token and target_path.startswith("/skills/"):
            headers["X-Skill-Token"] = skill_token

        last_error = ""
        tried: list[str] = []
        for base_url in _candidate_agent_urls():
            base_url = _validate_agent_url(base_url)
            tried.append(base_url)
            url = f"{base_url}{target_path}"
            if query:
                url += f"?{query}"
            try:
                req = Request(url, data=body, method=self.command, headers=headers)
                with urlopen(req, timeout=30) as resp:
                    data = resp.read()
                    self.send_response(resp.status)
                    self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("X-Agent-Url", base_url)
                    self.end_headers()
                    self.wfile.write(data)
                    return
            except HTTPError as exc:
                data = exc.read()
                self.send_response(exc.code)
                self.send_header("Content-Type", exc.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(data)))
                self.send_header("X-Agent-Url", base_url)
                self.end_headers()
                self.wfile.write(data)
                return
            except (URLError, TimeoutError, ConnectionError) as exc:
                last_error = str(exc)
                continue
        self._json({"ok": False, "offline": True, "error": last_error, "tried_agent_urls": tried, "agent_url": tried[0] if tried else ""}, HTTPStatus.BAD_GATEWAY)

    def _map_api_path(self, path: str) -> str:
        if path == "/api/brain/jobs":
            return "/brain/jobs"
        if path.startswith("/api/brain/jobs/"):
            return path.replace("/api", "", 1)
        table = {
            "/api/health": "/health",
            "/api/admin/queue": "/admin/queue",
            "/api/admin/brain-messages": "/admin/brain-messages",
            "/api/admin/memory": "/admin/memory",
            "/api/admin/skill-calls": "/admin/skill-calls",
            "/api/brain/test": "/brain/test",
            "/api/skills/manifest": "/skills/v1/manifest",
            "/api/skills/call": "/skills/v1/call",
        }
        return table.get(path, "")

    def _local_animation_flow_runs(self) -> None:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        try:
            limit = max(1, min(int((qs.get("limit") or ["20"])[0]), 100))
        except ValueError:
            limit = 20
        include_finished = (qs.get("include_finished") or ["true"])[0].lower() != "false"
        runs_root = ROOT.parent / "storage" / "animation_flow_runs"
        finished = {"DONE", "FAILED", "CANCELED", "BLOCKED"}
        items: list[dict] = []
        if runs_root.exists():
            files = sorted(runs_root.glob("AFLOW_*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
            for path in files:
                try:
                    item = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if not isinstance(item, dict):
                    continue
                if "/storage/debug/current_animation_workflow.json" in str(item.get("workflow_path") or "").replace("\\", "/"):
                    continue
                safe_item = _sanitize_animation_flow_run(item)
                if not include_finished and str(safe_item.get("status") or "").upper() in finished:
                    continue
                items.append(safe_item)
                if len(items) >= limit:
                    break
        current = next((item for item in items if str(item.get("status") or "").upper() not in finished), None)
        self._json({"ok": True, "source": "local_files", "count": len(items), "current": current or (items[0] if items else None), "items": items})

    def _local_workspace_summary(self) -> None:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        root_raw = (qs.get("root") or [""])[0]
        root = Path(root_raw.replace("/", "\\")) if root_raw else Path("E:/animation_automation")
        def routed(stage: str) -> list[Path]:
            return [root / "scene" / stage, root / "emoji" / stage]

        specs = [
            ("videos", "视频", routed("videos"), {".mp4", ".mov", ".avi", ".mkv", ".webm"}),
            ("frames", "帧", routed("frames"), {".png", ".jpg", ".jpeg", ".webp"}),
            ("matte", "抠图", routed("matte"), {".png", ".jpg", ".jpeg", ".webp"}),
            ("smooth", "后处理", routed("smooth"), {".png", ".jpg", ".jpeg", ".webp"}),
            ("unity_ready", "Unity Ready", [root / "unity_ready"], {".png", ".json", ".bytes", ".asset"}),
        ]
        items = []
        for key, label, paths, exts in specs:
            counts = [_count_tree(path, exts) for path in paths]
            items.append({
                "key": key,
                "label": label,
                "path": " ; ".join(str(path) for path in paths),
                "exists": any(path.exists() for path in paths),
                "count": sum(item[0] for item in counts),
                "folders": sum(item[1] for item in counts),
            })
        self._json({"ok": True, "source": "local_files", "root": str(root), "items": items})

    def _json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _sanitize_animation_flow_run(item: dict) -> dict:
    children = item.get("children") if isinstance(item.get("children"), dict) else {}
    p4 = item.get("p4") if isinstance(item.get("p4"), dict) else {}
    child_p4 = children.get("p4") if isinstance(children.get("p4"), dict) else {}
    unity_import = children.get("unity_import") if isinstance(children.get("unity_import"), dict) else {}
    unity_summary = _summarize_unity_import(unity_import)
    p4_summary = _summarize_flow_p4(child_p4)
    status = str(item.get("status") or "").upper() or "UNKNOWN"
    stages = item.get("stages") if isinstance(item.get("stages"), list) else []
    if p4_summary and p4_summary.get("shelved") and status in {"RUNNING", "DONE", "UNKNOWN"}:
        status = "DONE"
        stages = [_mark_stage_done(stage) for stage in stages]
    return {
        "run_id": item.get("id"),
        "id": item.get("id"),
        "status": status,
        "current_stage": "p4_shelve" if status == "DONE" and p4_summary else item.get("current_stage"),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "date_root": item.get("date_root"),
        "unity_ready": item.get("unity_ready"),
        "unity_project": item.get("unity_project"),
        "package": item.get("package"),
        "unity_import_mode": item.get("unity_import_mode"),
        "workflow_path": item.get("workflow_path"),
        "workflow_name": item.get("workflow_name"),
        "fps": item.get("fps"),
        "allow_p4_writes": item.get("allow_p4_writes"),
        "fake_matting_from_frames": item.get("fake_matting_from_frames"),
        "p4": {
            "stream": p4.get("stream"),
            "submit": p4.get("submit"),
            "unity_import_mode": p4.get("unity_import_mode"),
        },
        "stages": stages,
        "children": {
            "pipeline_run_id": children.get("pipeline_run_id"),
            "unity_import": unity_summary if unity_summary else None,
            "p4": p4_summary,
        },
        "error": item.get("error") or "",
    }


def _mark_stage_done(stage: object) -> object:
    if not isinstance(stage, dict):
        return stage
    updated = dict(stage)
    updated["status"] = "done"
    return updated


def _summarize_flow_p4(payload: dict) -> dict | None:
    if not payload:
        return None
    create_cl = payload.get("create_cl") if isinstance(payload.get("create_cl"), dict) else {}
    reconcile = payload.get("reconcile") if isinstance(payload.get("reconcile"), dict) else {}
    shelve = payload.get("shelve") if isinstance(payload.get("shelve"), dict) else {}
    report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
    changelist_id = (
        payload.get("changelist_id")
        or shelve.get("changelist_id")
        or reconcile.get("changelist_id")
        or create_cl.get("changelist_id")
    )
    target_paths = payload.get("target_paths") or reconcile.get("paths")
    if not changelist_id and not target_paths:
        return None
    return {
        "changelist_id": changelist_id,
        "target_paths": target_paths,
        "shelved": bool(shelve.get("ok")),
        "reported": bool(report.get("ok")),
        "reconciled": bool(reconcile.get("ok")),
    }


def _summarize_unity_import(payload: dict) -> dict | None:
    if not payload:
        return None
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    result_path = str(payload.get("result_path") or "")
    if not result and result_path:
        try:
            path = Path(result_path)
            if path.is_file():
                result = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            result = {}
    packages = result.get("packages") if isinstance(result.get("packages"), list) else []
    totals = {
        "tasks": 0,
        "textures": 0,
        "replaced": 0,
        "skipped": 0,
    }
    compact_packages = []
    for package in packages:
        if not isinstance(package, dict):
            continue
        compact = {
            "package": package.get("package") or package.get("name") or "",
            "mode": package.get("mode") or result.get("mode") or payload.get("mode") or "",
            "tasksProcessed": int(package.get("tasksProcessed") or package.get("task_count") or 0),
            "textures": int(package.get("textures") or package.get("importedTextures") or 0),
            "replacedTextures": int(package.get("replacedTextures") or 0),
            "skippedTextures": int(package.get("skippedTextures") or 0),
            "inferredFromDisk": bool(package.get("inferredFromDisk")),
        }
        totals["tasks"] += compact["tasksProcessed"]
        totals["textures"] += compact["textures"]
        totals["replaced"] += compact["replacedTextures"]
        totals["skipped"] += compact["skippedTextures"]
        compact_packages.append(compact)
    disk = payload.get("disk_progress") if isinstance(payload.get("disk_progress"), dict) else {}
    latest = payload.get("latest_status") if isinstance(payload.get("latest_status"), dict) else {}
    recovered = str(payload.get("error") or "") == "unity_runner_timeout" and bool(result.get("ok"))
    disk_confirmed = bool(result.get("inferredFromDisk") or payload.get("message") == "Unity result file was late/missing; disk polling confirmed the import outputs.")
    display_status = "CONFIRMED" if disk_confirmed else ("LATE_RESULT" if recovered else ("OK" if payload.get("ok") else str(payload.get("error") or "PENDING")))
    return {
        "ok": payload.get("ok"),
        "mode": payload.get("mode") or result.get("mode"),
        "error": payload.get("error") or "",
        "message": payload.get("message") or "",
        "request": payload.get("request") or "",
        "result_path": result_path,
        "status_path": payload.get("status_path") or "",
        "recovered": recovered,
        "disk_confirmed": disk_confirmed,
        "display_status": display_status,
        "result": {
            "ok": result.get("ok"),
            "mode": result.get("mode") or payload.get("mode"),
            "inferredFromDisk": bool(result.get("inferredFromDisk")),
            "packages": compact_packages,
            "totals": totals,
        },
        "disk_progress": {
            "supported": bool(disk.get("supported")),
            "complete": bool(disk.get("complete")),
            "sourceTextures": int(disk.get("sourceTextures") or 0),
            "replaceableTextures": int(disk.get("replaceableTextures") or 0),
            "replacedTextures": int(disk.get("replacedTextures") or 0),
            "skippedTextures": int(disk.get("skippedTextures") or 0),
        },
        "latest_status": {
            "phase": latest.get("phase") or "",
            "package": latest.get("package") or "",
            "character": latest.get("character") or "",
            "updatedAt": latest.get("updatedAt") or "",
        },
    }


def _count_tree(root: Path, exts: set[str]) -> tuple[int, int]:
    if not root.exists() or not root.is_dir():
        return 0, 0
    count = 0
    folders: set[Path] = set()
    try:
        for item in root.rglob("*"):
            if not item.is_file() or item.suffix.lower() not in exts:
                continue
            count += 1
            folders.add(item.parent)
    except Exception:
        return count, len(folders)
    return count, len(folders)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local-only AssetClaw External WebUI.")
    parser.add_argument("--host", default=os.environ.get("ASSETCLAW_WEBUI_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("ASSETCLAW_WEBUI_PORT", "5177")))
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("External WebUI must bind to localhost only")
    _agent_url()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"AssetClaw External WebUI: http://{args.host}:{args.port}")
    print("Agent backend candidates:")
    for item in _candidate_agent_urls():
        print(f"  - {item}")
    server.serve_forever()


if __name__ == "__main__":
    main()
