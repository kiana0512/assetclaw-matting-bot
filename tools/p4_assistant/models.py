from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class P4FileAction(str, Enum):
    ADD = "add"
    EDIT = "edit"
    DELETE = "delete"
    MOVE = "move"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class P4WorkspaceConfig:
    name: str
    p4port: str
    p4user: str
    p4client: str
    root: Path
    mode: str = "shelve_only"
    managed_paths: tuple[str, ...] = (
        "Assets/Art/UI/SpritesAnim/Emoji/...",
        "Assets/Art/UI/SpritesAnim/CharacterAnim/...",
    )
    forbidden_paths: tuple[str, ...] = ()
    stream: str = ""
    source: str = "configured"
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class P4Workspace(P4WorkspaceConfig):
    workflow: str = "p4_shelve_assistant"
    depot_paths: tuple[str, ...] = ()
    local_paths: tuple[str, ...] = ()
    description: str = ""
    risk_level: str = "normal"


@dataclass(frozen=True)
class WorkflowConfig:
    name: str
    description: str = ""
    default_workspace: str = ""
    workspaces: dict[str, P4Workspace] = field(default_factory=dict)


@dataclass(frozen=True)
class P4CommandResult:
    command: list[str]
    cwd: str
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float
    safe_summary: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "cwd": self.cwd,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_seconds": self.duration_seconds,
            "safe_summary": self.safe_summary,
            "ok": self.ok,
        }


@dataclass(frozen=True)
class P4Status:
    p4port: str
    p4user: str
    p4client: str
    root: str
    cwd: str
    stream: str = ""
    logged_in: bool = False
    workspace_matches_config: bool = False
    info: dict[str, str] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class P4FileChange:
    depot_path: str = ""
    local_path: str = ""
    action: P4FileAction = P4FileAction.UNKNOWN
    changelist_id: str = ""
    file_type: str = ""
    is_managed_path: bool = False
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def path(self) -> str:
        return self.local_path or self.depot_path


@dataclass(frozen=True)
class ChangelistInfo:
    changelist_id: str
    description: str = ""
    status: str = "pending"
    files: tuple[P4FileChange, ...] = ()


@dataclass(frozen=True)
class ShelveInfo:
    changelist_id: str
    shelved_changelist_id: str
    files: tuple[P4FileChange, ...] = ()
    forced: bool = False


@dataclass(frozen=True)
class SafetyResult:
    ok: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    checks: dict[str, str] = field(default_factory=dict)

    def require_ok(self) -> None:
        if not self.ok:
            raise PermissionError("; ".join(self.errors) or "P4 safety check failed")


@dataclass(frozen=True)
class ReportData:
    workspace: P4WorkspaceConfig
    changelist_id: str
    shelved_changelist_id: str = ""
    files: tuple[P4FileChange, ...] = ()
    safety: SafetyResult = field(default_factory=lambda: SafetyResult(ok=True))
    stream_or_branch: str = ""
    note: str = ""


@dataclass(frozen=True)
class IntentResult:
    intent: str
    workflow: str | None = None
    workspace: str | None = None
    paths: tuple[str, ...] = ()
    requires_confirmation: bool = False
    risk_level: str = "safe"
    raw_text: str = ""
    need_clarification: bool = False
    clarification: str = ""
    choices: tuple[str, ...] = ()
    refused: bool = False
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "workflow": self.workflow,
            "workspace": self.workspace,
            "paths": list(self.paths),
            "requires_confirmation": self.requires_confirmation,
            "risk_level": self.risk_level,
            "raw_text": self.raw_text,
            "need_clarification": self.need_clarification,
            "clarification": self.clarification,
            "choices": list(self.choices),
            "refused": self.refused,
            "message": self.message,
        }
