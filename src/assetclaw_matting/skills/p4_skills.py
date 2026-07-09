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
- p4.workspace_info：查看 workspace/root/stream/managed_paths
- p4.streams：查看可见 streams
- p4.switch_stream：默认只预览切换 stream，真实切换需要 yes，且有 opened files 默认阻断
- p4.get_latest：默认只 sync managed paths；all scope 需要 yes
- p4.create_cl：创建 pending changelist，描述自动补 [Shelve-only] 信息
- p4.reconcile：只把白名单 UI 目录 reconcile 到指定 CL
- p4.shelve：安全检查后 shelve 指定 CL，可用 force 覆盖已有 shelf
- p4.list_cls：查看当前 workspace 的 pending / shelved CL
- p4.cleanup_cl：删除指定 CL 的 shelf、revert 打开的文件，并删除 pending CL
- p4.report：生成可以直接复制到飞书的 CL / shelf / 文件统计 / 安全检查报告
- p4.shelve_ui_import：只输出 check / preview / 下一步计划，不批量执行写操作

不能做：
- 不支持 submit
- 不支持一键批量创建 CL + reconcile + shelve
- 不支持 merge / copy / stream 创建
- 不处理 UI 白名单外目录
- 不保存 P4 密码

默认白名单：
- Assets/Art/UI/SpritesAnim/Emoji/...
- Assets/Art/UI/SpritesAnim/CharacterAnim/...

delete 文件默认阻断，需要明确 allow_delete。create_cl / reconcile / shelve / cleanup_cl 必须二次确认。未登录时请手动运行 p4 login。"""
    return {"ok": True, "text": text}


def status(workflow: str | None = None, workspace: str | None = None, **_: Any) -> dict[str, Any]:
    return _ops().get_status(workflow=workflow, workspace=workspace)


def list_cls(workflow: str | None = None, workspace: str | None = None, **_: Any) -> dict[str, Any]:
    return _ops().list_changelists(workflow=workflow, workspace=workspace)


def cleanup_cl(workflow: str | None = None, workspace: str | None = None, cl: str | int | None = None, changelist: str | int | None = None, allow_delete: bool = False, **_: Any) -> dict[str, Any]:
    target_cl = cl or changelist
    if not target_cl:
        return {"ok": False, "error": "cl is required"}
    return _ops().cleanup_changelist(workflow=workflow, workspace=workspace, cl=target_cl, allow_delete=allow_delete)


def preview_cleanup_cl_confirmation(arguments: dict[str, Any], confirmation_id: str) -> str:
    cl = arguments.get("cl") or arguments.get("changelist") or ""
    workspace = arguments.get("workspace") or "默认 workspace"
    return "\n".join(
        [
            "请确认是否清理 P4 changelist：",
            f"CL：{cl}",
            f"Workspace：{workspace}",
            "动作：删除 shelf -> revert 该 CL 打开的文件 -> 删除 pending CL。",
            "Submit：disabled，不会提交。",
            f"回复：确认执行 {confirmation_id}",
        ]
    )


def inspect(workflow: str | None = None, workspace: str | None = None, **kwargs: Any) -> dict[str, Any]:
    return status(workflow=workflow, workspace=workspace, **kwargs)


def workspace_info(workflow: str | None = None, workspace: str | None = None, **_: Any) -> dict[str, Any]:
    return _ops().workspace_info(workflow=workflow, workspace=workspace)


def streams(workflow: str | None = None, workspace: str | None = None, **_: Any) -> dict[str, Any]:
    return _ops().streams(workflow=workflow, workspace=workspace)


def check(workflow: str | None = None, workspace: str | None = None, allow_delete: bool = False, **_: Any) -> dict[str, Any]:
    return _ops().run_check(workflow=workflow, workspace=workspace, allow_delete=allow_delete)


def preview(workflow: str | None = None, workspace: str | None = None, allow_delete: bool = False, paths: list[str] | None = None, **_: Any) -> dict[str, Any]:
    return _ops().preview_changes(workflow=workflow, workspace=workspace, allow_delete=allow_delete, paths=paths)


def preview_reconcile(workflow: str | None = None, workspace: str | None = None, allow_delete: bool = False, **kwargs: Any) -> dict[str, Any]:
    return preview(workflow=workflow, workspace=workspace, allow_delete=allow_delete, **kwargs)


def create_cl(workflow: str | None = None, workspace: str | None = None, desc: str = "", description: str = "", yes: bool = False, **_: Any) -> dict[str, Any]:
    return _ops().create_changelist(workflow=workflow, workspace=workspace, desc=desc or description, yes=yes)


def create_changelist(workflow: str | None = None, workspace: str | None = None, description: str = "", desc: str = "", **kwargs: Any) -> dict[str, Any]:
    return create_cl(workflow=workflow, workspace=workspace, desc=desc or description, **kwargs)


def reconcile(workflow: str | None = None, workspace: str | None = None, cl: str | int | None = None, changelist: str | int | None = None, allow_delete: bool = False, yes: bool = False, paths: list[str] | None = None, **_: Any) -> dict[str, Any]:
    target_cl = cl or changelist
    if not target_cl:
        return {"ok": False, "error": "cl is required"}
    return _ops().reconcile_changelist(workflow=workflow, workspace=workspace, cl=target_cl, allow_delete=allow_delete, yes=yes, paths=paths)


def shelve(workflow: str | None = None, workspace: str | None = None, cl: str | int | None = None, changelist: str | int | None = None, force: bool = False, allow_delete: bool = False, yes: bool = False, **_: Any) -> dict[str, Any]:
    target_cl = cl or changelist
    if not target_cl:
        return {"ok": False, "error": "cl is required"}
    return _ops().shelve_changelist(workflow=workflow, workspace=workspace, cl=target_cl, force=force, allow_delete=allow_delete, yes=yes)


def switch_stream(workflow: str | None = None, workspace: str | None = None, stream: str = "", preview: bool = True, yes: bool = False, allow_opened: bool = False, **_: Any) -> dict[str, Any]:
    return _ops().switch_stream(workflow=workflow, workspace=workspace, stream=stream, preview=preview or not yes, yes=yes, allow_opened=allow_opened)


def get_latest(workflow: str | None = None, workspace: str | None = None, scope: str = "managed", preview: bool = False, yes: bool = False, **_: Any) -> dict[str, Any]:
    return _ops().get_latest(workflow=workflow, workspace=workspace, scope=scope, preview=preview, yes=yes)


def report(
    workflow: str | None = None,
    workspace: str | None = None,
    cl: str | int | None = None,
    changelist: str | int | None = None,
    allow_delete: bool = False,
    unity_ready_manifest: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    target_cl = cl or changelist
    if not target_cl:
        return {"ok": False, "error": "cl is required"}
    return _ops().generate_report(
        workflow=workflow,
        workspace=workspace,
        cl=target_cl,
        allow_delete=allow_delete,
        unity_ready_manifest=unity_ready_manifest,
    )


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
    return {"ok": False, "error": "Shelve-only mode: use p4.get_latest. It defaults to managed paths and keeps submit disabled."}


def merge(**_: Any) -> dict[str, Any]:
    return {"ok": False, "error": "Shelve-only mode: merge/copy/stream operations are disabled."}


def _ops() -> P4Operations:
    return P4Operations(registry=WorkspaceRegistry())
