from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.p4_assistant.models import P4CommandResult
from tools.p4_assistant.operations import P4Operations
from tools.p4_assistant.safety import ensure_command_allowed
from tools.p4_assistant.workspace_registry import WorkspaceRegistry


class FakeRunner:
    def __init__(self) -> None:
        self.workspace = None
        self.calls: list[list[str]] = []
        self.opened_stdout = ""
        self.preview_stdout = "//depot/main/Assets/Art/UI/SpritesAnim/Emoji/a.png#1 - add change default (binary)"

    def for_workspace(self, workspace):
        self.workspace = workspace
        return self

    def is_available(self) -> bool:
        return True

    def _result(self, args: list[str], stdout: str = "") -> P4CommandResult:
        self.calls.append(args)
        return P4CommandResult(["p4", *args], str(self.workspace.root), 0, stdout, "", 0.01, "ok")

    def info(self):
        return self._result(["info"], f"Client name: {self.workspace.p4client}\nClient root: {self.workspace.root}\n")

    def login_status(self):
        return self._result(["login", "-s"], "User is logged in.\n")

    def client_spec(self):
        return self._result(["client", "-o", self.workspace.p4client], f"Root: {self.workspace.root}\nStream: //streams/main\n")

    def workspace_where(self, paths=None):
        return self._result(["where", *(paths or [])])

    def pending_changelists(self):
        return self._result(["changes", "-s", "pending"], "Change 123 on 2026/06/09 by user@client *pending* desc\n")

    def shelved_changelists(self):
        return self._result(["changes", "-s", "shelved"], "Change 124 on 2026/06/09 by user@client *shelved* desc\n")

    def streams(self):
        return self._result(["streams"], "Stream //streams/main mainline none 'main'\nStream //streams/001 development //streams/main '001'\n")

    def opened(self, cl=None):
        return self._result(["opened"] if cl is None else ["opened", "-c", str(cl)], self.opened_stdout)

    def reconcile_preview(self, paths):
        return self._result(["reconcile", "-n", *paths], self.preview_stdout)

    def create_changelist(self, description):
        self.calls.append(["change", "-i"])
        return "999"

    def reconcile_to_changelist(self, cl, paths):
        return self._result(["reconcile", "-c", str(cl), *paths])

    def reopen_to_changelist(self, cl, paths):
        return self._result(["reopen", "-c", str(cl), *paths])

    def shelve(self, cl, force=False):
        return self._result(["shelve", "-c", str(cl)])

    def describe(self, cl, shelved=False):
        return self._result(["describe", "-S", str(cl)], self.opened_stdout)

    def delete_shelve(self, cl):
        return self._result(["shelve", "-d", "-c", str(cl)])

    def revert_changelist(self, cl, paths):
        return self._result(["revert", "-c", str(cl), *paths])

    def delete_changelist(self, cl):
        return self._result(["change", "-d", str(cl)])

    def sync_preview(self, paths):
        return self._result(["sync", "-n", *paths])

    def sync(self, paths):
        return self._result(["sync", *paths])

    def switch_stream(self, stream):
        return self._result(["client", "-s", "-S", stream, self.workspace.p4client])


def _ops(tmp_path: Path, runner: FakeRunner | None = None) -> tuple[P4Operations, FakeRunner]:
    config = tmp_path / "workspaces.yaml"
    config.write_text(
        """
workspaces:
  spark_client:
    p4port: spark-p4.lilithgames.com:1666
    p4user: keizhang
    p4client: spark_client
    root: D:/Spark/Client
    managed_paths:
      - Assets/Art/UI/SpritesAnim/Emoji/...
      - Assets/Art/UI/Animation/Emoji/...
    forbidden_paths:
      - ProjectSettings/...
      - Assets/Plugins/...
""",
        encoding="utf-8",
    )
    fake = runner or FakeRunner()
    return P4Operations(registry=WorkspaceRegistry(config), runner=fake), fake


def test_submit_command_is_blocked() -> None:
    with pytest.raises(PermissionError):
        ensure_command_allowed(["submit"], confirmation=True)
    with pytest.raises(PermissionError):
        ensure_command_allowed(["revert", "//..."], confirmation=True)
    ensure_command_allowed(["revert", "-c", "123", "Assets/Art/UI/SpritesAnim/Emoji/..."], confirmation=True)


def test_preview_uses_reconcile_n(tmp_path: Path) -> None:
    ops, runner = _ops(tmp_path)
    payload = ops.preview_changes(workspace="spark_client")
    assert payload["ok"] is True
    assert any(call[:2] == ["reconcile", "-n"] for call in runner.calls)


def test_preview_accepts_target_paths(tmp_path: Path) -> None:
    ops, runner = _ops(tmp_path)
    payload = ops.preview_changes(workspace="spark_client", paths=["Assets/Art/UI/SpritesAnim/Emoji/Heather/..."])
    assert payload["paths"] == ["Assets/Art/UI/SpritesAnim/Emoji/Heather/..."]
    assert runner.calls[-1] == ["reconcile", "-n", "Assets/Art/UI/SpritesAnim/Emoji/Heather/..."]


def test_write_operations_need_confirmation(tmp_path: Path) -> None:
    ops, _ = _ops(tmp_path)
    assert ops.create_changelist(workspace="spark_client", desc="demo")["needs_confirmation"] is True
    assert ops.reconcile_changelist(workspace="spark_client", cl="999")["needs_confirmation"] is True
    assert ops.shelve_changelist(workspace="spark_client", cl="999")["needs_confirmation"] is True


def test_list_and_cleanup_changelists(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.opened_stdout = "//depot/main/Assets/Art/UI/SpritesAnim/Emoji/a.png#1 - edit change 999 (binary)"
    ops, _ = _ops(tmp_path, runner)

    listed = ops.list_changelists(workspace="spark_client")
    assert listed["ok"] is True
    assert [item["id"] for item in listed["items"]] == ["124", "123"]

    cleaned = ops.cleanup_changelist(workspace="spark_client", cl="999")
    assert cleaned["ok"] is True
    assert cleaned["deleted_shelf"] is True
    assert cleaned["reverted"] is True
    assert cleaned["deleted_changelist"] is True
    assert ["shelve", "-d", "-c", "999"] in runner.calls
    assert any(call[:3] == ["revert", "-c", "999"] for call in runner.calls)
    assert ["change", "-d", "999"] in runner.calls


def test_batch_shelve_ui_import_never_writes(tmp_path: Path) -> None:
    ops, runner = _ops(tmp_path)
    payload = ops.shelve_ui_import(workspace="spark_client", desc="demo", yes=True)
    assert payload["blocked"] is True
    assert payload["needs_separate_confirmation"] is True
    assert not any(call == ["change", "-i"] for call in runner.calls)
    assert not any(call[:1] == ["shelve"] for call in runner.calls)


def test_switch_stream_preview_and_opened_block(tmp_path: Path) -> None:
    runner = FakeRunner()
    ops, _ = _ops(tmp_path, runner)
    preview = ops.switch_stream(workspace="spark_client", stream="//streams/001")
    assert preview["preview"] is True
    runner.opened_stdout = "//depot/main/Assets/Art/UI/SpritesAnim/Emoji/a.png#1 - edit change 99 (binary)"
    blocked = ops.switch_stream(workspace="spark_client", stream="//streams/001", yes=True, preview=False)
    assert blocked["blocked"] is True


def test_get_latest_defaults_to_managed_paths(tmp_path: Path) -> None:
    ops, runner = _ops(tmp_path)
    payload = ops.get_latest(workspace="spark_client", scope="managed")
    assert payload["ok"] is True
    assert payload["paths"] == ["Assets/Art/UI/SpritesAnim/Emoji/...", "Assets/Art/UI/Animation/Emoji/..."]
    assert any(call[0] == "sync" and "//..." not in call for call in runner.calls)
    all_scope = ops.get_latest(workspace="spark_client", scope="all")
    assert all_scope["needs_confirmation"] is True


def test_forbidden_path_blocks_shelve(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.opened_stdout = "//depot/main/Assets/Plugins/bad.dll#1 - add change 999 (binary)"
    ops, _ = _ops(tmp_path, runner)
    payload = ops.shelve_changelist(workspace="spark_client", cl="999", yes=True)
    assert payload["ok"] is False
    assert "forbidden_paths" in json.dumps(payload["safety"], ensure_ascii=False)


def test_report_reads_unity_ready_manifest(tmp_path: Path) -> None:
    ready = tmp_path / "unity_ready"
    ready.mkdir()
    manifest = ready / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "sourceManifest": "../source_manifest.json",
                "packages": {
                    "scene": {"json": "scene/animation_resource_manifest.json", "tasks": []},
                    "emoji": {"json": "emoji/animation_resource_manifest.json", "tasks": []},
                },
            }
        ),
        encoding="utf-8",
    )
    runner = FakeRunner()
    runner.opened_stdout = "//depot/main/Assets/Art/UI/SpritesAnim/Emoji/a.png#1 - add change 999 (binary)"
    ops, _ = _ops(tmp_path, runner)
    report = ops.generate_report(workspace="spark_client", cl="999", unity_ready_manifest=str(manifest))
    assert "Submit" in report["report_text"] or "submit disabled" in report["report_text"]
    assert "Unity Ready:" in report["report_text"]
    assert "animation_resource_manifest.json" in report["report_text"]
