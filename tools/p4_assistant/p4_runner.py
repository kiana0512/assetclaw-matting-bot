from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from tools.p4_assistant.models import P4CommandResult, P4WorkspaceConfig
from tools.p4_assistant.safety import ensure_command_allowed, ensure_cwd_in_workspace, redact


class P4CommandError(RuntimeError):
    def __init__(self, result: P4CommandResult) -> None:
        super().__init__(result.safe_summary)
        self.result = result


class P4Runner:
    def __init__(self, workspace: P4WorkspaceConfig | None = None, p4_exe: str | None = None) -> None:
        self.workspace = workspace
        self.p4_exe = p4_exe or os.environ.get("P4_EXE") or _find_p4_exe()

    def is_available(self) -> bool:
        return bool(self.p4_exe)

    def for_workspace(self, workspace: P4WorkspaceConfig) -> "P4Runner":
        return P4Runner(workspace=workspace, p4_exe=self.p4_exe)

    def run(
        self,
        workspace_or_args: P4WorkspaceConfig | list[str],
        args: list[str] | None = None,
        cwd: Path | None = None,
        confirmation: bool = False,
        stdin_text: str | None = None,
        timeout_seconds: int = 120,
        check: bool = True,
    ) -> P4CommandResult:
        if args is None:
            if self.workspace is None:
                raise ValueError("workspace is required")
            workspace = self.workspace
            command_args = list(workspace_or_args)  # type: ignore[arg-type]
        else:
            workspace = workspace_or_args  # type: ignore[assignment]
            command_args = list(args)
        if not self.p4_exe:
            raise FileNotFoundError("p4 executable was not found in PATH. Install P4 CLI or add p4.exe to PATH.")
        ensure_command_allowed(command_args, confirmation=confirmation)
        run_cwd = cwd or workspace.root
        ensure_cwd_in_workspace(run_cwd, workspace)
        started = time.perf_counter()
        completed = subprocess.run(
            [self.p4_exe, *command_args],
            cwd=str(run_cwd),
            env=self._env_for(workspace),
            input=stdin_text,
            capture_output=True,
            text=True,
            shell=False,
            timeout=max(5, int(timeout_seconds or 120)),
        )
        duration = time.perf_counter() - started
        stdout = redact(completed.stdout or "")
        stderr = redact(completed.stderr or "")
        result = P4CommandResult(
            command=[Path(self.p4_exe).name, *command_args],
            cwd=str(run_cwd),
            returncode=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=duration,
            safe_summary=_safe_summary(command_args, completed.returncode, stdout, stderr),
        )
        if check and not result.ok:
            raise P4CommandError(result)
        return result

    def info(self) -> P4CommandResult:
        return self.run(["info"])

    def login_status(self) -> P4CommandResult:
        return self.run(["login", "-s"], check=False)

    def client_spec(self) -> P4CommandResult:
        if not self.workspace:
            raise ValueError("workspace is required")
        return self.run(["client", "-o", self.workspace.p4client])

    def workspace_where(self, paths: list[str] | None = None) -> P4CommandResult:
        return self.run(["where", *(paths or [])], check=False)

    def pending_changelists(self) -> P4CommandResult:
        return self.run(["changes", "-s", "pending", "-u", self.workspace.p4user, "-c", self.workspace.p4client], check=False)

    def shelved_changelists(self) -> P4CommandResult:
        return self.run(["changes", "-s", "shelved", "-u", self.workspace.p4user, "-c", self.workspace.p4client], check=False)

    def streams(self) -> P4CommandResult:
        return self.run(["streams"], check=False)

    def reconcile_preview(self, paths: list[str]) -> P4CommandResult:
        return self.run(["reconcile", "-n", *paths])

    def sync_preview(self, paths: list[str]) -> P4CommandResult:
        return self.run(["sync", "-n", *paths], timeout_seconds=300, check=False)

    def sync(self, paths: list[str]) -> P4CommandResult:
        return self.run(["sync", *paths], confirmation=True, timeout_seconds=1800)

    def switch_stream(self, stream: str) -> P4CommandResult:
        if not self.workspace:
            raise ValueError("workspace is required")
        return self.run(["client", "-s", "-S", stream, self.workspace.p4client], confirmation=True, timeout_seconds=300)

    def create_changelist(self, description: str) -> str:
        result = self.run(["change", "-i"], confirmation=True, stdin_text=_change_spec(description))
        match = re.search(r"Change\s+(\d+)\s+created", result.stdout + "\n" + result.stderr, re.IGNORECASE)
        if not match:
            raise RuntimeError(f"Could not parse changelist id from p4 change output: {result.safe_summary}")
        return match.group(1)

    def reconcile_to_changelist(self, cl: str | int, paths: list[str]) -> P4CommandResult:
        return self.run(["reconcile", "-c", str(cl), *paths], confirmation=True)

    def reopen_to_changelist(self, cl: str | int, paths: list[str]) -> P4CommandResult:
        return self.run(["reopen", "-c", str(cl), *paths], confirmation=True)

    def opened(self, cl: str | int | None = None) -> P4CommandResult:
        return self.run(["opened", "-c", str(cl)] if cl else ["opened"], check=False)

    def describe(self, cl: str | int, shelved: bool = False) -> P4CommandResult:
        args = ["describe"]
        if shelved:
            args.append("-S")
        args.append(str(cl))
        return self.run(args, check=False)

    def shelve(self, cl: str | int, force: bool = False) -> P4CommandResult:
        return self.run(["shelve", "-f", "-c", str(cl)] if force else ["shelve", "-c", str(cl)], confirmation=True, timeout_seconds=300)

    def _env_for(self, workspace: P4WorkspaceConfig) -> dict[str, str]:
        env = os.environ.copy()
        env["P4PORT"] = workspace.p4port
        env["P4USER"] = workspace.p4user
        env["P4CLIENT"] = workspace.p4client
        env.pop("P4PASSWD", None)
        env.pop("P4TICKETS", None)
        return env


def _change_spec(description: str) -> str:
    return f"Change: new\n\nStatus: new\n\nDescription:\n\t{description.strip().replace(chr(10), chr(10) + chr(9))}\n"


def _safe_summary(args: list[str], returncode: int, stdout: str, stderr: str) -> str:
    subcommand = args[0] if args else "p4"
    if _looks_like_login_error(stdout + "\n" + stderr):
        return "P4 authentication appears to be missing or expired. Please run p4 login manually; this tool will not ask for or store passwords."
    status = "ok" if returncode == 0 else f"failed ({returncode})"
    lines = [line for line in (stdout or stderr).splitlines() if line.strip()]
    preview = lines[0][:180] if lines else ""
    return f"p4 {subcommand} {status}" + (f": {preview}" if preview else "")


def _find_p4_exe() -> str | None:
    found = shutil.which("p4")
    if found:
        return found
    for candidate in (
        Path(r"C:\Program Files\Perforce\p4.exe"),
        Path(r"C:\Program Files (x86)\Perforce\p4.exe"),
        Path(r"C:\Program Files\Perforce\P4VResources\bin\p4.exe"),
    ):
        if candidate.exists():
            return str(candidate)
    return None


def _looks_like_login_error(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in ("please login", "not logged in", "ticket", "session has expired", "perforce password"))
