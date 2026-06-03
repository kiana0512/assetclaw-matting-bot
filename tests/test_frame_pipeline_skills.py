from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from assetclaw_matting.brain.local_command_brain import LocalCommandBrain
from assetclaw_matting.brain.result_formatter import format_skill_results
from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import init_db
from assetclaw_matting.skills.frame_skills import default_automation_paths, info as frame_info, run_preview as frame_preview
from assetclaw_matting.skills.pipeline_skills import run_preview as pipeline_preview
from assetclaw_matting.skills.registry import get_skill_meta


def setup_module() -> None:
    init_db(Path("E:/assetclaw-matting-bot/data/test_assetclaw.db"))
    create_tables()


def test_frame_tool_info_and_preview_are_safe() -> None:
    info = frame_info()
    assert info["exists"] is True
    assert info["fps"] == 24

    preview = frame_preview(download_dir="E:\\raw_videos", export_dir="E:\\output_frames", fps=24, diff_threshold=0.2)
    assert preview["export_dir"] == "E:\\output_frames"
    assert preview["diff_threshold"] == 0.2

    text = format_skill_results([{"ok": True, "skill": "frame.run_preview", "result": preview}])
    assert "抽帧任务预览" in text


def test_pipeline_preview_registry_and_router() -> None:
    preview = pipeline_preview(
        input_dir="E:\\raw_videos",
        frame_output_dir="E:\\output_frames",
        matte_output_dir="E:\\output_matting",
        smooth_output_dir="E:\\output_smooth",
    )
    assert preview["steps"][0].startswith("1.")
    assert get_skill_meta("pipeline.run_start")["requires_confirmation"] is True
    assert get_skill_meta("frame.run_start")["requires_confirmation"] is True

    brain = LocalCommandBrain()
    assert brain._infer_tool_calls("开始飞书抽帧，下载到 E:\\raw_videos，抽帧输出 E:\\output_frames")[0].skill == "frame.run_start"
    assert brain._infer_tool_calls("执行动画自动化流程 E:\\raw_videos E:\\output_frames E:\\output_matting E:\\output_smooth")[0].skill == "pipeline.run_start"

    text = format_skill_results([{"ok": True, "skill": "pipeline.run_preview", "result": preview}])
    assert "抽帧 -> ComfyUI 抠图 -> Cherry 平滑" in text


def test_default_animation_workspace_paths_are_on_e_drive() -> None:
    defaults = default_automation_paths("2026-06-02")
    assert defaults["workspace_root"] == "E:\\animation_automation\\2026-06-02"
    assert defaults["video_dir"].endswith("\\videos")
    assert defaults["frame_dir"].endswith("\\frames")
    assert defaults["matte_dir"].endswith("\\matte")
    assert defaults["smooth_dir"].endswith("\\smooth")

    preview = pipeline_preview()
    assert preview["workspace_root"].startswith("E:\\animation_automation\\")
    assert preview["input_dir"].endswith("\\videos")
    assert preview["frame_output_dir"].endswith("\\frames")
    assert preview["matte_output_dir"].endswith("\\matte")
    assert preview["smooth_output_dir"].endswith("\\smooth")


def test_frame_workflow_builds_role_emotion_identity() -> None:
    tool_dir = Path("E:/assetclaw-matting-bot/feishu_frame_tool")
    if str(tool_dir) not in sys.path:
        sys.path.insert(0, str(tool_dir))
    sys.modules.setdefault("extractor", types.SimpleNamespace(LocalFrameExtractor=object))
    sys.modules.setdefault("dedup", types.SimpleNamespace(dedup_folder=lambda *args, **kwargs: None))
    from workflow import Workflow

    workflow = Workflow.__new__(Workflow)
    workflow.f_role = "角色"
    workflow.f_parent = "父記錄"
    workflow.f_animation_name = "动画名"
    workflow.rec_map = {
        "rec_role": {"fields": {"角色": "gary"}},
        "rec_idle": {
            "fields": {
                "角色": "idle",
                "动画名": "待机",
                "父記錄": [{"record_ids": ["rec_role"], "text": "gary"}],
            }
        },
    }

    identity = workflow._record_identity("rec_idle", workflow.rec_map["rec_idle"]["fields"])

    assert identity["role"] == "gary"
    assert identity["emotion"] == "idle"
    assert identity["rel_dir"] == "gary\\idle"


def test_frame_workflow_processes_video_records_without_progress_filter(tmp_path) -> None:
    tool_dir = Path("E:/assetclaw-matting-bot/feishu_frame_tool")
    if str(tool_dir) not in sys.path:
        sys.path.insert(0, str(tool_dir))

    class FakeExtractor:
        def __init__(self, export_dir: str, fps: int, max_frames: int, logger):
            self.export_dir = Path(export_dir)

        def process_video(self, video_path: str, out_subdir: str) -> str:
            dst = self.export_dir / out_subdir
            dst.mkdir(parents=True, exist_ok=True)
            (dst / "0001.png").write_bytes(b"png")
            return str(dst)

    class FakeClient:
        def __init__(self) -> None:
            self.records = [
                {"record_id": "rec_role", "fields": {"角色": "gary"}},
                {
                    "record_id": "rec_idle",
                    "fields": {
                        "角色": "idle",
                        "父記錄": [{"record_ids": ["rec_role"], "text": "gary"}],
                        "进度": "",
                        "动画": [{"name": "source.mp4", "type": "video/mp4"}],
                    },
                },
            ]

        def list_records(self):
            return self.records

        def download_attachment(self, attachment, dest_dir, field_name="", record_id="", save_name=""):
            dst = Path(dest_dir) / save_name
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(b"video")
            return str(dst)

    fake_client = FakeClient()
    fake_feishu = types.SimpleNamespace(FeishuClient=types.SimpleNamespace(from_feishu_config=lambda *args, **kwargs: fake_client))
    fake_extractor = types.SimpleNamespace(LocalFrameExtractor=FakeExtractor)
    fake_dedup = types.SimpleNamespace(dedup_folder=lambda *args, **kwargs: None)
    old_modules = {name: sys.modules.get(name) for name in ("feishu_client", "extractor", "dedup", "workflow")}
    sys.modules["feishu_client"] = fake_feishu
    sys.modules["extractor"] = fake_extractor
    sys.modules["dedup"] = fake_dedup
    sys.modules.pop("workflow", None)
    try:
        from workflow import Workflow

        workflow = Workflow(
            {
                "feishu": {},
                "fields": {"animation": "动画", "role": "角色", "parent": "父記錄"},
                "paths": {"download_dir": str(tmp_path / "videos"), "export_dir": str(tmp_path / "frames")},
                "dedup": {"enabled": False},
                "framepacker": {"fps": 24, "max_frames": 24},
            }
        )
        workflow.run()
    finally:
        for name, module in old_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    assert (tmp_path / "videos" / "gary" / "idle" / "gary_idle.mp4").exists()
    assert (tmp_path / "frames" / "gary" / "idle" / "0001.png").exists()
    assert (tmp_path / "frames" / "_pipeline_manifest.json").exists()


def test_frame_workflow_refuses_video_record_without_character_emotion() -> None:
    tool_dir = Path("E:/assetclaw-matting-bot/feishu_frame_tool")
    if str(tool_dir) not in sys.path:
        sys.path.insert(0, str(tool_dir))
    sys.modules.setdefault("extractor", types.SimpleNamespace(LocalFrameExtractor=object))
    sys.modules.setdefault("dedup", types.SimpleNamespace(dedup_folder=lambda *args, **kwargs: None))
    sys.modules.pop("workflow", None)
    from workflow import Workflow

    workflow = Workflow.__new__(Workflow)
    workflow.f_role = "角色"
    workflow.f_parent = "父記錄"
    workflow.f_animation_name = "动画名"
    workflow.rec_map = {"rec_lonely": {"fields": {"角色": "idle"}}}

    with pytest.raises(ValueError, match="Cannot resolve character/emotion"):
        workflow._record_identity("rec_lonely", workflow.rec_map["rec_lonely"]["fields"])
