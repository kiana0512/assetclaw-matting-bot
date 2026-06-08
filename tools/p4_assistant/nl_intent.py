from __future__ import annotations

import re

from tools.p4_assistant.models import IntentResult
from tools.p4_assistant.workspace_registry import WorkspaceRegistry

REFUSAL = "当前 P4 助手是 Shelve-only 模式，不支持 submit。请使用 shelve 并把 changelist ID / shelf ID 交给负责人 review。"


def parse_intent(text: str, registry: WorkspaceRegistry | None = None) -> IntentResult:
    raw = (text or "").strip()
    lowered = raw.lower()
    forbidden = ("submit", "提交", "merge", "合流", "copy up", "copy", "stream 创建", "创建 stream", "create_stream", "trunk")
    if any(item in lowered for item in forbidden) or any(item in raw for item in ("提交", "合 main", "合 trunk")):
        return IntentResult(intent="refuse", raw_text=raw, refused=True, risk_level="high", message=REFUSAL)

    intent = "unknown"
    if any(kw in lowered for kw in ("status", "状态", "workspace 状态", "登录状态")):
        intent = "status"
    elif any(kw in lowered for kw in ("check", "检查", "安全检查")):
        intent = "check"
    elif any(kw in lowered for kw in ("preview", "预览", "reconcile -n")):
        intent = "preview"
    elif any(kw in lowered for kw in ("create-cl", "create changelist", "创建 cl", "创建 changelist", "新建 changelist")):
        intent = "create_cl"
    elif any(kw in lowered for kw in ("reconcile", "调和", "纳入改动")):
        intent = "reconcile"
    elif any(kw in lowered for kw in ("shelve", "搁置", "上架 shelf")):
        intent = "shelve"
    elif any(kw in lowered for kw in ("report", "报告", "飞书")):
        intent = "report"

    workflow = _pick_workflow(raw, registry)
    workspace = _pick_workspace(raw, registry, workflow)
    if registry and intent != "unknown":
        clarification = _clarification_if_needed(registry, workflow, workspace)
        if clarification:
            return IntentResult(intent=intent, workflow=workflow, workspace=workspace, raw_text=raw, need_clarification=True, clarification=clarification, choices=tuple(registry.workspace_choices()))
    return IntentResult(intent=intent, workflow=workflow, workspace=workspace, paths=tuple(_extract_paths(raw)), raw_text=raw)


def _pick_workflow(text: str, registry: WorkspaceRegistry | None) -> str | None:
    if not registry:
        return None
    for name in registry.workflow_names():
        if name in text:
            return name
    workflows = registry.workflow_names()
    return workflows[0] if len(workflows) == 1 else None


def _pick_workspace(text: str, registry: WorkspaceRegistry | None, workflow: str | None) -> str | None:
    if not registry or not workflow:
        return None
    cfg = registry.workflows().get(workflow)
    if not cfg:
        return None
    for name in cfg.workspaces:
        if name in text:
            return name
    return cfg.default_workspace if cfg.default_workspace else (next(iter(cfg.workspaces)) if len(cfg.workspaces) == 1 else None)


def _extract_paths(text: str) -> list[str]:
    return [item.strip() for item in re.findall(r"([A-Za-z]:\\[^\s，。]+|//[^\s，。]+|Assets/[^\s，。]+)", text) if item.strip()]


def _clarification_if_needed(registry: WorkspaceRegistry, workflow: str | None, workspace: str | None) -> str:
    if not workflow:
        return "需要指定 workflow。"
    cfg = registry.workflows().get(workflow)
    if not cfg:
        return "workflow 不存在。"
    if not workspace:
        return "需要指定 workspace。"
    return ""
