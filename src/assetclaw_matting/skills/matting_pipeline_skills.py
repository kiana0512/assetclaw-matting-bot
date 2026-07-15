from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from assetclaw_matting.config import settings

_PREFLIGHT_CACHE: dict[str, Any] = {}
_PREFLIGHT_CACHE_SECONDS = 15


def status(**_: Any) -> dict[str, Any]:
    repo = _repo_dir()
    commit = _git_commit(repo) if (repo / ".git").exists() else {}
    remote = _git_remote_state(repo) if (repo / ".git").exists() else {}
    assets = _asset_plan()
    links = [_link_status(item) for item in assets]
    cherry_html = Path(settings.cherry_postprocess_html_path)
    return {
        "ok": True,
        "repo_url": settings.matting_pipeline_repo_url,
        "repo_dir": str(repo),
        "repo_exists": repo.exists(),
        "branch": commit.get("branch", ""),
        "commit": commit.get("commit", ""),
        "commit_time": commit.get("commit_time", ""),
        "commit_subject": commit.get("subject", ""),
        "remote_commit": remote.get("remote_commit", ""),
        "ahead": remote.get("ahead"),
        "behind": remote.get("behind"),
        "up_to_date": remote.get("up_to_date"),
        "dirty": commit.get("dirty", False),
        "workflow_path": str(_workflow_target()),
        "workflow_exists": _workflow_target().exists(),
        "cherry_html_path": str(cherry_html),
        "cherry_html_exists": cherry_html.is_file(),
        "assets": links,
        "all_ready": all(item["source_exists"] and item["target_exists"] for item in links) and _workflow_target().exists() and cherry_html.is_file(),
    }


def verify(**_: Any) -> dict[str, Any]:
    payload = status()
    errors: list[str] = []
    for item in payload["assets"]:
        if not item["source_exists"]:
            errors.append(f"源文件缺失：{item['source']}")
        if not item["target_exists"]:
            errors.append(f"ComfyUI 目标缺失：{item['target']}")
        if item["source_exists"] and item["target_exists"]:
            if not _same_content(Path(item["source"]), Path(item["target"])):
                errors.append(f"ComfyUI 目标内容不是仓库最新版本：{item['target']}")
    workflow = _workflow_target()
    if workflow.exists():
        try:
            data = json.loads(workflow.read_text(encoding="utf-8"))
            payload["workflow_nodes"] = len(data) if isinstance(data, dict) else 0
        except Exception as exc:
            errors.append(f"工作流 JSON 无法解析：{exc}")
    else:
        errors.append(f"默认工作流缺失：{workflow}")
    cherry_html = Path(settings.cherry_postprocess_html_path)
    if not cherry_html.is_file():
        errors.append(f"Cherry 唯一算法 HTML 缺失：{cherry_html}")
    for item in payload["assets"]:
        if item["kind"] == "lora" and item["source_exists"]:
            source = Path(item["source"])
            if source.exists() and source.stat().st_size < 1_000_000:
                errors.append(f"Lora 文件过小，疑似 Git LFS pointer 未拉取：{source}")
    payload["ok"] = not errors
    payload["errors"] = errors
    return payload


def update(force_copy: bool = False, **_: Any) -> dict[str, Any]:
    repo = _repo_dir()
    git_output = _sync_repo(repo)
    synced = []
    for item in _asset_plan():
        source = _first_existing(item["sources"])
        if not source:
            raise FileNotFoundError(f"matting pipeline source not found: {item['name']}")
        target = item["target"]
        _sync_asset(source, target, force_copy=force_copy)
        synced.append({**item, "source": str(source), "target": str(target), "mode": _target_mode(target)})
    checked = verify()
    return {
        "ok": checked.get("ok", False),
        "repo_url": settings.matting_pipeline_repo_url,
        "repo_dir": str(repo),
        "git_output": git_output[-4000:],
        "branch": checked.get("branch", ""),
        "commit": checked.get("commit", ""),
        "commit_time": checked.get("commit_time", ""),
        "commit_subject": checked.get("commit_subject", ""),
        "remote_commit": checked.get("remote_commit", ""),
        "ahead": checked.get("ahead"),
        "behind": checked.get("behind"),
        "up_to_date": checked.get("up_to_date"),
        "workflow_path": str(_workflow_target()),
        "workflow_exists": checked.get("workflow_exists", False),
        "workflow_nodes": checked.get("workflow_nodes"),
        "synced": synced,
        "verify_errors": checked.get("errors", []),
        "needs_comfyui_restart": True,
    }


def ensure_latest_for_task(force_copy: bool = False, **_: Any) -> dict[str, Any]:
    cached = _cached_preflight()
    if cached:
        return cached
    from assetclaw_matting.progress import notify_progress

    notify_progress("正在更新抠图管线")
    repo = _repo_dir()
    before = status()
    git_output = _sync_repo(repo)
    after_pull = status()
    needs_sync = _needs_asset_sync(after_pull)
    queue = _comfyui_queue_activity()
    synced = []
    if needs_sync:
        for item in _asset_plan():
            source = _first_existing(item["sources"])
            if not source:
                raise FileNotFoundError(f"matting pipeline source not found: {item['name']}")
            target = item["target"]
            _sync_asset(source, target, force_copy=force_copy)
            synced.append({**item, "source": str(source), "target": str(target), "mode": _target_mode(target)})
    checked = verify()
    if not checked.get("ok"):
        result = {**checked, "git_output": git_output[-4000:], "synced": synced, "needs_sync": needs_sync}
        _remember_preflight(result)
        return result
    changed = bool(synced) or before.get("commit") != checked.get("commit")
    result = {
        "ok": True,
        "repo_url": settings.matting_pipeline_repo_url,
        "repo_dir": str(repo),
        "git_output": git_output[-4000:],
        "branch": checked.get("branch", ""),
        "commit": checked.get("commit", ""),
        "commit_time": checked.get("commit_time", ""),
        "commit_subject": checked.get("commit_subject", ""),
        "remote_commit": checked.get("remote_commit", ""),
        "ahead": checked.get("ahead"),
        "behind": checked.get("behind"),
        "up_to_date": checked.get("up_to_date"),
        "workflow_path": checked.get("workflow_path", ""),
        "workflow_exists": checked.get("workflow_exists", False),
        "workflow_nodes": checked.get("workflow_nodes"),
        "synced": synced,
        "assets": checked.get("assets", []),
        "needs_sync": needs_sync,
        "updated": changed,
        "queue": queue,
        "message": _task_notice(checked, changed),
    }
    _remember_preflight(result)
    return result


def _cached_preflight() -> dict[str, Any] | None:
    if not _PREFLIGHT_CACHE:
        return None
    if time.time() - float(_PREFLIGHT_CACHE.get("ts") or 0) > _PREFLIGHT_CACHE_SECONDS:
        return None
    value = dict(_PREFLIGHT_CACHE.get("value") or {})
    if value.get("ok") and value.get("message"):
        value["message"] = str(value["message"]).replace("已自动更新到", "已确认 Git 最新版本")
    return value


def _remember_preflight(result: dict[str, Any]) -> None:
    _PREFLIGHT_CACHE["ts"] = time.time()
    _PREFLIGHT_CACHE["value"] = dict(result)


def _sync_repo(repo: Path) -> str:
    if not repo.exists():
        repo.parent.mkdir(parents=True, exist_ok=True)
        return _git(["clone", "--branch", settings.matting_pipeline_branch, settings.matting_pipeline_repo_url, str(repo)], cwd=repo.parent)
    if not (repo / ".git").exists():
        raise RuntimeError(f"matting pipeline repo_dir exists but is not a git repo: {repo}")
    output = []
    branch = settings.matting_pipeline_branch
    remote_ref = f"origin/{branch}"
    output.append(_git(["fetch", "--prune", "origin"], cwd=repo))
    output.append(_git(["reset", "--hard"], cwd=repo))
    output.append(_git(["clean", "-fd"], cwd=repo))
    output.append(_git(["checkout", "--force", "-B", branch, remote_ref], cwd=repo))
    output.append(_git(["reset", "--hard", remote_ref], cwd=repo))
    output.append(_git(["clean", "-fd"], cwd=repo))
    output.append(_git_lfs_pull(repo))
    return "\n".join(output)


def _git_lfs_pull(repo: Path) -> str:
    try:
        return _git(["lfs", "pull"], cwd=repo)
    except RuntimeError as exc:
        text = str(exc)
        if "git: 'lfs' is not a git command" in text or "git-lfs" in text.lower():
            return "git lfs pull skipped: git-lfs is not installed"
        raise


def _needs_asset_sync(payload: dict[str, Any]) -> bool:
    if payload.get("up_to_date") is False:
        return True
    for item in payload.get("assets") or []:
        if not item.get("source_exists") or not item.get("target_exists"):
            return True
        if not item.get("same_as_source"):
            return True
    return False


def _task_notice(payload: dict[str, Any], changed: bool) -> str:
    commit = str(payload.get("commit") or "")[:12] or "-"
    when = payload.get("commit_time") or "-"
    if changed:
        return f"抠图管线已同步最新版本 {commit}（{when}）。"
    return f"抠图管线已确认最新版本 {commit}（{when}）。"


def _comfyui_queue_activity() -> dict[str, Any]:
    if settings.comfyui_fake_mode:
        return {"ok": True, "active": False, "running": 0, "pending": 0, "fake_mode": True}
    try:
        from assetclaw_matting.comfyui.client import comfyui_client

        queue = comfyui_client.get_queue()
        running = len(queue.get("queue_running") or queue.get("running") or [])
        pending = len(queue.get("queue_pending") or queue.get("pending") or [])
        return {"ok": True, "active": running + pending > 0, "running": running, "pending": pending}
    except Exception as exc:
        return {"ok": False, "active": False, "running": 0, "pending": 0, "error": str(exc)}


def _git(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{proc.stdout}")
    return proc.stdout.strip()


def _git_commit(repo: Path) -> dict[str, Any]:
    def run(args: list[str]) -> str:
        return _git(args, cwd=repo).strip()

    try:
        return {
            "branch": run(["rev-parse", "--abbrev-ref", "HEAD"]),
            "commit": run(["rev-parse", "--short=12", "HEAD"]),
            "commit_time": run(["show", "-s", "--format=%ci", "HEAD"]),
            "subject": run(["show", "-s", "--format=%s", "HEAD"]),
            "dirty": bool(run(["status", "--porcelain"])),
        }
    except Exception:
        return {}


def _git_remote_state(repo: Path) -> dict[str, Any]:
    try:
        head = _git(["rev-parse", "HEAD"], cwd=repo).strip()
        branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo).strip()
        remote_ref = f"origin/{branch}"
        remote = _git(["rev-parse", remote_ref], cwd=repo).strip()
        counts = _git(["rev-list", "--left-right", "--count", f"HEAD...{remote_ref}"], cwd=repo).split()
        ahead = int(counts[0]) if counts else 0
        behind = int(counts[1]) if len(counts) > 1 else 0
        return {
            "remote_commit": remote,
            "ahead": ahead,
            "behind": behind,
            "up_to_date": head == remote and ahead == 0 and behind == 0,
        }
    except Exception:
        return {}


def _sync_asset(source: Path, target: Path, force_copy: bool = False) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if _points_to(target, source):
        return
    if target.exists() and _same_content(source, target):
        return
    if target.exists() or target.is_symlink():
        _backup_existing(target)
    if not force_copy:
        try:
            os.symlink(source, target, target_is_directory=source.is_dir())
            return
        except OSError:
            pass
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)


def _backup_existing(target: Path) -> None:
    backup_root = Path(settings.storage_dir) / "matting_pipeline_backups" / datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_root.mkdir(parents=True, exist_ok=True)
    backup = backup_root / target.name
    shutil.move(str(target), str(backup))


def _points_to(target: Path, source: Path) -> bool:
    if not target.exists() and not target.is_symlink():
        return False
    try:
        if target.is_symlink():
            return target.resolve() == source.resolve()
        return target.resolve() == source.resolve()
    except OSError:
        return False


def _same_content(source: Path, target: Path) -> bool:
    if not source.exists() or not target.exists():
        return False
    if source.is_dir() != target.is_dir():
        return False
    if source.is_file():
        return source.stat().st_size == target.stat().st_size and _sha256(source) == _sha256(target)
    source_files = sorted(path for path in source.rglob("*") if path.is_file())
    target_files = sorted(path for path in target.rglob("*") if path.is_file())
    source_rel = [path.relative_to(source) for path in source_files]
    target_rel = [path.relative_to(target) for path in target_files]
    if source_rel != target_rel:
        return False
    return all(_sha256(source / rel) == _sha256(target / rel) for rel in source_rel)


def _same_content_fast(source: Path, target: Path) -> bool:
    if not source.exists() or not target.exists():
        return False
    if source.is_dir() != target.is_dir():
        return False
    if source.is_file():
        return source.stat().st_size == target.stat().st_size and int(source.stat().st_mtime) == int(target.stat().st_mtime)
    source_files = sorted(path for path in source.rglob("*") if path.is_file())
    target_files = sorted(path for path in target.rglob("*") if path.is_file())
    source_rel = [path.relative_to(source) for path in source_files]
    target_rel = [path.relative_to(target) for path in target_files]
    if source_rel != target_rel:
        return False
    for rel in source_rel:
        src = source / rel
        dst = target / rel
        if src.stat().st_size != dst.stat().st_size or int(src.stat().st_mtime) != int(dst.stat().st_mtime):
            return False
    return True


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _asset_plan() -> list[dict[str, Any]]:
    repo = _repo_dir()
    comfy = Path(settings.comfyui_dir)
    configured_lora_sources = [repo / "loras" / settings.matting_pipeline_lora_name, repo / settings.matting_pipeline_lora_name]
    items = [
        {
            "name": settings.matting_pipeline_workflow_name,
            "kind": "workflow",
            "sources": [repo / "workflows" / settings.matting_pipeline_workflow_name, repo / settings.matting_pipeline_workflow_name],
            "target": comfy / "user" / "default" / "workflows" / settings.matting_pipeline_workflow_name,
        },
        {
            "name": settings.matting_pipeline_lora_name,
            "kind": "lora",
            "sources": configured_lora_sources,
            "target": comfy / "models" / "loras" / settings.matting_pipeline_lora_name,
        },
        {
            "name": settings.matting_pipeline_custom_node_name,
            "kind": "custom_node",
            "sources": [repo / "custom_nodes" / settings.matting_pipeline_custom_node_name, repo / settings.matting_pipeline_custom_node_name],
            "target": comfy / "custom_nodes" / settings.matting_pipeline_custom_node_name,
        },
    ]
    for lora_name in _workflow_lora_names():
        if lora_name == settings.matting_pipeline_lora_name:
            continue
        items.append(
            {
                "name": lora_name,
                "kind": "lora_alias",
                "sources": configured_lora_sources,
                "target": comfy / "models" / "loras" / lora_name,
            }
        )
    return items


def _workflow_lora_names() -> list[str]:
    workflow = _repo_dir() / settings.matting_pipeline_workflow_name
    if not workflow.exists():
        workflow = _repo_dir() / "workflows" / settings.matting_pipeline_workflow_name
    if not workflow.exists():
        return []
    try:
        data = json.loads(workflow.read_text(encoding="utf-8"))
    except Exception:
        return []
    names: list[str] = []
    nodes = data.get("nodes") if isinstance(data, dict) else None
    if not isinstance(nodes, list):
        nodes = list(data.values()) if isinstance(data, dict) else []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("type") or node.get("class_type") or "").lower()
        if "lora" not in node_type:
            continue
        widgets = node.get("widgets_values") or []
        if isinstance(widgets, list):
            for value in widgets:
                text = str(value)
                if text.lower().endswith((".safetensors", ".ckpt", ".pt")) and text not in names:
                    names.append(text)
                    break
        inputs = node.get("inputs") or {}
        if isinstance(inputs, dict):
            value = inputs.get("lora_name")
            if isinstance(value, str) and value not in names:
                names.append(value)
    return names


def _link_status(item: dict[str, Any]) -> dict[str, Any]:
    source = _first_existing(item["sources"])
    target = item["target"]
    return {
        "name": item["name"],
        "kind": item["kind"],
        "source": str(source or item["sources"][0]),
        "target": str(target),
        "source_exists": bool(source and source.exists()),
        "target_exists": target.exists(),
        "target_mode": _target_mode(target),
        "linked_to_source": bool(source and _points_to(target, source)),
        "same_as_source": bool(source and (_points_to(target, source) or _same_content_fast(source, target))),
    }


def _target_mode(target: Path) -> str:
    if target.is_symlink():
        return "symlink"
    if target.exists():
        return "copy"
    return "missing"


def _first_existing(paths: list[Path]) -> Path | None:
    return next((path for path in paths if path.exists()), None)


def _repo_dir() -> Path:
    return Path(settings.matting_pipeline_repo_dir)


def _workflow_target() -> Path:
    return Path(settings.comfyui_dir) / "user" / "default" / "workflows" / settings.matting_pipeline_workflow_name
