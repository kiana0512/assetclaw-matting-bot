from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

from tools.p4_assistant.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_FORBIDDEN_PATHS,
    DEFAULT_MANAGED_PATHS,
    EXAMPLE_CONFIG,
    LOCAL_CONFIG_PATH,
    default_config_hint,
)
from tools.p4_assistant.models import P4Workspace, WorkflowConfig

PASSWORD_KEYS = {"password", "passwd", "p4passwd", "P4PASSWD"}


class WorkspaceRegistry:
    def __init__(self, config_path: Path | str | None = None) -> None:
        self.config_path = self._pick_config_path(config_path)
        self.loaded_from_example = self.config_path is None
        self.hint = default_config_hint() if self.loaded_from_example else ""
        self._raw = self._load_raw_config()
        self._warnings: list[str] = []
        self._workflows = self._parse_workflows(self._raw)

    @property
    def warnings(self) -> list[str]:
        return list(self._warnings)

    def workflows(self) -> dict[str, WorkflowConfig]:
        return self._workflows

    def workflow_names(self) -> list[str]:
        return sorted(self._workflows)

    def workspace_choices(self) -> list[str]:
        choices: list[str] = []
        for workflow, cfg in self._workflows.items():
            for workspace in cfg.workspaces:
                choices.append(f"{workflow}/{workspace}")
        return choices

    def resolve(self, workflow: str | None = None, workspace: str | None = None) -> P4Workspace:
        if workspace and "/" in workspace:
            left, right = workspace.split("/", 1)
            workflow = workflow or left
            workspace = right
        workflow_name = workflow or self._single_workflow_name()
        if workflow_name not in self._workflows:
            raise KeyError(f"unknown workflow: {workflow_name}. choices: {', '.join(self.workflow_names())}")
        cfg = self._workflows[workflow_name]
        workspace_name = workspace or cfg.default_workspace or self._single_workspace_name(cfg)
        if workspace_name not in cfg.workspaces:
            raise KeyError(f"unknown workspace: {workspace_name}. choices: {', '.join(cfg.workspaces)}")
        return cfg.workspaces[workspace_name]

    def _pick_config_path(self, config_path: Path | str | None) -> Path | None:
        if config_path:
            candidate = Path(config_path)
            return candidate if candidate.exists() else None
        if LOCAL_CONFIG_PATH.exists():
            return LOCAL_CONFIG_PATH
        if DEFAULT_CONFIG_PATH.exists():
            return DEFAULT_CONFIG_PATH
        return None

    def _single_workflow_name(self) -> str:
        if len(self._workflows) != 1:
            raise ValueError(f"workflow is required. choices: {', '.join(self.workflow_names())}")
        return next(iter(self._workflows))

    def _single_workspace_name(self, cfg: WorkflowConfig) -> str:
        if len(cfg.workspaces) != 1:
            raise ValueError(f"workspace is required. choices: {', '.join(cfg.workspaces)}")
        return next(iter(cfg.workspaces))

    def _load_raw_config(self) -> dict[str, Any]:
        if self.config_path is None:
            return copy.deepcopy(EXAMPLE_CONFIG)
        try:
            import yaml  # type: ignore
        except Exception as exc:
            raise RuntimeError("Reading workspace config requires PyYAML.") from exc
        data = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError("workspace config must be a mapping")
        return data

    def _parse_workflows(self, raw: dict[str, Any]) -> dict[str, WorkflowConfig]:
        if "workspaces" in raw:
            return self._parse_flat_workspaces(raw)
        return self._parse_legacy_workflows(raw)

    def _parse_flat_workspaces(self, raw: dict[str, Any]) -> dict[str, WorkflowConfig]:
        workspace_map: dict[str, P4Workspace] = {}
        for workspace_name, data in (raw.get("workspaces") or {}).items():
            if isinstance(data, dict):
                workspace_map[workspace_name] = self._workspace_from_dict("p4_shelve_assistant", workspace_name, data, {})
        if not workspace_map:
            raise ValueError("workspace config has no workspaces")
        default_workspace = str(raw.get("default_workspace") or next(iter(workspace_map)))
        return {
            "p4_shelve_assistant": WorkflowConfig(
                name="p4_shelve_assistant",
                description="P4 UI asset shelve-only assistant",
                default_workspace=default_workspace,
                workspaces=workspace_map,
            )
        }

    def _parse_legacy_workflows(self, raw: dict[str, Any]) -> dict[str, WorkflowConfig]:
        workflows: dict[str, WorkflowConfig] = {}
        for workflow_name, workflow_data in (raw.get("workflows") or {}).items():
            if not isinstance(workflow_data, dict):
                continue
            workspace_map: dict[str, P4Workspace] = {}
            for workspace_name, data in (workflow_data.get("workspaces") or {}).items():
                if isinstance(data, dict):
                    workspace_map[workspace_name] = self._workspace_from_dict(workflow_name, workspace_name, data, workflow_data)
            workflows[workflow_name] = WorkflowConfig(
                name=workflow_name,
                description=str(workflow_data.get("description") or ""),
                default_workspace=str(workflow_data.get("default_workspace") or ""),
                workspaces=workspace_map,
            )
        if not workflows:
            raise ValueError("workspace config has no workflows or workspaces")
        return workflows

    def _workspace_from_dict(self, workflow: str, name: str, data: dict[str, Any], workflow_data: dict[str, Any]) -> P4Workspace:
        warnings = self._password_warnings(name, data)
        mode = str(data.get("mode") or "shelve_only")
        if mode != "shelve_only":
            warnings.append(f"workspace {name}: unsupported mode {mode!r}; forced to shelve_only")
            mode = "shelve_only"
        p4port = os.environ.get("P4PORT") or str(data.get("p4port") or "")
        p4user = os.environ.get("P4USER") or str(data.get("p4user") or "")
        p4client = os.environ.get("P4CLIENT") or str(data.get("p4client") or "")
        managed = tuple(str(item).replace("\\", "/") for item in (data.get("managed_paths") or data.get("local_paths") or DEFAULT_MANAGED_PATHS))
        forbidden = tuple(str(item).replace("\\", "/") for item in (data.get("forbidden_paths") or DEFAULT_FORBIDDEN_PATHS))
        self._warnings.extend(warnings)
        return P4Workspace(
            workflow=workflow,
            name=name,
            p4port=p4port,
            p4user=p4user,
            p4client=p4client,
            root=Path(str(data.get("root") or "")),
            mode=mode,
            managed_paths=managed,
            forbidden_paths=forbidden,
            stream=str(data.get("stream") or ""),
            depot_paths=tuple(str(item) for item in (data.get("depot_paths") or [])),
            local_paths=tuple(str(item) for item in (data.get("local_paths") or [])),
            description=str(workflow_data.get("description") or ""),
            risk_level=str(data.get("risk_level") or "normal"),
            source="example" if self.loaded_from_example else "configured",
            warnings=tuple(warnings),
        )

    def _password_warnings(self, name: str, data: dict[str, Any]) -> list[str]:
        found = [key for key in data if str(key).lower() in {item.lower() for item in PASSWORD_KEYS}]
        if not found:
            return []
        return [f"workspace {name}: password-like fields ignored: {', '.join(found)}"]
