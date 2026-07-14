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
        ["reset", "--hard"],
        ["clean", "-fd"],
        ["checkout", "--force", "-B", "main", "origin/main"],
        ["reset", "--hard", "origin/main"],
        ["clean", "-fd"],
        ["lfs", "pull"],
    ]
    assert "git-lfs is not installed" in output


def test_pipeline_git_error_is_hidden_from_user() -> None:
    from assetclaw_matting.brain.result_formatter import format_skill_results

    text = format_skill_results(
        [
            {
                "ok": False,
                "skill": "direct_video.start",
                "error": "git checkout --force -B main origin/main failed:\nerror: ImageClip.json would be overwritten",
            }
        ]
    )

    assert "抠图管线更新失败：ImageClip.json" in text
    assert "git checkout" not in text
    assert "direct_video.start" not in text
