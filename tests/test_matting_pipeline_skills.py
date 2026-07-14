from __future__ import annotations

from pathlib import Path


def test_sync_repo_overwrites_local_changes(monkeypatch, tmp_path: Path) -> None:
    from assetclaw_matting.config import settings
    from assetclaw_matting.skills import matting_pipeline_skills

    repo = tmp_path / "imageclip"
    (repo / ".git").mkdir(parents=True)
    calls: list[list[str]] = []

    monkeypatch.setattr(settings, "matting_pipeline_branch", "main")

    def fake_git(args: list[str], cwd: Path) -> str:
        calls.append(args)
        if args == ["lfs", "pull"]:
            raise RuntimeError("git lfs pull failed:\ngit: 'lfs' is not a git command")
        return "ok"

    monkeypatch.setattr(matting_pipeline_skills, "_git", fake_git)

    output = matting_pipeline_skills._sync_repo(repo)

    assert calls == [
        ["fetch", "--prune", "origin"],
        ["checkout", "-B", "main", "origin/main"],
        ["reset", "--hard", "origin/main"],
        ["clean", "-fd"],
        ["lfs", "pull"],
    ]
    assert "git-lfs is not installed" in output
