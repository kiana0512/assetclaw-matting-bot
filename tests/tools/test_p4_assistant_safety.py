from __future__ import annotations

from pathlib import Path

import pytest

from tools.p4_assistant.models import P4FileAction, P4FileChange, P4WorkspaceConfig
from tools.p4_assistant.safety import (
    block_submit_command,
    check_unity_meta_pairs,
    ensure_shelve_only_mode,
    is_path_allowed,
    is_path_forbidden,
    validate_opened_files,
)
from tools.p4_assistant.workspace_registry import WorkspaceRegistry


def _workspace(**kwargs) -> P4WorkspaceConfig:
    data = {
        "name": "spark_client_ui",
        "p4port": "spark-p4.lilithgames.com:1666",
        "p4user": "keizhang",
        "p4client": "keizhang_L-20260528ZLGJA_8024",
        "root": Path("D:/Spark/Client"),
        "managed_paths": ("Assets/Art/UI/SpritesAnim/Emoji/...", "Assets/Art/UI/SpritesAnim/CharacterAnim/..."),
        "forbidden_paths": ("ProjectSettings/...", "Library/...", "Temp/...", "Assets/Art/Character/..."),
    }
    data.update(kwargs)
    return P4WorkspaceConfig(**data)


def test_path_allowlist_judgement() -> None:
    ws = _workspace()
    assert is_path_allowed("Assets/Art/UI/SpritesAnim/Emoji/happy/a.png", ws.managed_paths)
    assert is_path_allowed("//depot/main/Assets/Art/UI/SpritesAnim/CharacterAnim/a.anim", ws.managed_paths)
    assert is_path_allowed("//streams/main/Client/Assets/Art/UI/SpritesAnim/CharacterAnim/a.anim", ws.managed_paths)
    assert not is_path_allowed("Assets/Art/Character/hero/a.png", ws.managed_paths)


def test_forbidden_path_judgement() -> None:
    ws = _workspace()
    assert is_path_forbidden("ProjectSettings/ProjectVersion.txt", ws.forbidden_paths)
    assert is_path_forbidden("Library/cache.bin", ws.forbidden_paths)
    assert not is_path_forbidden("Assets/Art/UI/SpritesAnim/Emoji/a.png", ws.forbidden_paths)


def test_delete_default_blocks_and_allow_delete_warns() -> None:
    ws = _workspace()
    files = [P4FileChange(local_path="Assets/Art/UI/SpritesAnim/Emoji/old.png", action=P4FileAction.DELETE)]
    blocked = validate_opened_files(files, ws)
    assert not blocked.ok
    assert "allow-delete" in blocked.errors[0]
    allowed = validate_opened_files(files, ws, allow_delete=True)
    assert allowed.ok
    assert allowed.warnings


def test_submit_is_always_blocked() -> None:
    with pytest.raises(PermissionError):
        block_submit_command()


def test_main_stream_does_not_block_shelve_only() -> None:
    result = ensure_shelve_only_mode(_workspace(stream="//spark/main"))
    assert result.ok


def test_password_fields_are_warned_and_ignored(tmp_path: Path) -> None:
    config = tmp_path / "workspaces.yaml"
    config.write_text(
        """
workspaces:
  spark_client_ui:
    p4port: spark-p4.lilithgames.com:1666
    p4user: keizhang
    p4client: client
    root: D:/Spark/Client
    password: ignored-placeholder
""",
        encoding="utf-8",
    )
    registry = WorkspaceRegistry(config)
    ws = registry.resolve(workspace="spark_client_ui")
    assert ws.p4client == "client"
    assert any("password-like fields ignored" in item for item in ws.warnings)


def test_meta_pair_warning() -> None:
    files = [P4FileChange(local_path="Assets/Art/UI/SpritesAnim/Emoji/new.png", action=P4FileAction.ADD)]
    result = check_unity_meta_pairs(files)
    assert result.ok
    assert result.warnings
