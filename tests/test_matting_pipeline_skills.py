from __future__ import annotations

from pathlib import Path

from PIL import Image


def test_sync_repo_overwrites_local_changes(monkeypatch, tmp_path: Path) -> None:
    from assetclaw_matting.config import settings
    from assetclaw_matting.skills import matting_pipeline_skills

    repo = tmp_path / "imageclip"
    (repo / ".git").mkdir(parents=True)
    calls: list[list[str]] = []

    monkeypatch.setattr(settings, "matting_pipeline_branch", "main")

    def fake_git(args: list[str], cwd: Path) -> str:
        calls.append(args)
        if args == ["rev-parse", "HEAD"]:
            return "local"
        if args == ["rev-parse", "origin/main"]:
            return "remote"
        if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return "main"
        if args == ["status", "--porcelain"]:
            return " M ImageClip.json"
        if args == ["lfs", "pull"]:
            raise RuntimeError("git lfs pull failed:\ngit: 'lfs' is not a git command")
        return "ok"

    monkeypatch.setattr(matting_pipeline_skills, "_git", fake_git)

    output = matting_pipeline_skills._sync_repo(repo)

    assert calls == [
        ["fetch", "--prune", "origin"],
        ["rev-parse", "HEAD"],
        ["rev-parse", "origin/main"],
        ["rev-parse", "--abbrev-ref", "HEAD"],
        ["status", "--porcelain"],
        ["reset", "--hard"],
        ["clean", "-fd"],
        ["checkout", "--force", "-B", "main", "origin/main"],
        ["reset", "--hard", "origin/main"],
        ["clean", "-fd"],
        ["lfs", "pull"],
    ]
    assert "git-lfs is not installed" in output


def test_sync_repo_skips_reset_when_checkout_is_already_current(monkeypatch, tmp_path: Path) -> None:
    from assetclaw_matting.config import settings
    from assetclaw_matting.skills import matting_pipeline_skills

    repo = tmp_path / "imageclip"
    (repo / ".git").mkdir(parents=True)
    calls: list[list[str]] = []
    monkeypatch.setattr(settings, "matting_pipeline_branch", "main")

    def fake_git(args: list[str], cwd: Path) -> str:
        calls.append(args)
        if args in (["rev-parse", "HEAD"], ["rev-parse", "origin/main"]):
            return "same-commit"
        if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return "main"
        if args == ["status", "--porcelain"]:
            return ""
        return "fetched"

    monkeypatch.setattr(matting_pipeline_skills, "_git", fake_git)

    output = matting_pipeline_skills._sync_repo(repo)

    assert calls == [
        ["fetch", "--prune", "origin"],
        ["rev-parse", "HEAD"],
        ["rev-parse", "origin/main"],
        ["rev-parse", "--abbrev-ref", "HEAD"],
        ["status", "--porcelain"],
    ]
    assert "already up to date: same-commit" in output


def test_preflight_cache_cannot_skip_a_later_task_check(monkeypatch) -> None:
    from assetclaw_matting.skills import matting_pipeline_skills

    monkeypatch.setattr(matting_pipeline_skills, "_PREFLIGHT_CACHE", {})
    matting_pipeline_skills._remember_preflight({"ok": True, "message": "checked"})
    checked_at = float(matting_pipeline_skills._PREFLIGHT_CACHE["ts"])

    assert matting_pipeline_skills._cached_preflight(not_before=checked_at) is not None
    assert matting_pipeline_skills._cached_preflight(not_before=checked_at + 0.001) is None


def test_cherry_task_preflight_updates_only_local_postprocess_assets(monkeypatch, tmp_path: Path) -> None:
    from assetclaw_matting.config import settings
    from assetclaw_matting.skills import matting_pipeline_skills

    repo = tmp_path / "imageclip"
    (repo / ".git").mkdir(parents=True)
    html = repo / "cherry-postprocess.html"
    html.write_text("verified cherry", encoding="utf-8")
    full = repo / "CharactorFull"
    emoji = repo / "CharactorEmoji"
    full.mkdir()
    emoji.mkdir()
    Image.new("RGBA", (384, 512), (255, 0, 0, 255)).save(full / "jennifer.png")
    Image.new("RGBA", (256, 256), (255, 0, 0, 255)).save(emoji / "jennifer.png")

    monkeypatch.setattr(settings, "matting_pipeline_repo_dir", repo)
    monkeypatch.setattr(settings, "cherry_postprocess_html_path", html)
    monkeypatch.setattr(settings, "cherry_character_full_reference_dir", full)
    monkeypatch.setattr(settings, "cherry_character_emoji_reference_dir", emoji)
    monkeypatch.setattr(matting_pipeline_skills, "_sync_repo", lambda _repo: "fetched origin/main")
    monkeypatch.setattr(
        matting_pipeline_skills,
        "_git_commit",
        lambda _repo: {
            "branch": "main",
            "commit": "latest-html-commit",
            "commit_time": "2026-08-13",
            "subject": "update cherry and references",
        },
    )
    monkeypatch.setattr(
        matting_pipeline_skills,
        "_verify_cherry_release",
        lambda: {"ok": True, "promoted": True, "sha256": "abc", "path": str(html)},
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Cherry-only preflight must not inspect or sync local ComfyUI")

    monkeypatch.setattr(matting_pipeline_skills, "_asset_plan", forbidden)
    monkeypatch.setattr(matting_pipeline_skills, "verify", forbidden)
    monkeypatch.setattr(matting_pipeline_skills, "_comfyui_queue_activity", forbidden)

    result = matting_pipeline_skills._ensure_latest_cherry_for_task_locked()

    assert result["ok"] is True
    assert result["commit"] == "latest-html-commit"
    assert result["character_assets"]["full_count"] == 1
    assert result["character_assets"]["emoji_count"] == 1
    assert "workflow_path" not in result
    assert "assets" not in result


def test_runtime_node_check_ignores_frontend_and_disabled_nodes(monkeypatch) -> None:
    from assetclaw_matting.comfyui.client import comfyui_client
    from assetclaw_matting.skills import matting_pipeline_skills

    workflow = {
        "nodes": [
            {"id": 1, "type": "LoadImage", "mode": 0},
            {"id": 2, "type": "Reroute", "mode": 0},
            {"id": 3, "type": "MissingButBypassed", "mode": 4},
            {"id": 4, "type": "MissingActiveNode", "mode": 0},
        ]
    }
    monkeypatch.setattr(comfyui_client, "get_object_info", lambda: {"LoadImage": {}})

    checked = matting_pipeline_skills._verify_workflow_runtime(workflow)

    assert checked["reachable"] is True
    assert checked["missing_node_types"] == ["MissingActiveNode"]


def test_runtime_node_check_reports_unreachable_registry(monkeypatch) -> None:
    from assetclaw_matting.comfyui.client import comfyui_client
    from assetclaw_matting.skills import matting_pipeline_skills

    def fail() -> dict:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(comfyui_client, "get_object_info", fail)

    checked = matting_pipeline_skills._verify_workflow_runtime({"1": {"class_type": "LoadImage"}})

    assert checked["reachable"] is False
    assert checked["missing_node_types"] == []
    assert "connection refused" in checked["error"]


def test_preflight_error_prefers_specific_runtime_errors() -> None:
    from assetclaw_matting.skills import matting_pipeline_skills

    message = matting_pipeline_skills.preflight_error(
        {"ok": False, "errors": ["工作流包含秋叶未注册的节点：MissingNode"]}
    )

    assert message == "工作流包含秋叶未注册的节点：MissingNode"
    assert "preflight" not in message


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
