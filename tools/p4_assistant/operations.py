from __future__ import annotations

import re
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from tools.p4_assistant.models import P4FileAction, P4FileChange, P4WorkspaceConfig, ReportData, SafetyResult
from tools.p4_assistant.p4_runner import P4Runner
from tools.p4_assistant.safety import (
    ensure_shelve_only_mode,
    validate_managed_paths,
    validate_opened_files,
    validate_reconcile_preview,
    workspace_warnings,
)
from tools.p4_assistant.workspace_registry import WorkspaceRegistry


class P4Operations:
    def __init__(self, registry: WorkspaceRegistry | None = None, runner: P4Runner | None = None) -> None:
        self.registry = registry or WorkspaceRegistry()
        self.runner = runner or P4Runner()

    def get_status(self, workflow: str | None = None, workspace: str | None = None) -> dict[str, Any]:
        ws = self.registry.resolve(workflow, workspace)
        runner = self.runner.for_workspace(ws)
        info = runner.info()
        login = runner.login_status()
        client = runner.client_spec()
        opened = runner.opened()
        pending = runner.pending_changelists()
        shelved = runner.shelved_changelists()
        info_data = _parse_info(info.stdout)
        client_spec = _parse_client_spec(client.stdout)
        root = info_data.get("client root") or client_spec.get("root") or str(ws.root)
        stream = client_spec.get("stream") or ws.stream or _guess_branch(client_spec)
        logged_in = login.ok and "not necessary" not in (login.stdout + login.stderr).lower()
        matches = _same_path(root, ws.root) and (not info_data.get("client name") or info_data["client name"].lower() == ws.p4client.lower())
        opened_files = parse_p4_file_changes(opened.stdout)
        return _payload(ws, "status", True, {
            "p4port": ws.p4port,
            "p4user": ws.p4user,
            "p4client": ws.p4client,
            "root": root,
            "current_directory": str(ws.root),
            "stream": stream,
            "logged_in": logged_in,
            "opened_files_count": len(opened_files),
            "pending_changelists": _parse_change_ids(pending.stdout),
            "shelved_changelists": _parse_change_ids(shelved.stdout),
            "workspace_matches_config": matches,
            "mode": ws.mode,
            "submit": "disabled",
            "warnings": workspace_warnings(ws),
            "commands": [info.as_dict(), login.as_dict(), client.as_dict(), opened.as_dict(), pending.as_dict(), shelved.as_dict()],
        })

    def list_changelists(self, workflow: str | None = None, workspace: str | None = None) -> dict[str, Any]:
        ws = self.registry.resolve(workflow, workspace)
        runner = self.runner.for_workspace(ws)
        pending = runner.pending_changelists()
        shelved = runner.shelved_changelists()
        pending_items = _parse_change_items(pending.stdout, "pending")
        shelved_items = _parse_change_items(shelved.stdout, "shelved")
        by_id: dict[str, dict[str, Any]] = {}
        for item in pending_items + shelved_items:
            entry = by_id.setdefault(
                item["id"],
                {
                    "id": item["id"],
                    "date": item.get("date") or "",
                    "user_client": item.get("user_client") or "",
                    "description": item.get("description") or "",
                    "pending": False,
                    "shelved": False,
                },
            )
            entry[item["status"]] = True
            if item.get("description") and not entry.get("description"):
                entry["description"] = item["description"]
        items = sorted(by_id.values(), key=lambda item: int(item["id"]), reverse=True)
        return _payload(ws, "list-cls", True, {
            "items": items,
            "pending_count": len(pending_items),
            "shelved_count": len(shelved_items),
            "submit": "disabled",
            "commands": [pending.as_dict(), shelved.as_dict()],
        })

    def workspace_info(self, workflow: str | None = None, workspace: str | None = None) -> dict[str, Any]:
        ws = self.registry.resolve(workflow, workspace)
        runner = self.runner.for_workspace(ws)
        info = runner.info()
        client = runner.client_spec()
        where = runner.workspace_where(list(ws.managed_paths))
        client_spec = _parse_client_spec(client.stdout)
        return _payload(ws, "workspace-info", True, {
            "stream": client_spec.get("stream") or ws.stream or _guess_branch(client_spec),
            "client_spec": client_spec,
            "managed_paths": list(ws.managed_paths),
            "forbidden_paths": list(ws.forbidden_paths),
            "submit": "disabled",
            "commands": [info.as_dict(), client.as_dict(), where.as_dict()],
        })

    def streams(self, workflow: str | None = None, workspace: str | None = None) -> dict[str, Any]:
        ws = self.registry.resolve(workflow, workspace)
        result = self.runner.for_workspace(ws).streams()
        return _payload(ws, "streams", True, {
            "streams": _parse_streams(result.stdout),
            "submit": "disabled",
            "commands": [result.as_dict()],
        })

    def run_check(self, workflow: str | None = None, workspace: str | None = None, allow_delete: bool = False) -> dict[str, Any]:
        ws = self.registry.resolve(workflow, workspace)
        runner = self.runner.for_workspace(ws)
        errors: list[str] = []
        warnings: list[str] = workspace_warnings(ws)
        mode = ensure_shelve_only_mode(ws)
        paths = validate_managed_paths(ws)
        errors.extend(mode.errors + paths.errors)
        warnings.extend(mode.warnings + paths.warnings)
        p4_available = runner.is_available()
        if not p4_available:
            errors.append("p4 executable was not found")
        login = runner.login_status() if p4_available else None
        if login and not login.ok:
            warnings.append("P4 login is missing or expired. Run p4 login manually.")
        opened = runner.opened() if p4_available else None
        opened_files = parse_p4_file_changes(opened.stdout if opened else "")
        safety = validate_opened_files(opened_files, ws, allow_delete=allow_delete)
        errors.extend(safety.errors)
        warnings.extend(safety.warnings)
        ok = not errors
        return _payload(ws, "check", ok, {
            "mode": ws.mode,
            "submit": "disabled",
            "p4_available": p4_available,
            "login_ok": bool(login and login.ok),
            "managed_paths": list(ws.managed_paths),
            "forbidden_paths": list(ws.forbidden_paths),
            "opened_files": [file_change_to_dict(item) for item in opened_files],
            "safety": _safety_dict(SafetyResult(ok, tuple(errors), tuple(warnings), {**mode.checks, **paths.checks, **safety.checks})),
            "commands": [item.as_dict() for item in (login, opened) if item],
        })

    def preview_changes(self, workflow: str | None = None, workspace: str | None = None, allow_delete: bool = False, paths: list[str] | None = None) -> dict[str, Any]:
        ws = self.registry.resolve(workflow, workspace)
        target_paths = _safe_target_paths(paths, ws)
        result = self.runner.for_workspace(ws).reconcile_preview(target_paths)
        files = parse_p4_file_changes(result.stdout)
        safety = validate_reconcile_preview(files, ws, allow_delete=allow_delete)
        return _payload(ws, "preview", safety.ok, {
            "paths": target_paths,
            "files": [file_change_to_dict(item) for item in files],
            "stats": action_stats(files),
            "safety": _safety_dict(safety),
            "delete_warning": [item.path for item in files if item.action == P4FileAction.DELETE],
            "commands": [result.as_dict()],
        })

    def create_changelist(self, workflow: str | None = None, workspace: str | None = None, desc: str = "", yes: bool = False) -> dict[str, Any]:
        ws = self.registry.resolve(workflow, workspace)
        ensure_shelve_only_mode(ws).require_ok()
        description = _shelve_only_description(ws, desc)
        if not yes:
            return _needs_confirmation(ws, "create-cl", {"description": description})
        cl = self.runner.for_workspace(ws).create_changelist(description)
        return _payload(ws, "create-cl", True, {"changelist_id": cl, "description": description})

    def reconcile_changelist(self, workflow: str | None = None, workspace: str | None = None, cl: str | int = "", allow_delete: bool = False, yes: bool = False, paths: list[str] | None = None) -> dict[str, Any]:
        ws = self.registry.resolve(workflow, workspace)
        target_paths = _safe_target_paths(paths, ws)
        if not yes:
            return _needs_confirmation(ws, "reconcile", {"changelist_id": str(cl), "paths": target_paths})
        runner = self.runner.for_workspace(ws)
        result = runner.reconcile_to_changelist(cl, target_paths)
        default_opened = parse_p4_file_changes(runner.opened().stdout)
        reopen = None
        if default_opened:
            default_safety = validate_opened_files(default_opened, ws, allow_delete=allow_delete)
            if default_safety.ok:
                reopen = runner.reopen_to_changelist(cl, target_paths)
        opened = runner.opened(cl)
        files = parse_p4_file_changes(opened.stdout)
        safety = validate_opened_files(files, ws, allow_delete=allow_delete)
        return _payload(ws, "reconcile", safety.ok, {
            "changelist_id": str(cl),
            "paths": target_paths,
            "files": [file_change_to_dict(item) for item in files],
            "stats": action_stats(files),
            "safety": _safety_dict(safety),
            "commands": [item.as_dict() for item in (result, reopen, opened) if item],
        })

    def shelve_changelist(self, workflow: str | None = None, workspace: str | None = None, cl: str | int = "", force: bool = False, allow_delete: bool = False, yes: bool = False) -> dict[str, Any]:
        ws = self.registry.resolve(workflow, workspace)
        runner = self.runner.for_workspace(ws)
        opened = runner.opened(cl)
        files = parse_p4_file_changes(opened.stdout)
        safety = validate_opened_files(files, ws, allow_delete=allow_delete)
        if not safety.ok:
            return _payload(ws, "shelve", False, {"changelist_id": str(cl), "safety": _safety_dict(safety), "files": [file_change_to_dict(item) for item in files]})
        if not yes:
            return _needs_confirmation(ws, "shelve", {"changelist_id": str(cl), "force": force, "safety": _safety_dict(safety), "files": [file_change_to_dict(item) for item in files]})
        shelve = runner.shelve(cl, force=force)
        describe = runner.describe(cl, shelved=True)
        shelved_files = parse_p4_file_changes(describe.stdout) or files
        return _payload(ws, "shelve", True, {
            "changelist_id": str(cl),
            "shelved_changelist_id": str(cl),
            "force": force,
            "files": [file_change_to_dict(item) for item in shelved_files],
            "stats": action_stats(shelved_files),
            "safety": _safety_dict(safety),
            "commands": [opened.as_dict(), shelve.as_dict(), describe.as_dict()],
        })

    def cleanup_changelist(
        self,
        workflow: str | None = None,
        workspace: str | None = None,
        cl: str | int = "",
        allow_delete: bool = False,
    ) -> dict[str, Any]:
        if not cl:
            return {"ok": False, "error": "cl is required"}
        ws = self.registry.resolve(workflow, workspace)
        ensure_shelve_only_mode(ws).require_ok()
        runner = self.runner.for_workspace(ws)
        opened = runner.opened(cl)
        shelved = runner.describe(cl, shelved=True)
        opened_files = parse_p4_file_changes(opened.stdout)
        shelved_files = parse_p4_file_changes(shelved.stdout)
        files_by_path = {item.path: item for item in [*opened_files, *shelved_files] if item.path}
        files = list(files_by_path.values())
        safety = validate_opened_files(files, ws, allow_delete=allow_delete)
        if not safety.ok:
            return _payload(ws, "cleanup-cl", False, {
                "changelist_id": str(cl),
                "files": [file_change_to_dict(item) for item in files],
                "stats": action_stats(files),
                "safety": _safety_dict(safety),
                "commands": [opened.as_dict(), shelved.as_dict()],
            })
        commands = [opened.as_dict(), shelved.as_dict()]
        deleted_shelf = False
        reverted = False
        if shelved_files:
            delete_shelf = runner.delete_shelve(cl)
            commands.append(delete_shelf.as_dict())
            deleted_shelf = delete_shelf.ok
            if not delete_shelf.ok:
                return _payload(ws, "cleanup-cl", False, {
                    "changelist_id": str(cl),
                    "error": delete_shelf.safe_summary,
                    "deleted_shelf": deleted_shelf,
                    "commands": commands,
                })
        if opened_files:
            revert = runner.revert_changelist(cl, list(ws.managed_paths))
            commands.append(revert.as_dict())
            reverted = revert.ok
            if not revert.ok:
                return _payload(ws, "cleanup-cl", False, {
                    "changelist_id": str(cl),
                    "error": revert.safe_summary,
                    "deleted_shelf": deleted_shelf,
                    "reverted": reverted,
                    "commands": commands,
                })
        deleted_cl = runner.delete_changelist(cl)
        commands.append(deleted_cl.as_dict())
        return _payload(ws, "cleanup-cl", deleted_cl.ok, {
            "changelist_id": str(cl),
            "deleted_shelf": deleted_shelf,
            "reverted": reverted,
            "deleted_changelist": deleted_cl.ok,
            "files": [file_change_to_dict(item) for item in files],
            "stats": action_stats(files),
            "safety": _safety_dict(safety),
            "submit": "disabled",
            "commands": commands,
            "error": "" if deleted_cl.ok else deleted_cl.safe_summary,
        })

    def switch_stream(self, workflow: str | None = None, workspace: str | None = None, stream: str = "", preview: bool = True, yes: bool = False, allow_opened: bool = False) -> dict[str, Any]:
        ws = self.registry.resolve(workflow, workspace)
        runner = self.runner.for_workspace(ws)
        opened = runner.opened()
        opened_files = parse_p4_file_changes(opened.stdout)
        if opened_files and not allow_opened:
            return _payload(ws, "switch-stream", False, {
                "needs_confirmation": False,
                "blocked": True,
                "error": "opened files exist; switch-stream is blocked unless --allow-opened is explicitly passed.",
                "opened_files": [file_change_to_dict(item) for item in opened_files],
                "planned_command": ["p4", "client", "-s", "-S", stream, ws.p4client],
                "commands": [opened.as_dict()],
            })
        plan = {
            "stream": stream,
            "planned_command": ["p4", "client", "-s", "-S", stream, ws.p4client],
            "opened_files_count": len(opened_files),
            "allow_opened": allow_opened,
            "commands": [opened.as_dict()],
        }
        if preview or not yes:
            return _payload(ws, "switch-stream", True, {"preview": True, "needs_confirmation": not yes, **plan})
        result = runner.switch_stream(stream)
        return _payload(ws, "switch-stream", result.ok, {**plan, "preview": False, "commands": [opened.as_dict(), result.as_dict()]})

    def get_latest(self, workflow: str | None = None, workspace: str | None = None, scope: str = "managed", preview: bool = False, yes: bool = False) -> dict[str, Any]:
        ws = self.registry.resolve(workflow, workspace)
        if scope not in {"managed", "all"}:
            raise ValueError("scope must be managed or all")
        paths = list(ws.managed_paths) if scope == "managed" else ["//..."]
        runner = self.runner.for_workspace(ws)
        if preview:
            result = runner.sync_preview(paths)
            return _payload(ws, "get-latest", result.ok, {"preview": True, "scope": scope, "paths": paths, "commands": [result.as_dict()]})
        if scope == "all" and not yes:
            return _needs_confirmation(ws, "get-latest", {"scope": scope, "paths": paths, "message": "--scope all may be slow and must use --yes."})
        result = runner.sync(paths)
        return _payload(ws, "get-latest", result.ok, {"preview": False, "scope": scope, "paths": paths, "commands": [result.as_dict()]})

    def generate_report(
        self,
        workflow: str | None = None,
        workspace: str | None = None,
        cl: str | int = "",
        allow_delete: bool = False,
        unity_ready_manifest: str | None = None,
    ) -> dict[str, Any]:
        ws = self.registry.resolve(workflow, workspace)
        runner = self.runner.for_workspace(ws)
        opened = runner.opened(cl)
        shelved = runner.describe(cl, shelved=True)
        files = parse_p4_file_changes(shelved.stdout) or parse_p4_file_changes(opened.stdout)
        safety = validate_opened_files(files, ws, allow_delete=allow_delete)
        report = render_report(ReportData(ws, str(cl), str(cl), tuple(files), safety, ws.stream or "main", unity_ready_manifest=str(unity_ready_manifest or "")))
        return _payload(ws, "report", safety.ok, {
            "changelist_id": str(cl),
            "shelved_changelist_id": str(cl),
            "files": [file_change_to_dict(item) for item in files],
            "stats": action_stats(files),
            "safety": _safety_dict(safety),
            "report_text": report,
            "unity_ready_manifest": unity_ready_manifest or "",
            "commands": [opened.as_dict(), shelved.as_dict()],
        })

    def shelve_ui_import(self, workflow: str | None = None, workspace: str | None = None, desc: str = "", yes: bool = False, force: bool = False, allow_delete: bool = False) -> dict[str, Any]:
        check = self.run_check(workflow, workspace, allow_delete=allow_delete)
        preview = self.preview_changes(workflow, workspace, allow_delete=allow_delete)
        ws = self.registry.resolve(workflow, workspace)
        return _payload(ws, "shelve-ui-import", False, {
            "blocked": True,
            "needs_separate_confirmation": True,
            "message": "Batch P4 writes are disabled. Run create-cl, reconcile, and shelve as three separate confirmed steps after reviewing check/preview.",
            "desc": desc,
            "force": force,
            "allow_delete": allow_delete,
            "check": check,
            "preview": preview,
            "next_steps": [
                "create-cl --desc ... --yes",
                "reconcile --cl <created_cl> --yes",
                "shelve --cl <created_cl> --yes",
                "report --cl <created_cl> --unity-ready-manifest <unity_ready/manifest.json>",
            ],
        })


def parse_p4_file_changes(text: str) -> list[P4FileChange]:
    files: list[P4FileChange] = []
    for line in (text or "").splitlines():
        path = _first_path(line)
        if not path:
            continue
        action = _action_from_line(line)
        if action == P4FileAction.UNKNOWN:
            continue
        cl = _changelist_from_line(line)
        file_type = _file_type_from_line(line)
        files.append(P4FileChange(depot_path=path if path.startswith("//") else "", local_path="" if path.startswith("//") else path, action=action, changelist_id=cl, file_type=file_type))
    return files


def _safe_target_paths(paths: list[str] | None, ws: P4WorkspaceConfig) -> list[str]:
    target_paths = [str(item).replace("\\", "/") for item in (paths or ws.managed_paths) if str(item).strip()]
    if not target_paths:
        raise ValueError("paths is empty")
    return target_paths


def render_report(data: ReportData) -> str:
    stats = action_stats(list(data.files))
    delete_files = [item.path for item in data.files if item.action == P4FileAction.DELETE]
    lines = [
        "【P4 UI 动画资源导入 - Shelve Only】",
        "",
        "P4 Server:",
        data.workspace.p4port,
        "",
        "User:",
        data.workspace.p4user,
        "",
        "Workspace:",
        data.workspace.p4client,
        "",
        "Root:",
        str(data.workspace.root),
        "",
        "Stream/Branch:",
        data.stream_or_branch or "main",
        "",
        "Changelist:",
        data.changelist_id,
        "",
        "Shelved Changelist:",
        data.shelved_changelist_id or data.changelist_id,
        "",
        "Submit:",
        "DISABLED，未执行 submit。",
        "",
        "影响目录:",
        *[f"- {path}" for path in data.workspace.managed_paths],
        "",
        "文件统计:",
        f"- add: {stats['add']}",
        f"- edit: {stats['edit']}",
        f"- delete: {stats['delete']}",
        f"- move: {stats['move']}",
        "",
        "安全检查:",
        f"- 只包含白名单目录: {data.safety.checks.get('managed paths only', 'PASS')}",
        "- submit disabled: PASS",
        f"- 白名单外 opened files: {'NONE' if data.safety.ok else 'BLOCKED'}",
        f"- delete 文件: {'WARNING' if delete_files else 'NONE'}",
        f"- Unity .meta 检查: {data.safety.checks.get('Unity .meta', 'PASS')}",
        "",
        "文件列表:",
    ]
    lines.extend(f"{item.action.value}  {item.path}" for item in data.files)
    if not data.files:
        lines.append("- 无文件")
    if delete_files:
        lines.extend(["", "Delete 文件:", *[f"- {path}" for path in delete_files]])
    if data.safety.warnings:
        lines.extend(["", "Warnings:", *[f"- {warning}" for warning in data.safety.warnings]])
    if data.unity_ready_manifest:
        lines.extend(_unity_ready_report_lines(data.unity_ready_manifest))
    lines.extend(["", "说明:", "请组内成员根据 shelved changelist 进行 review / unshelve / 修改。该工具不会执行 submit。"])
    return "\n".join(lines)


def _unity_ready_report_lines(manifest_path: str) -> list[str]:
    path = Path(manifest_path)
    lines = ["", "Unity Ready:"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        ready_root = path.parent
        lines.extend(
            [
                str(ready_root),
                "",
                "Scene JSON:",
                str(ready_root / payload.get("packages", {}).get("scene", {}).get("json", "scene/animation_resource_manifest.json")),
                "",
                "Emoji JSON:",
                str(ready_root / payload.get("packages", {}).get("emoji", {}).get("json", "emoji/animation_resource_manifest.json")),
                "",
                "Story JSON:",
                str(ready_root / payload.get("packages", {}).get("story", {}).get("json", "story/animation_resource_manifest.json")),
                "",
                "Source Manifest:",
                str((ready_root / str(payload.get("sourceManifest", "../source_manifest.json"))).resolve()),
            ]
        )
    except Exception as exc:
        lines.append(f"{manifest_path} (读取失败: {exc})")
    return lines


def action_stats(files: list[P4FileChange]) -> dict[str, int]:
    counts = Counter(item.action.value for item in files)
    return {key: int(counts.get(key, 0)) for key in ("add", "edit", "delete", "move", "unknown")}


def file_change_to_dict(item: P4FileChange) -> dict[str, Any]:
    return {
        "depot_path": item.depot_path,
        "local_path": item.local_path,
        "path": item.path,
        "action": item.action.value,
        "changelist_id": item.changelist_id,
        "file_type": item.file_type,
    }


def _payload(ws: P4WorkspaceConfig, operation: str, ok: bool, extra: dict[str, Any]) -> dict[str, Any]:
    payload = {"ok": ok, "operation": operation, "workspace": ws.name, "root": str(ws.root), "p4port": ws.p4port, "p4user": ws.p4user, "p4client": ws.p4client}
    payload.update(extra)
    return payload


def _needs_confirmation(ws: P4WorkspaceConfig, operation: str, extra: dict[str, Any]) -> dict[str, Any]:
    return _payload(ws, operation, False, {
        "needs_confirmation": True,
        "message": f"{operation} writes P4/server or workspace state. Re-run with --yes after checking preview.",
        "submit": "disabled",
        **extra,
    })


def _safety_dict(safety: SafetyResult) -> dict[str, Any]:
    return {"ok": safety.ok, "errors": list(safety.errors), "warnings": list(safety.warnings), "checks": safety.checks}


def _shelve_only_description(ws: P4WorkspaceConfig, desc: str) -> str:
    managed = "\n".join(f"- {path}" for path in ws.managed_paths)
    return f"{desc.strip()}\n\n[Shelve-only]\nworkspace: {ws.name}\nuser: {ws.p4user}\nroot: {ws.root}\nmanaged paths:\n{managed}\ncreated_at: {datetime.now().isoformat(timespec='seconds')}\nsubmit: disabled"


def _parse_info(stdout: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for line in stdout.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            data[key.strip().lower()] = value.strip()
    return data


def _parse_client_spec(stdout: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for line in stdout.splitlines():
        if line.startswith("\t") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip().lower()] = value.strip()
    return data


def _parse_change_ids(stdout: str) -> list[str]:
    ids: list[str] = []
    for line in stdout.splitlines():
        match = re.search(r"\bChange\s+(\d+)\b", line, re.IGNORECASE)
        if match:
            ids.append(match.group(1))
    return ids


def _parse_change_items(stdout: str, status: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    pattern = re.compile(
        r"^Change\s+(?P<id>\d+)\s+on\s+(?P<date>\S+)\s+by\s+(?P<user_client>\S+)\s+\*(?P<status>\w+)\*\s*(?P<description>.*)$",
        re.IGNORECASE,
    )
    for line in stdout.splitlines():
        match = pattern.search(line.strip())
        if not match:
            id_match = re.search(r"\bChange\s+(\d+)\b", line, re.IGNORECASE)
            if id_match:
                items.append({"id": id_match.group(1), "status": status, "date": "", "user_client": "", "description": line.strip()})
            continue
        item = match.groupdict()
        item["status"] = status
        item["description"] = item.get("description", "").strip().strip("'")
        items.append(item)
    return items


def _parse_streams(stdout: str) -> list[str]:
    streams: list[str] = []
    for line in stdout.splitlines():
        match = re.search(r"(//\S+)", line)
        if match:
            streams.append(match.group(1))
    return streams


def _guess_branch(spec: dict[str, str]) -> str:
    view = spec.get("view", "")
    return "main" if "main" in view.lower() else ""


def _same_path(left: str, right: Path) -> bool:
    return str(Path(left)).rstrip("\\/").lower() == str(right).rstrip("\\/").lower()


def _first_path(line: str) -> str:
    match = re.search(r"(//[^\s#]+|[A-Za-z]:[\\/][^\s#]+|Assets/[^\s#]+|ProjectSettings/[^\s#]+|Packages/[^\s#]+|Library/[^\s#]+|Temp/[^\s#]+)", line)
    return match.group(1).rstrip(",") if match else ""


def _action_from_line(line: str) -> P4FileAction:
    lowered = line.lower()
    if "move/" in lowered or " - moved" in lowered or " moved " in lowered:
        return P4FileAction.MOVE
    if "delete" in lowered:
        return P4FileAction.DELETE
    if "add" in lowered:
        return P4FileAction.ADD
    if "edit" in lowered:
        return P4FileAction.EDIT
    return P4FileAction.UNKNOWN


def _changelist_from_line(line: str) -> str:
    match = re.search(r"(?:change|changelist)\s+(\d+)", line, re.IGNORECASE)
    return match.group(1) if match else ""


def _file_type_from_line(line: str) -> str:
    match = re.search(r"#\d+\s+-\s+\w+\s+\w+\s+(.*)$", line)
    return match.group(1).strip() if match else ""
