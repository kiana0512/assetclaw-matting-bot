from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tools.p4_assistant.nl_intent import parse_intent
from tools.p4_assistant.operations import P4Operations
from tools.p4_assistant.workspace_registry import WorkspaceRegistry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P4 Shelve-only assistant for Unity UI emoji assets")
    sub = parser.add_subparsers(dest="command", required=True)

    _add_common(sub.add_parser("status"))
    _add_common(sub.add_parser("workspace-info"))
    _add_common(sub.add_parser("streams"))
    check = _add_common(sub.add_parser("check"))
    check.add_argument("--allow-delete", action="store_true")
    preview = _add_common(sub.add_parser("preview"))
    preview.add_argument("--allow-delete", action="store_true")
    create = _add_common(sub.add_parser("create-cl"))
    create.add_argument("--desc", required=True)
    create.add_argument("--yes", action="store_true")
    reconcile = _add_common(sub.add_parser("reconcile"))
    reconcile.add_argument("--cl", required=True)
    reconcile.add_argument("--allow-delete", action="store_true")
    reconcile.add_argument("--yes", action="store_true")
    shelve = _add_common(sub.add_parser("shelve"))
    shelve.add_argument("--cl", required=True)
    shelve.add_argument("--force", action="store_true")
    shelve.add_argument("--allow-delete", action="store_true")
    shelve.add_argument("--yes", action="store_true")
    switch = _add_common(sub.add_parser("switch-stream"))
    switch.add_argument("--stream", required=True)
    switch.add_argument("--preview", action="store_true")
    switch.add_argument("--yes", action="store_true")
    switch.add_argument("--allow-opened", action="store_true")
    latest = _add_common(sub.add_parser("get-latest"))
    latest.add_argument("--scope", choices=("managed", "all"), default="managed")
    latest.add_argument("--preview", action="store_true")
    latest.add_argument("--yes", action="store_true")
    report = _add_common(sub.add_parser("report"))
    report.add_argument("--cl", required=True)
    report.add_argument("--allow-delete", action="store_true")
    report.add_argument("--unity-ready-manifest")
    one = _add_common(sub.add_parser("shelve-ui-import", help="Plan-only check/preview for UI import P4 stage; write steps must be confirmed separately."))
    one.add_argument("--desc", required=True)
    one.add_argument("--yes", action="store_true")
    one.add_argument("--force", action="store_true")
    one.add_argument("--allow-delete", action="store_true")
    submit = _add_common(sub.add_parser("submit"))
    submit.add_argument("--cl", "--changelist")
    ask = sub.add_parser("ask")
    ask.add_argument("text")
    ask.add_argument("--config")
    ask.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    registry = WorkspaceRegistry(Path(args.config) if getattr(args, "config", None) else None)
    ops = P4Operations(registry=registry)

    try:
        if args.command == "ask":
            intent = parse_intent(args.text, registry)
            payload = _execute_intent(ops, intent.as_dict())
        elif args.command == "status":
            payload = ops.get_status(args.workflow, args.workspace)
        elif args.command == "workspace-info":
            payload = ops.workspace_info(args.workflow, args.workspace)
        elif args.command == "streams":
            payload = ops.streams(args.workflow, args.workspace)
        elif args.command == "check":
            payload = ops.run_check(args.workflow, args.workspace, allow_delete=args.allow_delete)
        elif args.command == "preview":
            payload = ops.preview_changes(args.workflow, args.workspace, allow_delete=args.allow_delete)
        elif args.command == "create-cl":
            payload = ops.create_changelist(args.workflow, args.workspace, args.desc, yes=args.yes)
        elif args.command == "reconcile":
            payload = ops.reconcile_changelist(args.workflow, args.workspace, args.cl, allow_delete=args.allow_delete, yes=args.yes)
        elif args.command == "shelve":
            payload = ops.shelve_changelist(args.workflow, args.workspace, args.cl, force=args.force, allow_delete=args.allow_delete, yes=args.yes)
        elif args.command == "switch-stream":
            payload = ops.switch_stream(args.workflow, args.workspace, args.stream, preview=(args.preview or not args.yes), yes=args.yes, allow_opened=args.allow_opened)
        elif args.command == "get-latest":
            payload = ops.get_latest(args.workflow, args.workspace, scope=args.scope, preview=args.preview, yes=args.yes)
        elif args.command == "report":
            payload = ops.generate_report(
                args.workflow,
                args.workspace,
                args.cl,
                allow_delete=args.allow_delete,
                unity_ready_manifest=args.unity_ready_manifest,
            )
        elif args.command == "shelve-ui-import":
            payload = ops.shelve_ui_import(args.workflow, args.workspace, args.desc, yes=args.yes, force=args.force, allow_delete=args.allow_delete)
        elif args.command == "submit":
            payload = {"ok": False, "error": "Shelve-only mode: submit is disabled."}
        else:
            payload = {"ok": False, "error": f"unknown command: {args.command}"}
    except Exception as exc:
        payload = {"ok": False, "error": str(exc)}

    _print(payload, as_json=bool(getattr(args, "json", False)))
    return 0 if payload.get("ok", False) else 1


def _add_common(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("--workspace", default="spark_client_ui")
    parser.add_argument("--workflow")
    parser.add_argument("--config")
    parser.add_argument("--json", action="store_true")
    return parser


def _execute_intent(ops: P4Operations, intent: dict[str, Any]) -> dict[str, Any]:
    if intent.get("refused"):
        return {"ok": False, "intent": intent, "error": intent.get("message")}
    name = intent.get("intent")
    workflow = intent.get("workflow")
    workspace = intent.get("workspace")
    if name == "status":
        return ops.get_status(workflow, workspace)
    if name == "check":
        return ops.run_check(workflow, workspace)
    if name == "preview":
        return ops.preview_changes(workflow, workspace)
    if name == "create_cl":
        return {"ok": False, "needs_desc": True, "intent": intent, "message": "create-cl requires --desc."}
    if name == "reconcile":
        return {"ok": False, "needs_cl": True, "intent": intent, "message": "reconcile requires --cl."}
    if name == "shelve":
        return {"ok": False, "needs_cl": True, "intent": intent, "message": "shelve requires --cl."}
    if name == "report":
        return {"ok": False, "needs_cl": True, "intent": intent, "message": "report requires --cl."}
    return {"ok": False, "intent": intent, "message": "unknown intent"}


def _print(payload: dict[str, Any], as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if payload.get("operation") == "report" and payload.get("report_text"):
        print(payload["report_text"])
        return
    if not payload.get("ok"):
        print(f"ERROR: {payload.get('error') or payload.get('message') or 'operation failed'}")
    print(f"Operation: {payload.get('operation', '-')}")
    print(f"Workspace: {payload.get('workspace', '-')}")
    print(f"P4PORT: {payload.get('p4port', '-')}")
    print(f"P4USER: {payload.get('p4user', '-')}")
    print(f"P4CLIENT: {payload.get('p4client', '-')}")
    if "root" in payload:
        print(f"Root: {payload['root']}")
    if "mode" in payload:
        print(f"Mode: {payload['mode']}")
    print("Submit: disabled")
    if payload.get("managed_paths"):
        print("Managed paths:")
        for path in payload["managed_paths"]:
            print(f"- {path}")
    if payload.get("changelist_id"):
        print(f"Changelist: {payload['changelist_id']}")
    if payload.get("shelved_changelist_id"):
        print(f"Shelved Changelist: {payload['shelved_changelist_id']}")
    if payload.get("stats"):
        print("File stats:")
        for key, value in payload["stats"].items():
            print(f"- {key}: {value}")
    if payload.get("files"):
        print("Files:")
        for item in payload["files"]:
            print(f"{item.get('action', 'unknown')}  {item.get('path', '')}")
    safety = payload.get("safety") or {}
    if safety:
        print("Safety:")
        for key, value in (safety.get("checks") or {}).items():
            print(f"- {key}: {value}")
        for warning in safety.get("warnings") or []:
            print(f"WARNING: {warning}")
        for error in safety.get("errors") or []:
            print(f"ERROR: {error}")
    if payload.get("report_text"):
        print()
        print(payload["report_text"])


if __name__ == "__main__":
    raise SystemExit(main())
