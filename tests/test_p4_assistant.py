from __future__ import annotations

from pathlib import Path

import pytest

from tools.p4_assistant.models import P4CommandResult
from tools.p4_assistant.nl_intent import parse_intent
from tools.p4_assistant.operations import P4Operations
from tools.p4_assistant.p4_runner import P4Runner
from tools.p4_assistant.safety import ensure_command_allowed, redact, workspace_warnings
from tools.p4_assistant.workspace_registry import WorkspaceRegistry
from assetclaw_matting.brain.local_command_brain import LocalCommandBrain
from assetclaw_matting.brain.result_formatter import format_skill_results
from assetclaw_matting.skills.registry import get_skill_meta


def test_workspace_registry_uses_example_and_env_override(monkeypatch) -> None:
    monkeypatch.setenv("P4CLIENT", "override_client")
    registry = WorkspaceRegistry(config_path=Path.cwd() / "tools/p4_assistant/missing.yaml")
    workspace = registry.resolve("ai_art_comfyui")
    assert registry.loaded_from_example is True
    assert workspace.p4port == "rd-center-p4.lilith.com:1666"
    assert workspace.p4user == "kianaren"
    assert workspace.p4client == "override_client"
    assert "workspaces.yaml" in registry.hint
    combined = registry.resolve(workspace="ai_art_comfyui/ai_art_comfyui_trunk_f")
    assert combined.workflow == "ai_art_comfyui"
    assert combined.name == "ai_art_comfyui_trunk_f"


def test_safety_blocks_mutating_commands_without_confirmation() -> None:
    ensure_command_allowed(["sync", "-n", "//depot/..."])
    with pytest.raises(PermissionError):
        ensure_command_allowed(["sync", "//depot/..."])
    with pytest.raises(PermissionError):
        ensure_command_allowed(["submit", "-c", "123"])
    with pytest.raises(PermissionError):
        ensure_command_allowed(["client", "-i"])
    ensure_command_allowed(["client", "-i"], confirmation=True)
    with pytest.raises(PermissionError):
        ensure_command_allowed(["obliterate", "//depot/..."], confirmation=True)
    assert "P4PASSWD=<redacted>" in redact("P4PASSWD=secret")


def test_runner_uses_p4_exe_env(monkeypatch) -> None:
    monkeypatch.setenv("P4_EXE", "C:/Perforce/p4.exe")
    runner = P4Runner()
    assert runner.p4_exe == "C:/Perforce/p4.exe"


def test_runner_requires_cwd_under_workspace(tmp_path: Path) -> None:
    registry = WorkspaceRegistry(config_path=Path.cwd() / "tools/p4_assistant/missing.yaml")
    workspace = registry.resolve("ai_art_comfyui")
    runner = P4Runner(p4_exe="p4.exe")
    with pytest.raises(PermissionError):
        runner.run(workspace, ["info"], cwd=tmp_path)


def test_nl_intent_parses_common_requests() -> None:
    registry = WorkspaceRegistry(config_path=Path.cwd() / "tools/p4_assistant/missing.yaml")
    status = parse_intent("看看 ai_art_comfyui 这个工作区现在改了什么", registry)
    assert status.intent == "status"
    assert status.workflow == "ai_art_comfyui"
    assert status.need_clarification is False

    sync = parse_intent("帮我预览 workflows 拉最新", registry)
    assert sync.intent == "preview_sync"
    assert sync.paths == ("workflows",)

    submit = parse_intent("提交 changelist 123456", registry)
    assert submit.intent == "submit"
    assert submit.requires_confirmation is True
    assert submit.risk_level == "high"

    setup = parse_intent("预览创建 p4 workspace", registry)
    assert setup.intent == "preview_setup_workspace"
    real_setup = parse_intent("初始化 p4 workspace", registry)
    assert real_setup.intent == "setup_workspace"
    assert real_setup.requires_confirmation is True


def test_operations_status_uses_safe_preview_commands() -> None:
    class FakeRunner:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        def run(self, workspace, args, **kwargs):
            self.calls.append(args)
            if args[0] == "opened":
                stdout = "//ai_art_comfyui/trunk/workflows/a.json#1 - edit default change (text)\n"
            elif args[0] == "reconcile":
                stdout = "//ai_art_comfyui/trunk/workflows/new.json - opened for add\n"
            elif args[0] == "diff":
                stdout = "==== //ai_art_comfyui/trunk/workflows/a.json#1 - F:\\P4Workspace\\kianaren\\ai_art_comfyui_trunk\\workflows\\a.json ====\n+node\n"
            else:
                stdout = ""
            return P4CommandResult(args, "F:/P4Workspace/kianaren/ai_art_comfyui_trunk", 0, stdout, "", 0.01, f"p4 {args[0]} ok")

    registry = WorkspaceRegistry(config_path=Path.cwd() / "tools/p4_assistant/missing.yaml")
    fake = FakeRunner()
    ops = P4Operations(registry=registry, runner=fake)  # type: ignore[arg-type]
    payload = ops.get_status("ai_art_comfyui")
    assert payload["ok"] is True
    assert fake.calls == [
        ["opened"],
        ["reconcile", "-n", "//ai_art_comfyui/trunk/workflows/..."],
        ["diff", "-du"],
    ]
    assert payload["summary"]["opened_count"] == 1
    assert payload["summary"]["local_adds"] == ["//ai_art_comfyui/trunk/workflows/new.json"]


def test_operations_inventory_summarizes_depots_clients_and_mapping() -> None:
    class FakeRunner:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        def run(self, workspace, args, **kwargs):
            self.calls.append(args)
            if args[0] == "info":
                stdout = (
                    "User name: kianaren\n"
                    "Client name: kianaren_ai_art_comfyui_trunk_f\n"
                    "Client root: F:\\P4Workspace\\kianaren\\ai_art_comfyui_trunk\n"
                    "Client stream: //ai_art_comfyui/trunk\n"
                    "Server address: WIN-SG24T1H9VU1:1666\n"
                )
            elif args[0] == "depots":
                stdout = "Depot ai_art_comfyui 2026/01/01 stream //ai_art_comfyui/...\nDepot tools 2026/01/02 local //tools/...\n"
            elif args[0] == "clients":
                stdout = "Client kianaren_ai_art_comfyui_trunk_f 2026/06/05 root F:\\P4Workspace\\kianaren\\ai_art_comfyui_trunk 'test client'\n"
            elif args[:2] == ["client", "-o"]:
                stdout = "Client:\tkianaren_ai_art_comfyui_trunk_f\n\nRoot:\tF:\\P4Workspace\\kianaren\\ai_art_comfyui_trunk\n\nStream:\t//ai_art_comfyui/trunk\n"
            elif args[0] == "streams":
                stdout = "Stream //ai_art_comfyui/trunk mainline none 'Main stream'\n"
            else:
                stdout = ""
            return P4CommandResult(args, "F:/P4Workspace/kianaren/ai_art_comfyui_trunk", 0, stdout, "", 0.01, f"p4 {args[0]} ok")

    registry = WorkspaceRegistry(config_path=Path.cwd() / "tools/p4_assistant/missing.yaml")
    fake = FakeRunner()
    payload = P4Operations(registry=registry, runner=fake).inventory("ai_art_comfyui")  # type: ignore[arg-type]
    assert payload["ok"] is True
    assert fake.calls == [
        ["info"],
        ["depots"],
        ["clients", "-u", "kianaren"],
        ["client", "-o", "kianaren_ai_art_comfyui_trunk_f"],
        ["streams", "//ai_art_comfyui/trunk"],
    ]
    assert payload["summary"]["counts"]["depots"] == 2
    assert payload["summary"]["counts"]["clients_for_user"] == 1
    assert payload["summary"]["configured_mappings"][0]["depot"] == "//ai_art_comfyui/trunk/workflows/..."


def test_operations_compare_depot_summarizes_head_vs_have() -> None:
    class FakeRunner:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        def run(self, workspace, args, **kwargs):
            self.calls.append(args)
            if args[0] == "fstat":
                stdout = (
                    "... depotFile //ai_art_comfyui/trunk/workflows/a.json\n"
                    "... headRev 2\n"
                    "... haveRev 1\n"
                    "... headAction edit\n"
                )
            elif args[0] == "sync":
                stdout = "//ai_art_comfyui/trunk/workflows/a.json#2 - updating F:\\P4Workspace\\kianaren\\ai_art_comfyui_trunk\\workflows\\a.json\n"
            else:
                stdout = ""
            return P4CommandResult(args, "F:/P4Workspace/kianaren/ai_art_comfyui_trunk", 0, stdout, "", 0.01, f"p4 {args[0]} ok")

    registry = WorkspaceRegistry(config_path=Path.cwd() / "tools/p4_assistant/missing.yaml")
    fake = FakeRunner()
    payload = P4Operations(registry=registry, runner=fake).compare_depot("ai_art_comfyui")  # type: ignore[arg-type]
    assert payload["ok"] is True
    assert fake.calls[0][:3] == ["fstat", "-T", "depotFile,headRev,haveRev,headAction,action"]
    assert fake.calls[0][-1] == "//ai_art_comfyui/trunk/..."
    assert payload["summary"]["clean"] is False
    assert payload["summary"]["out_of_date"][0]["depotFile"] == "//ai_art_comfyui/trunk/workflows/a.json"
    assert payload["summary"]["top_level_items"][0]["name"] == "workflows"


def test_operations_workspace_details_reads_each_client_spec() -> None:
    class FakeRunner:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        def run(self, workspace, args, **kwargs):
            self.calls.append(args)
            if args[0] == "clients":
                stdout = (
                    "Client one 2026/06/01 root E:\\one 'one client'\n"
                    "Client kianaren_ai_art_comfyui_trunk_f 2026/06/05 root F:\\P4Workspace\\kianaren\\ai_art_comfyui_trunk 'current'\n"
                )
            elif args[:2] == ["client", "-o"]:
                name = args[2]
                root = "F:\\P4Workspace\\kianaren\\ai_art_comfyui_trunk" if name == "kianaren_ai_art_comfyui_trunk_f" else "E:\\one"
                stream = "//ai_art_comfyui/trunk" if name == "kianaren_ai_art_comfyui_trunk_f" else ""
                stdout = f"Client:\t{name}\n\nUpdate:\t2026/06/05 10:00:00\n\nAccess:\t2026/06/05 11:00:00\n\nRoot:\t{root}\n\nStream:\t{stream}\n\nView:\n\t//ai_art_comfyui/trunk/... //{name}/...\n"
            else:
                stdout = ""
            return P4CommandResult(args, "F:/P4Workspace/kianaren/ai_art_comfyui_trunk", 0, stdout, "", 0.01, f"p4 {args[0]} ok")

    registry = WorkspaceRegistry(config_path=Path.cwd() / "tools/p4_assistant/missing.yaml")
    payload = P4Operations(registry=registry, runner=FakeRunner()).workspace_details("ai_art_comfyui")  # type: ignore[arg-type]
    assert payload["summary"]["count"] == 2
    current = [item for item in payload["summary"]["items"] if item["is_current"]][0]
    assert current["stream"] == "//ai_art_comfyui/trunk"
    assert current["view_lines"][0].startswith("//ai_art_comfyui/trunk/")


def test_operations_preview_setup_workspace_builds_stream_spec() -> None:
    registry = WorkspaceRegistry(config_path=Path.cwd() / "tools/p4_assistant/missing.yaml")
    ops = P4Operations(registry=registry)
    payload = ops.preview_setup_workspace("ai_art_comfyui")
    assert payload["ok"] is True
    assert payload["operation"] == "preview_setup_workspace"
    assert "Client:\tkianaren_ai_art_comfyui_trunk_f" in payload["client_spec"]
    assert "Root:\tF:\\P4Workspace\\kianaren\\ai_art_comfyui_trunk" in payload["client_spec"]
    assert "Stream:\t//ai_art_comfyui/trunk" in payload["client_spec"]
    assert "View:" not in payload["client_spec"]


def test_operations_inspect_treats_internal_server_address_as_reachable() -> None:
    class FakeRunner:
        def run(self, workspace, args, **kwargs):
            stdout = (
                "User name: kianaren\n"
                "Client name: kianaren_ai_art_comfyui_trunk_f\n"
                "Client root: F:\\P4Workspace\\kianaren\\ai_art_comfyui_trunk\n"
                "Client stream: //ai_art_comfyui/trunk\n"
                "Server address: WIN-SG24T1H9VU1:1666\n"
            )
            return P4CommandResult(args, "F:/P4Workspace/kianaren/ai_art_comfyui_trunk", 0, stdout, "", 0.01, "p4 info ok")

    registry = WorkspaceRegistry(config_path=Path.cwd() / "tools/p4_assistant/missing.yaml")
    payload = P4Operations(registry=registry, runner=FakeRunner()).inspect_workspace("ai_art_comfyui")  # type: ignore[arg-type]
    assert payload["checks"]["server_reachable"] is True
    assert "server_matches" not in payload["checks"]
    assert payload["checks"]["user_matches"] is True
    assert payload["info"]["server address"] == "WIN-SG24T1H9VU1:1666"


def test_operations_list_workflows_reads_synced_workspace(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    workflows = root / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "demo.json").write_text(
        '{"1":{"class_type":"LoadImage"},"2":{"class_type":"SaveImage"}}',
        encoding="utf-8",
    )
    registry = WorkspaceRegistry(config_path=Path.cwd() / "tools/p4_assistant/missing.yaml")
    workspace = registry.resolve("ai_art_comfyui")
    local_workspace = workspace.__class__(**{**workspace.__dict__, "root": root})

    class FakeRegistry:
        def resolve(self, workflow=None, workspace=None):
            return local_workspace

    payload = P4Operations(registry=FakeRegistry()).list_workflows("ai_art_comfyui")  # type: ignore[arg-type]
    assert payload["ok"] is True
    assert payload["summary"]["count"] == 1
    item = payload["summary"]["items"][0]
    assert item["name"] == "demo.json"
    assert item["node_count"] == 2
    assert item["load_image_count"] == 1
    assert item["save_image_count"] == 1


def test_workspace_warning_for_risky_root() -> None:
    registry = WorkspaceRegistry(config_path=Path.cwd() / "tools/p4_assistant/missing.yaml")
    workspace = registry.resolve("ai_art_comfyui")
    risky = workspace.__class__(**{**workspace.__dict__, "root": Path("C:/Users/kianaren/Downloads/ws")})
    warnings = workspace_warnings(risky)
    assert any("C:" in item for item in warnings)
    assert any("Desktop/Downloads/OneDrive" in item for item in warnings)


def test_p4_skills_registered_and_local_brain_routes() -> None:
    assert get_skill_meta("p4.help")
    assert get_skill_meta("p4.status")
    assert get_skill_meta("p4.inventory")
    assert get_skill_meta("p4.workspace_details")
    assert get_skill_meta("p4.compare_depot")
    assert get_skill_meta("p4.list_workflows")
    assert get_skill_meta("p4.preview_setup_workspace")
    assert get_skill_meta("p4.setup_workspace")
    assert get_skill_meta("p4.preview_sync")
    assert get_skill_meta("p4.preview_reconcile")
    assert get_skill_meta("p4.build_changelist")
    assert get_skill_meta("p4.list_cls")
    assert get_skill_meta("p4.cleanup_cl")["requires_confirmation"] is True

    brain = LocalCommandBrain()
    help_call = brain._infer_tool_calls("p4现在功能有哪些")
    assert help_call[0].skill == "p4.help"

    workflow_call = brain._infer_tool_calls("我们现在有哪些工作流呢p4")
    assert workflow_call[0].skill == "p4.list_workflows"

    inventory_call = brain._infer_tool_calls("p4有多少depot和workspace 对应关系是啥")
    assert inventory_call[0].skill == "p4.inventory"
    assert brain._infer_tool_calls("可以查看这个p4的depot吗")[0].skill == "p4.inventory"
    assert brain._infer_tool_calls("show p4 depots and workspaces")[0].skill == "p4.inventory"
    assert brain._infer_tool_calls("ok check p4 status")[0].skill == "p4.status"
    assert brain._infer_tool_calls("可以对比下本地的工作区和服务器上的depot的文件差异吗")[0].skill == "p4.compare_depot"
    assert brain._infer_tool_calls("这个depot对应的三个工作区的详情信息是啥")[0].skill == "p4.workspace_details"

    status_call = brain._infer_tool_calls("看看 ai_art_comfyui 现在改了什么")
    assert status_call[0].skill == "p4.status"
    assert status_call[0].arguments["workflow"] == "ai_art_comfyui"

    sync_call = brain._infer_tool_calls("帮我预览 workflows 拉最新")
    assert sync_call[0].skill == "p4.preview_sync"
    assert sync_call[0].arguments["paths"] == ["workflows"]

    changelist_call = brain._infer_tool_calls("帮我生成这次 changelist 描述")
    assert changelist_call[0].skill == "p4.build_changelist"
    list_cl_call = brain._infer_tool_calls("现在工作区有哪些 CL 的 id")
    assert list_cl_call[0].skill == "p4.list_cls"
    cleanup_call = brain._infer_tool_calls("这个版本不对，帮我删除 CL 6901")
    assert cleanup_call[0].skill == "p4.cleanup_cl"
    assert cleanup_call[0].arguments["cl"] == "6901"
    setup_call = brain._infer_tool_calls("预览创建 p4 workspace")
    assert setup_call[0].skill == "p4.preview_setup_workspace"


def test_p4_formatter_outputs_summary() -> None:
    text = format_skill_results(
        [
            {
                "ok": True,
                "skill": "p4.status",
                "result": {
                    "ok": True,
                    "operation": "get_status",
                    "workflow": "ai_art_comfyui",
                    "workspace": "ai_art_comfyui_trunk_f",
                    "readable_summary": "p4 opened ok",
                    "summary": {
                        "opened_count": 1,
                        "opened_files": ["//ai_art_comfyui/trunk/workflows/a.json"],
                        "local_adds": ["//ai_art_comfyui/trunk/workflows/new.json"],
                    },
                },
            }
        ]
    )
    assert "P4：状态检查" in text
    assert "opened 1" in text

    inventory_text = format_skill_results(
        [
            {
                "ok": True,
                "skill": "p4.inventory",
                "result": {
                    "ok": True,
                    "operation": "inventory",
                    "workflow": "ai_art_comfyui",
                    "workspace": "ai_art_comfyui_trunk_f",
                    "p4port": "rd-center-p4.lilith.com:1666",
                    "p4user": "kianaren",
                    "p4client": "kianaren_ai_art_comfyui_trunk_f",
                    "summary": {
                        "counts": {"depots": 2, "clients_for_user": 1, "configured_workspaces": 1},
                        "info": {
                            "server address": "WIN-SG24T1H9VU1:1666",
                            "user name": "kianaren",
                            "client name": "kianaren_ai_art_comfyui_trunk_f",
                            "client root": "F:\\P4Workspace\\kianaren\\ai_art_comfyui_trunk",
                            "client stream": "//ai_art_comfyui/trunk",
                        },
                        "depots": [{"name": "ai_art_comfyui", "type": "stream", "map": "//ai_art_comfyui/..."}],
                        "clients": [{"name": "kianaren_ai_art_comfyui_trunk_f", "root": "F:\\P4Workspace\\kianaren\\ai_art_comfyui_trunk"}],
                        "configured_mappings": [{"depot": "//ai_art_comfyui/trunk/workflows/...", "local": "F:\\P4Workspace\\kianaren\\ai_art_comfyui_trunk\\workflows"}],
                    },
                },
            }
        ]
    )
    assert "2 个 depot" in inventory_text
    assert "//ai_art_comfyui/trunk/workflows/..." in inventory_text

    compare_text = format_skill_results(
        [
            {
                "ok": True,
                "skill": "p4.compare_depot",
                "result": {
                    "ok": True,
                    "operation": "compare_depot",
                    "summary": {
                        "depot_file_count": 1,
                        "clean": False,
                        "sync_preview": {"preview_count": 1},
                        "out_of_date": [{"depotFile": "//ai_art_comfyui/trunk/workflows/a.json", "haveRev": "1", "headRev": "2"}],
                        "not_synced": [],
                        "deleted_at_head": [],
                        "missing_top_level_items": [{"name": "models"}, {"name": "custom_nodes"}],
                        "local_status": {"opened_count": 0, "local_adds": [], "local_edits": [], "local_deletes": [], "diff_files": []},
                    },
                },
            }
        ]
    )
    assert "P4：本地/Depot 对比" in compare_text
    assert "发现差异" in compare_text
    assert "本地缺少 depot 顶层项" in compare_text

    details_text = format_skill_results(
        [
            {
                "ok": True,
                "skill": "p4.workspace_details",
                "result": {
                    "ok": True,
                    "operation": "workspace_details",
                    "summary": {
                        "count": 1,
                        "items": [
                            {
                                "name": "kianaren_ai_art_comfyui_trunk_f",
                                "is_current": True,
                                "root": "F:\\P4Workspace\\kianaren\\ai_art_comfyui_trunk",
                                "stream": "//ai_art_comfyui/trunk",
                                "view_lines": ["//ai_art_comfyui/trunk/... //kianaren_ai_art_comfyui_trunk_f/..."],
                            }
                        ],
                    },
                },
            }
        ]
    )
    assert "工作区详情：1 个" in details_text
    assert "kianaren_ai_art_comfyui_trunk_f" in details_text

    workflows_text = format_skill_results(
        [
            {
                "ok": True,
                "skill": "p4.list_workflows",
                "result": {
                    "ok": True,
                    "operation": "list_workflows",
                    "workflow": "ai_art_comfyui",
                    "workspace": "ai_art_comfyui_trunk_f",
                    "summary": {
                        "count": 1,
                        "roots": ["F:\\P4Workspace\\kianaren\\ai_art_comfyui_trunk\\workflows"],
                        "items": [
                            {
                                "name": "demo.json",
                                "node_count": 2,
                                "load_image_count": 1,
                                "save_image_count": 1,
                            }
                        ],
                    },
                },
            }
        ]
    )
    assert "P4 工作流：1 个" in workflows_text
    assert "demo.json" in workflows_text

    setup_text = format_skill_results(
        [
            {
                "ok": True,
                "skill": "p4.preview_setup_workspace",
                "result": {
                    "ok": True,
                    "operation": "preview_setup_workspace",
                    "workflow": "ai_art_comfyui",
                    "workspace": "ai_art_comfyui_trunk_f",
                    "root": "F:\\P4Workspace\\kianaren\\ai_art_comfyui_trunk",
                    "stream": "//ai_art_comfyui/trunk",
                    "client_spec": "Client:\tkianaren_ai_art_comfyui_trunk_f\n\nStream:\t//ai_art_comfyui/trunk",
                },
            }
        ]
    )
    assert "client spec 预览" in setup_text
