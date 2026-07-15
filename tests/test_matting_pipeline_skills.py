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


def test_pipeline_status_formatter_is_concise_for_humans() -> None:
    from assetclaw_matting.brain.result_formatter import format_skill_results

    text = format_skill_results(
        [
            {
                "ok": True,
                "skill": "matting_pipeline.status",
                "result": {
                    "ok": True,
                    "repo_dir": "E:/imageclip-pipeline/imageclip",
                    "repo_url": "git@gitlab.lilithgame.com:rd_center/ai_art/imageclip.git",
                    "branch": "main",
                    "commit": "98a67a59c328",
                    "commit_time": "2026-07-08 14:51:05 +0800",
                    "commit_subject": "后处理html - 更新阴影生成的参数",
                    "up_to_date": True,
                    "workflow_path": "C:/Users/lilithgames/Downloads/ComfyUI-aki-v3/ComfyUI/user/default/workflows/ImageClip.json",
                    "workflow_exists": True,
                    "all_ready": True,
                    "assets": [
                        {
                            "name": "ImageClip.json",
                            "kind": "workflow",
                            "source": "E:/imageclip-pipeline/imageclip/ImageClip.json",
                            "target": "C:/Users/lilithgames/Downloads/ComfyUI-aki-v3/ComfyUI/user/default/workflows/ImageClip.json",
                            "source_exists": True,
                            "target_exists": True,
                            "target_mode": "copy",
                        }
                    ],
                },
            }
        ]
    )

    assert "抠图管线：ImageClip，已是最新版本。" in text
    assert "版本：main / 98a67a59c328" in text
    assert "资源：工作流、Lora、Cherry 节点都已就绪，可以使用。" in text
    assert "E:/imageclip-pipeline" not in text
    assert "git@gitlab" not in text
    assert "资源同步" not in text
    assert "copy" not in text
