from __future__ import annotations

from assetclaw_matting.brain.direct_video_planner import plan_direct_video_task
from assetclaw_matting.brain.pre_llm_router import _plan_task_overview
from assetclaw_matting.brain.result_formatter import format_skill_results
from assetclaw_matting.brain.schemas import BrainMessage
from assetclaw_matting.feishu.processor import _is_progress_query
from assetclaw_matting.skills.agent_ops_skills import task_overview
from assetclaw_matting.skills.registry import get_skill_meta


def _task_call(text: str):
    planned = _plan_task_overview(BrainMessage(text=text))
    assert planned is not None
    calls, reason = planned
    assert reason == "parent task overview route"
    assert len(calls) == 1
    assert calls[0].skill == "agent.task_overview"
    return calls[0]


def test_generic_task_questions_route_to_all_parent_sources() -> None:
    for text in ("现在任务进度", "现在任务进度有哪些"):
        call = _task_call(text)
        assert call.arguments == {
            "scope": "all",
            "view": "active",
            "detail": False,
        }

    call = _task_call("全部的任务列表呢")
    assert call.arguments == {
        "scope": "all",
        "view": "list",
        "detail": False,
    }


def test_explicit_parent_source_questions_keep_their_scope() -> None:
    cases = {
        "直传图片任务呢": "image",
        "图片直接发的任务呢": "image",
        "直传视频任务呢": "video",
        "视频直接发的任务呢": "video",
        "飞书动画任务呢": "animation_flow",
        "飞书视频文件下载处理到哪了": "animation_flow",
    }
    for text, media_type in cases.items():
        call = _task_call(text)
        assert call.arguments["scope"] == media_type
        assert call.arguments["view"] == "active"


def test_natural_paraphrases_select_state_views() -> None:
    cases = {
        "机器现在都在跑什么": ("all", "active"),
        "运行中的任务有哪些": ("all", "running"),
        "队列里有什么": ("all", "queue"),
        "还有几个任务在排队": ("all", "queue"),
        "已经完成了哪些任务": ("all", "completed"),
        "图片那批怎么样了": ("image", "active"),
        "视频那批到哪了": ("video", "active"),
        "表格那批处理情况": ("animation_flow", "active"),
    }
    for text, (scope, view) in cases.items():
        call = _task_call(text)
        assert call.arguments["scope"] == scope
        assert call.arguments["view"] == view


def test_parent_task_id_becomes_query() -> None:
    call = _task_call("帮我看下 img_ab12cd34 的详细进度")
    assert call.arguments == {
        "scope": "all",
        "view": "active",
        "query": "IMG_AB12CD34",
        "detail": True,
    }


def test_component_and_six_video_batch_queries_keep_existing_routes() -> None:
    assert _plan_task_overview(BrainMessage(text="ComfyUI 任务进度")) is None
    assert _plan_task_overview(BrainMessage(text="这批六个任务进度列表")) is None
    calls, _reason = plan_direct_video_task(BrainMessage(text="这批六个任务进度列表"))
    assert calls and calls[0].skill == "direct_video.list"


def test_explicit_image_reply_never_falls_back_to_comfy_child() -> None:
    payload = {
        "filters": {
            "task_view": "active",
            "media_type": "image",
            "include_finished": False,
        },
        "direct_images": [],
        "direct_videos": [],
        "animation_flows": [],
        "active": [
            {
                "run_id": "COMFY_UNRELATED",
                "status": "RUNNING",
                "done": 9,
                "total": 36,
            }
        ],
    }
    text = format_skill_results([{"ok": True, "skill": "agent.task_overview", "result": payload}])

    assert "图片直发：0 个" in text
    assert "当前没有任务" in text
    assert "COMFY_UNRELATED" not in text


def test_all_task_reply_groups_parents_and_dedupes_claimed_children() -> None:
    payload = {
        "filters": {
            "task_view": "active",
            "media_type": "",
            "include_finished": False,
        },
        "direct_images": [
            {
                "run_id": "IMG_PARENT",
                "status": "RUNNING",
                "stage": "matting",
                "run_label": "portrait.png",
                "child_run_ids": ["COMFY_CHILD", "CHERRY_CHILD"],
                "items": [
                    {
                        "name": "portrait.png",
                        "total": 1,
                        "matte_done": 1,
                        "smooth_done": 0,
                        "comfyui_run_id": "COMFY_CHILD",
                    }
                ],
            }
        ],
        "direct_videos": [
            {
                "run_id": "VID_PARENT",
                "status": "QUEUED",
                "stage": "queued",
                "run_label": "walk.mp4",
                "items": [{"name": "walk.mp4", "total": 24, "matte_done": 0, "smooth_done": 0}],
            }
        ],
        "animation_flows": [
            {
                "run_id": "AFLOW_PARENT",
                "status": "RUNNING",
                "current_stage": "frame_extract",
                "children": {"pipeline_run_id": "PIPE_CHILD"},
            }
        ],
        "active": [
            {"run_id": "COMFY_CHILD", "status": "RUNNING", "done": 1, "total": 1},
            {"run_id": "CHERRY_CHILD", "status": "RUNNING", "done": 0, "total": 1},
            {"run_id": "PIPE_CHILD", "status": "RUNNING"},
            {"run_id": "CHERRY_SOLO", "status": "RUNNING", "done": 3, "total": 8},
        ],
    }
    text = format_skill_results([{"ok": True, "skill": "agent.task_overview", "result": payload}])

    assert "图片直发：1 个" in text and "IMG_PARENT" in text
    assert "视频直发：1 个" in text and "VID_PARENT" in text
    assert "飞书动画流程：1 个" in text and "AFLOW_PARENT" in text
    assert "独立任务：1 个" in text and "CHERRY_SOLO" in text
    assert "COMFY_CHILD" not in text
    assert "CHERRY_CHILD" not in text
    assert "PIPE_CHILD" not in text


def test_readonly_task_queries_do_not_send_processing_ack() -> None:
    for text in (
        "现在任务进度",
        "现在任务进度有哪些",
        "全部的任务列表呢",
        "直传图片任务呢",
        "队列里有什么",
        "机器现在都在跑什么",
        "完成了哪些任务",
    ):
        assert _is_progress_query(text) is True


def test_task_overview_skill_is_registered() -> None:
    meta = get_skill_meta("agent.task_overview")
    assert meta is not None
    assert meta["risk_level"] == "readonly"


def test_task_overview_filters_queue_without_starting_work(monkeypatch) -> None:
    from assetclaw_matting.skills import agent_ops_skills

    snapshot = {
        "ok": True,
        "filters": {"include_finished": False},
        "direct_images": [
            {"run_id": "IMG_RUNNING", "status": "RUNNING", "stage": "matting"},
            {"run_id": "IMG_QUEUED", "status": "QUEUED", "stage": "queued"},
        ],
        "direct_videos": [{"run_id": "VID_WAITING", "status": "RUNNING", "stage": "waiting_pipeline_queue"}],
        "animation_flows": [{"run_id": "AFLOW_RUNNING", "status": "RUNNING", "current_stage": "frame_extract"}],
        "active": [
            {"run_id": "COMFY_RUNNING", "status": "RUNNING"},
            {"run_id": "CHERRY_QUEUED", "status": "QUEUED"},
        ],
    }
    monkeypatch.setattr(agent_ops_skills, "current_work", lambda **_kwargs: snapshot)

    payload = task_overview(view="queue")

    assert [item["run_id"] for item in payload["direct_images"]] == ["IMG_QUEUED"]
    assert [item["run_id"] for item in payload["direct_videos"]] == ["VID_WAITING"]
    assert payload["animation_flows"] == []
    assert [item["run_id"] for item in payload["active"]] == ["CHERRY_QUEUED"]
    assert payload["filters"]["task_view"] == "queue"
