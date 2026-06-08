from __future__ import annotations

import re
from pathlib import Path

from tools.p4_assistant.models import P4FileAction, P4FileChange, P4WorkspaceConfig, SafetyResult

SENSITIVE_KEYS = ("password", "passwd", "ticket", "token", "cookie", "p4passwd", "p4ticket")
RISKY_ROOT_PARTS = ("desktop", "downloads", "onedrive")
ALLOWED_COMMANDS = {"info", "login", "client", "reconcile", "change", "opened", "describe", "shelve", "where"}
BLOCKED_COMMANDS = {"submit", "merge", "copy", "stream", "revert", "delete", "integrate"}
UNITY_ASSET_EXTENSIONS = {".png", ".jpg", ".jpeg", ".anim", ".controller", ".prefab", ".mat", ".asset", ".json"}


def redact(text: str) -> str:
    result = str(text or "")
    for key in SENSITIVE_KEYS:
        result = re.sub(rf"({key}\s*[=:]\s*)\S+", rf"\1<redacted>", result, flags=re.IGNORECASE)
    result = re.sub(r"(P4PASSWD=)\S+", r"\1<redacted>", result, flags=re.IGNORECASE)
    return result


def block_submit_command() -> None:
    raise PermissionError("Shelve-only mode: submit is disabled.")


def command_risk(args: list[str]) -> str:
    if not args:
        return "high"
    subcommand = args[0].lower()
    if subcommand in BLOCKED_COMMANDS:
        return "high"
    if subcommand in {"reconcile", "change", "shelve"} and "-n" not in args:
        return "medium"
    return "safe"


def requires_confirmation(args: list[str]) -> bool:
    return False


def ensure_command_allowed(args: list[str], confirmation: bool = False) -> None:
    if not args:
        raise ValueError("missing p4 subcommand")
    subcommand = args[0].lower()
    if subcommand == "submit":
        block_submit_command()
    if subcommand in BLOCKED_COMMANDS:
        raise PermissionError(f"Shelve-only mode: p4 {subcommand} is disabled.")
    if subcommand not in ALLOWED_COMMANDS:
        raise PermissionError(f"p4 subcommand is not allowed in shelve-only mode: {subcommand}")


def ensure_cwd_in_workspace(cwd: Path, workspace: P4WorkspaceConfig) -> None:
    root = workspace.root.resolve()
    target = cwd.resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise PermissionError(f"cwd must be under workspace root: {root}") from exc


def ensure_shelve_only_mode(config: P4WorkspaceConfig) -> SafetyResult:
    if config.mode != "shelve_only":
        return SafetyResult(False, errors=(f"Unsupported mode {config.mode!r}. Shelve-only mode is required.",), checks={"mode": "FAIL"})
    return SafetyResult(True, checks={"mode": "PASS", "submit disabled": "PASS"})


def validate_managed_paths(config: P4WorkspaceConfig) -> SafetyResult:
    errors = []
    warnings = []
    if not config.managed_paths:
        errors.append("managed_paths is empty")
    for path in config.managed_paths:
        if is_path_forbidden(path, config.forbidden_paths):
            errors.append(f"managed path overlaps forbidden path: {path}")
        if not path.endswith("..."):
            warnings.append(f"managed path should usually end with ...: {path}")
    return SafetyResult(not errors, errors=tuple(errors), warnings=tuple(warnings), checks={"managed paths": "PASS" if not errors else "FAIL"})


def is_path_allowed(path: str, managed_paths: tuple[str, ...] | list[str]) -> bool:
    norm = _normalize_path(path)
    return any(_matches_p4_pattern(norm, _normalize_path(pattern)) for pattern in managed_paths)


def is_path_forbidden(path: str, forbidden_paths: tuple[str, ...] | list[str]) -> bool:
    norm = _normalize_path(path)
    return any(_matches_p4_pattern(norm, _normalize_path(pattern)) for pattern in forbidden_paths)


def validate_opened_files(files: list[P4FileChange], config: P4WorkspaceConfig, allow_delete: bool = False) -> SafetyResult:
    return _validate_files(files, config, allow_delete=allow_delete)


def validate_reconcile_preview(files: list[P4FileChange], config: P4WorkspaceConfig, allow_delete: bool = False) -> SafetyResult:
    return _validate_files(files, config, allow_delete=allow_delete)


def check_unity_meta_pairs(files: list[P4FileChange]) -> SafetyResult:
    warnings: list[str] = []
    changed = {_normalize_path(item.path): item for item in files}
    added_assets = [item for item in files if item.action == P4FileAction.ADD and Path(item.path).suffix.lower() in UNITY_ASSET_EXTENSIONS and not item.path.endswith(".meta")]
    for item in added_assets:
        meta = _normalize_path(item.path + ".meta")
        if meta not in changed and not _local_meta_exists(item.path):
            warnings.append(f"added Unity asset has no matching .meta in changes: {item.path}")
    for item in files:
        if item.path.endswith(".meta"):
            base = _normalize_path(item.path[:-5])
            if base not in changed:
                warnings.append(f".meta changed without its asset in the same file list: {item.path}")
    return SafetyResult(True, warnings=tuple(warnings), checks={"Unity .meta": "WARNING" if warnings else "PASS"})


def workspace_warnings(workspace: P4WorkspaceConfig) -> list[str]:
    root = workspace.root
    lowered = str(root).lower()
    warnings: list[str] = list(workspace.warnings)
    if root.drive.upper() == "C:":
        warnings.append("workspace root is on C:; prefer a dedicated work/data drive.")
    if lowered.startswith("\\\\"):
        warnings.append("workspace root appears to be a network path; P4 operations may be slow.")
    if any(part in lowered for part in RISKY_ROOT_PARTS):
        warnings.append("workspace root looks like Desktop/Downloads/OneDrive; this is risky for a P4 workspace.")
    return warnings


def has_large_or_binary_risk(text: str) -> bool:
    lowered = text.lower()
    risky_ext = (".psd", ".zip", ".7z", ".rar", ".mp4", ".mov", ".avi", ".bin", ".ckpt", ".safetensors", ".pth")
    return "models/" in lowered or "models\\" in lowered or any(ext in lowered for ext in risky_ext)


def _validate_files(files: list[P4FileChange], config: P4WorkspaceConfig, allow_delete: bool) -> SafetyResult:
    errors: list[str] = []
    warnings: list[str] = []
    for item in files:
        path = item.path
        if not is_path_allowed(path, config.managed_paths):
            errors.append(f"file is outside managed_paths: {path}")
        if is_path_forbidden(path, config.forbidden_paths):
            errors.append(f"file is under forbidden_paths: {path}")
        if item.action == P4FileAction.DELETE:
            message = f"delete action detected: {path}"
            if allow_delete:
                warnings.append(message)
            else:
                errors.append(message + " (pass --allow-delete to continue)")
    meta = check_unity_meta_pairs(files)
    warnings.extend(meta.warnings)
    checks = {
        "managed paths only": "PASS" if not any("outside managed_paths" in err for err in errors) else "FAIL",
        "forbidden paths": "PASS" if not any("forbidden_paths" in err for err in errors) else "FAIL",
        "delete files": "WARNING" if allow_delete and any(item.action == P4FileAction.DELETE for item in files) else ("FAIL" if any("delete action" in err for err in errors) else "PASS"),
        "Unity .meta": meta.checks.get("Unity .meta", "PASS"),
        "submit disabled": "PASS",
    }
    return SafetyResult(not errors, errors=tuple(errors), warnings=tuple(warnings), checks=checks)


def _matches_p4_pattern(path: str, pattern: str) -> bool:
    if pattern.endswith("/..."):
        prefix = pattern[:-4].rstrip("/")
        return path == prefix or path.startswith(prefix + "/")
    if pattern.endswith("..."):
        return path.startswith(pattern[:-3].rstrip("/"))
    return path == pattern or path.startswith(pattern.rstrip("/") + "/")


def _normalize_path(path: str) -> str:
    raw = str(path or "").replace("\\", "/").strip().strip('"')
    if raw.startswith("//"):
        parts = raw.split("/")
        if len(parts) > 4:
            raw = "/".join(parts[4:])
    raw = re.sub(r"^[A-Za-z]:/", "", raw)
    return raw.lstrip("/")


def _local_meta_exists(path: str) -> bool:
    try:
        return Path(path + ".meta").exists()
    except OSError:
        return False
