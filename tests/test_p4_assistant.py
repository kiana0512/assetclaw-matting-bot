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
from assetclaw_matting.skills import p4_skills
from assetclaw_matting.skills.registry import get_skill_meta


def test_workspace_registry_uses_example_and_env_override(monkeypatch) -> None:
    monkeypatch.setenv("P4CLIENT", "override_client")
    registry = WorkspaceRegistry(config_path=Path.cwd() / "tools/p4_assistant/missing.yaml")
    workspace = registry.resolve("p4_shelve_assistant")
    assert registry.loaded_from_example is True
    assert workspace.p4port == "spark-p4.lilithgames.com:1666"
    assert workspace.p4user == "keizhang"
    assert workspace.p4client == "override_client"
    assert "workspaces.yaml" in registry.hint
    combined = registry.resolve(workspace="p4_shelve_assistant/spark_client_ui")
    assert combined.workflow == "p4_shelve_assistant"
    assert combined.name == "spark_client_ui"


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
    workspace = registry.resolve("p4_shelve_assistant")
    runner = P4Runner(p4_exe="p4.exe")
    with pytest.raises(PermissionError):
        runner.run(workspace, ["info"], cwd=tmp_path)


def test_nl_intent_parses_common_requests() -> None:
    registry = WorkspaceRegistry(config_path=Path.cwd() / "tools/p4_assistant/missing.yaml")
    status = parse_intent("查看 p4 workspace 状态", registry)
    assert status.intent == "status"
    assert status.workflow == "p4_shelve_assistant"
    assert status.need_clarification is False

    preview = parse_intent("预览 Assets/Art/UI/SpritesAnim/Emoji/...", registry)
    assert preview.intent == "preview"
    assert preview.paths == ("Assets/Art/UI/SpritesAnim/Emoji/...",)

    submit = parse_intent("提交 changelist 123456", registry)
    assert submit.intent == "refuse"
    assert submit.refused is True
    assert submit.risk_level == "high"

    setup = parse_intent("预览创建 p4 workspace", registry)
    assert setup.intent == "preview"


def test_operations_status_uses_safe_preview_commands() -> None:
    class FakeRunner:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        def for_workspace(self, workspace):
            self.workspace = workspace
            return self

        def _result(self, args, stdout=""):
            self.calls.append(args)
            return P4CommandResult(args, "D:/Spark/Client", 0, stdout, "", 0.01, f"p4 {args[0]} ok")

        def info(self):
            return self._result(["info"], "User name: keizhang\nClient name: spark_client_ui\nClient root: D:\\Spark\\Client\n")

        def login_status(self):
            return self._result(["login", "-s"], "User keizhang ticket expires in 12 hours.\n")

        def client_spec(self):
            return self._result(["client", "-o", "spark_client_ui"], "Client:\tspark_client_ui\n\nRoot:\tD:\\Spark\\Client\n")

        def opened(self):
            return self._result(["opened"], "//Spark/Client/Assets/Art/UI/a.png#1 - edit default change (binary)\n")

        def pending_changelists(self):
            return self._result(["changes", "pending"], "Change 123 on 2026/07/30 by keizhang@spark_client_ui 'pending'\n")

        def shelved_changelists(self):
            return self._result(["changes", "shelved"], "Change 122 on 2026/07/29 by keizhang@spark_client_ui 'shelved'\n")

    registry = WorkspaceRegistry(config_path=Path.cwd() / "tools/p4_assistant/missing.yaml")
    fake = FakeRunner()
    ops = P4Operations(registry=registry, runner=fake)  # type: ignore[arg-type]
    payload = ops.get_status("p4_shelve_assistant")
    assert payload["ok"] is True
    assert [call[0] for call in fake.calls] == ["info", "login", "client", "opened", "changes", "changes"]
    assert payload["opened_files_count"] == 1
    assert payload["pending_changelists"] == ["123"]
    assert payload["shelved_changelists"] == ["122"]
    assert payload["submit"] == "disabled"


def test_operations_inventory_summarizes_depots_clients_and_mapping() -> None:
    payload = p4_skills.inventory()
    assert payload["ok"] is False
    assert "Shelve-only" in payload["error"]


def test_operations_compare_depot_summarizes_head_vs_have() -> None:
    payload = p4_skills.compare_depot()
    assert payload["ok"] is False
    assert "disabled" in payload["error"]


def test_operations_workspace_details_reads_each_client_spec() -> None:
    payload = p4_skills.workspace_details()
    assert payload["ok"] is False
    assert "disabled" in payload["error"]


def test_operations_preview_setup_workspace_builds_stream_spec() -> None:
    payload = p4_skills.preview_setup_workspace()
    assert payload["ok"] is False
    assert "disabled" in payload["error"]


def test_operations_inspect_treats_internal_server_address_as_reachable() -> None:
    assert p4_skills.inspect is not None
    assert p4_skills.status is not None


def test_operations_list_workflows_reads_synced_workspace(tmp_path: Path) -> None:
    payload = p4_skills.list_workflows()
    assert payload["ok"] is False
    assert "outside" in payload["error"]


def test_workspace_warning_for_risky_root() -> None:
    registry = WorkspaceRegistry(config_path=Path.cwd() / "tools/p4_assistant/missing.yaml")
    workspace = registry.resolve("p4_shelve_assistant")
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
    assert workflow_call[0].skill == "p4.help"

    inventory_call = brain._infer_tool_calls("p4有多少depot和workspace 对应关系是啥")
    assert inventory_call[0].skill == "p4.help"
    assert brain._infer_tool_calls("可以查看这个p4的depot吗")[0].skill == "p4.help"
    assert brain._infer_tool_calls("show p4 depots and workspaces")[0].skill == "p4.help"
    assert brain._infer_tool_calls("ok check p4 status")[0].skill == "p4.status"
    assert brain._infer_tool_calls("p4 对比本地和 depot 差异")[0].skill == "p4.help"
    assert brain._infer_tool_calls("p4 depot 工作区详情")[0].skill == "p4.help"

    status_call = brain._infer_tool_calls("p4 查看状态")
    assert status_call[0].skill == "p4.status"

    sync_call = brain._infer_tool_calls("帮我预览 workflows 拉最新")
    assert sync_call[0].skill == "p4.help"

    changelist_call = brain._infer_tool_calls("p4 创建 changelist")
    assert changelist_call[0].skill == "p4.create_cl"
    list_cl_call = brain._infer_tool_calls("现在工作区有哪些 CL 的 id")
    assert list_cl_call[0].skill == "p4.list_cls"
    cleanup_call = brain._infer_tool_calls("这个版本不对，帮我删除 CL 6901")
    assert cleanup_call[0].skill == "p4.cleanup_cl"
    assert cleanup_call[0].arguments["cl"] == "6901"
    setup_call = brain._infer_tool_calls("预览创建 p4 workspace")
    assert setup_call[0].skill == "p4.help"


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
