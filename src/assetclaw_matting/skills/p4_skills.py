from __future__ import annotations

from typing import Any

from tools.p4_assistant.operations import P4Operations
from tools.p4_assistant.workspace_registry import WorkspaceRegistry

SUBMIT_REFUSAL = "当前 P4 助手是 Shelve-only 模式，不支持 submit。请使用 shelve 并把 changelist ID / shelf ID 交给负责人 review。"


def help(**_: Any) -> dict[str, Any]:
    text = """\
P4 UI 表情资源助手现在是 Shelve-only 模式，只服务 Unity UI 表情/角色动画资源导入后的 P4 管理。

能做：
- p4.status：查看 P4PORT / P4USER / P4CLIENT / root / stream / 登录状态
- p4.check：检查 p4、登录、workspace、managed_paths、白名单外 opened 文件和 Shelve-only 模式
- p4.preview：只对 UI 白名单目录执行 reconcile -n，不改变 P4 状态
- p4.create_cl：创建 pending changelist，描述自动补 [Shelve-only] 信息
- p4.reconcile：只把白名单 UI 目录 reconcile 到指定 CL
- p4.shelve：安全检查后 shelve 指定 CL，可用 force 覆盖已有 shelf
- p4.report：生成可以直接复制到飞书的 CL / shelf / 文件统计 / 安全检查报告
- p4.shelve_ui_import：check -> preview -> create CL -> reconcile -> shelve -> report，执行前需要 yes

不能做：
- 不支持 submit
- 不支持 sync / 拉最新
- 不支持 merge / copy / stream 创建
- 不处理 UI 白名单外目录
- 不保存 P4 密码

默认白名单：
- Assets/Art/UI/SpritesAnim/Emoji/...
- Assets/Art/UI/SpritesAnim/CharacterAnim/...

delete 文件默认阻断，需要明确 allow_delete。未登录时请手动运行 p4 login。"""
    return {"ok": True, "text": text}


def status(workflow: str | None = None, workspace: str | None = None, **_: Any) -> dict[str, Any]:
    return _ops().get_status(workflow=workflow, workspace=workspace)


def inspect(workflow: str | None = None, workspace: str | None = None, **kwargs: Any) -> dict[str, Any]:
    return status(workflow=workflow, workspace=workspace, **kwargs)


def check(workflow: str | None = None, workspace: str | None = None, allow_delete: bool = False, **_: Any) -> dict[str, Any]:
    return _ops().run_check(workflow=workflow, workspace=workspace, allow_delete=allow_delete)


def preview(workflow: str | None = None, workspace: str | None = None, allow_delete: bool = False, **_: Any) -> dict[str, Any]:
    return _ops().preview_changes(workflow=workflow, workspace=workspace, allow_delete=allow_delete)


def preview_reconcile(workflow: str | None = None, workspace: str | None = None, allow_delete: bool = False, **kwargs: Any) -> dict[str, Any]:
    return preview(workflow=workflow, workspace=workspace, allow_delete=allow_delete, **kwargs)


def create_cl(workflow: str | None = None, workspace: str | None = None, desc: str = "", description: str = "", **_: Any) -> dict[str, Any]:
    return _ops().create_changelist(workflow=workflow, workspace=workspace, desc=desc or description)


def create_changelist(workflow: str | None = None, workspace: str | None = None, description: str = "", desc: str = "", **kwargs: Any) -> dict[str, Any]:
    return create_cl(workflow=workflow, workspace=workspace, desc=desc or description, **kwargs)


def reconcile(workflow: str | None = None, workspace: str | None = None, cl: str | int | None = None, changelist: str | int | None = None, allow_delete: bool = False, **_: Any) -> dict[str, Any]:
    target_cl = cl or changelist
    if not target_cl:
        return {"ok": False, "error": "cl is required"}
    return _ops().reconcile_changelist(workflow=workflow, workspace=workspace, cl=target_cl, allow_delete=allow_delete)


def shelve(workflow: str | None = None, workspace: str | None = None, cl: str | int | None = None, changelist: str | int | None = None, force: bool = False, allow_delete: bool = False, **_: Any) -> dict[str, Any]:
    target_cl = cl or changelist
    if not target_cl:
        return {"ok": False, "error": "cl is required"}
    return _ops().shelve_changelist(workflow=workflow, workspace=workspace, cl=target_cl, force=force, allow_delete=allow_delete)


def report(workflow: str | None = None, workspace: str | None = None, cl: str | int | None = None, changelist: str | int | None = None, allow_delete: bool = False, **_: Any) -> dict[str, Any]:
    target_cl = cl or changelist
    if not target_cl:
        return {"ok": False, "error": "cl is required"}
    return _ops().generate_report(workflow=workflow, workspace=workspace, cl=target_cl, allow_delete=allow_delete)


def shelve_ui_import(workflow: str | None = None, workspace: str | None = None, desc: str = "", description: str = "", yes: bool = False, force: bool = False, allow_delete: bool = False, **_: Any) -> dict[str, Any]:
    return _ops().shelve_ui_import(workflow=workflow, workspace=workspace, desc=desc or description, yes=yes, force=force, allow_delete=allow_delete)


def inventory(**_: Any) -> dict[str, Any]:
    return {"ok": False, "error": "Shelve-only mode: depot/workspace inventory is outside this UI import assistant. Use p4.status/check/preview/shelve/report."}


def workspace_details(**_: Any) -> dict[str, Any]:
    return {"ok": False, "error": "Shelve-only mode: workspace setup/details tooling is disabled. Use p4.status for current workspace verification."}


def compare_depot(**_: Any) -> dict[str, Any]:
    return {"ok": False, "error": "Shelve-only mode: depot/head comparison and sync planning are disabled. Use p4.preview for managed UI paths."}


def list_workflows(**_: Any) -> dict[str, Any]:
    return {"ok": False, "error": "Shelve-only mode: workflow listing is outside this P4 assistant."}


def preview_setup_workspace(**_: Any) -> dict[str, Any]:
    return {"ok": False, "error": "Shelve-only mode: workspace client creation is disabled."}


def setup_workspace(**_: Any) -> dict[str, Any]:
    return {"ok": False, "error": "Shelve-only mode: workspace client creation is disabled."}


def preview_sync(**_: Any) -> dict[str, Any]:
    return sync()


def build_changelist(workflow: str | None = None, workspace: str | None = None, desc: str = "", description: str = "", **kwargs: Any) -> dict[str, Any]:
    return create_cl(workflow=workflow, workspace=workspace, desc=desc or description or "[UI Emoji Import]", **kwargs)


def submit(**_: Any) -> dict[str, Any]:
    return {"ok": False, "error": SUBMIT_REFUSAL}


def sync(**_: Any) -> dict[str, Any]:
    return {"ok": False, "error": "Shelve-only mode: sync / 拉最新 is disabled in this P4 assistant."}


def merge(**_: Any) -> dict[str, Any]:
    return {"ok": False, "error": "Shelve-only mode: merge/copy/stream operations are disabled."}


def _ops() -> P4Operations:
    return P4Operations(registry=WorkspaceRegistry())
