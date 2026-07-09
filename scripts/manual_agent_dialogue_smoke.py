from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
for import_root in (ROOT, ROOT / "src"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from assetclaw_matting.brain.local_command_brain import LocalCommandBrain
from assetclaw_matting.brain.schemas import BrainMessage
from assetclaw_matting.config import settings
from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import init_db


ROOT = Path(settings.assetclaw_root)
SMOKE_ROOT = ROOT / "storage" / "debug" / "manual_agent_dialogue"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run manual-style Agent Q/A smoke checks.")
    parser.add_argument("--speech", action="store_true", help="Also run a real ASR voice-message route with the local sample audio.")
    args = parser.parse_args()

    init_db(ROOT / "data" / "manual_agent_dialogue.db")
    create_tables()
    _prepare_files()

    brain = LocalCommandBrain()
    cases = _cases(include_speech=args.speech)
    failures: list[dict[str, Any]] = []

    print("Manual Agent dialogue smoke")
    print("=" * 72)
    for index, case in enumerate(cases, start=1):
        message = BrainMessage(
            conversation_id=case.get("conversation_id", "manual-dialogue"),
            user_id="manual-smoke",
            text=case.get("text", ""),
            attachments=case.get("attachments") or [],
        )
        response = brain.handle_message(message)
        tool_names = [call.skill for call in response.tool_calls]
        record = {
            "index": index,
            "name": case["name"],
            "user": message.text or "[attachment]",
            "reply": response.text,
            "tool_calls": tool_names,
        }
        ok, reason = _check(case, record)
        status = "OK" if ok else "FAIL"
        print(f"\n[{index:02d}] {status} {case['name']}")
        print("用户:", record["user"])
        print("工具:", ", ".join(tool_names) if tool_names else "-")
        print("回复:", response.text.replace("\n", " / "))
        if not ok:
            record["reason"] = reason
            failures.append(record)

    print("\n" + "=" * 72)
    if failures:
        print("FAILURES")
        print(json.dumps(failures, ensure_ascii=False, indent=2))
        return 1
    print(f"All manual dialogue checks passed: {len(cases)}")
    return 0


def _prepare_files() -> None:
    SMOKE_ROOT.mkdir(parents=True, exist_ok=True)
    image = SMOKE_ROOT / "input" / "demo.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    if not image.exists():
        Image.new("RGBA", (8, 8), (32, 128, 255, 255)).save(image)
    workflow = SMOKE_ROOT / "workflow.json"
    if not workflow.exists():
        workflow.write_text(
            json.dumps(
                {
                    "1": {"class_type": "LoadImage", "inputs": {"image": "input.png"}},
                    "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": "manual"}},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )


def _cases(include_speech: bool) -> list[dict[str, Any]]:
    workflow = SMOKE_ROOT / "workflow.json"
    image_dir = SMOKE_ROOT / "input"
    output_dir = SMOKE_ROOT / "output"
    cases: list[dict[str, Any]] = [
        {"name": "capability_help", "text": "你可以干嘛呀", "tools": ["bot.help"], "contains": ["初音未来机器人"]},
        {"name": "capability_help_colloquial", "text": "你会做啥", "tools": ["bot.help"], "contains": ["初音未来机器人"], "not_contains": ["我接原创下一句"]},
        {"name": "normal_greeting_not_singing", "text": "晚上好 你可以听我的语音吗", "contains": ["可以听你的语音", "本地 ASR"], "not_contains": ["我接原创下一句"]},
        {"name": "singing_disabled_enter", "text": "进入唱歌模式", "conversation_id": "manual-no-sing", "contains": ["唱歌", "功能已经关闭"], "not_contains": ["已进入唱歌模式", "我接原创下一句"]},
        {"name": "singing_disabled_stop", "text": "不要再接下一句了", "conversation_id": "manual-no-sing", "contains": ["已经关闭"], "not_contains": ["我接原创下一句"]},
        {"name": "weather_normal_after_old_sing_words", "text": "今天天气怎么样", "conversation_id": "manual-no-sing", "tools": ["life.weather"], "not_contains": ["我接原创下一句"]},
        {"name": "food_normal_after_old_sing_words", "text": "外卖点什么比较好呢", "conversation_id": "manual-no-sing", "tools": ["life.food_suggest"], "not_contains": ["我接原创下一句"]},
        {"name": "comfy_normal_after_old_sing_words", "text": "查看comfyui状态", "conversation_id": "manual-no-sing", "tools": ["comfyui.status"], "not_contains": ["我接原创下一句"]},
        {"name": "p4_normal_after_old_sing_words", "text": "查看p4功能的状态", "conversation_id": "manual-no-sing", "tools": ["p4.help"], "not_contains": ["我接原创下一句"]},
        {"name": "voice_reply_on", "text": "开启语音回复", "conversation_id": "manual-voice", "contains": ["已开启语音回复"]},
        {"name": "voice_reply_off", "text": "关闭语音回复", "conversation_id": "manual-voice", "contains": ["已关闭语音回复"]},
        {"name": "gpu_and_current_work", "text": "查看 GPU 状态和当前任务", "tools": ["system.gpu_status", "agent.current_work"]},
        {"name": "file_list", "text": "看看 E:\\assetclaw-matting-bot\\docs 有哪些文件", "tools": ["file.list_allowed"]},
        {"name": "comfy_preview", "text": f"预览 {image_dir} 到 {output_dir} 的抠图任务，工作流 {workflow}", "tools": ["comfyui.run_preview"]},
        {"name": "comfy_start_requires_confirmation", "text": f"开始批量抠图 输入 {image_dir} 输出 {output_dir} 工作流 {workflow}", "contains": ["请确认", "确认执行"]},
        {"name": "p4_help_route", "text": "p4现在功能有哪些", "tools": ["p4.help"]},
    ]
    if include_speech:
        sample = ROOT / "storage" / "models" / "asr" / "iic__SenseVoiceSmall" / "example" / "zh.mp3"
        cases.append(
            {
                "name": "real_voice_attachment_asr",
                "text": "",
                "conversation_id": "manual-speech",
                "attachments": [{"type": "audio", "local_path": str(sample), "file_name": sample.name}],
                "contains": ["语音识别", "开放时间"],
            }
        )
    return cases


def _check(case: dict[str, Any], record: dict[str, Any]) -> tuple[bool, str]:
    reply = str(record["reply"])
    tools = record["tool_calls"]
    for expected in case.get("contains") or []:
        if expected not in reply:
            return False, f"missing reply text: {expected}"
    for forbidden in case.get("not_contains") or []:
        if forbidden in reply:
            return False, f"forbidden reply text: {forbidden}"
    expected_tools = case.get("tools") or []
    if expected_tools and tools[: len(expected_tools)] != expected_tools:
        return False, f"tool mismatch: expected prefix {expected_tools}, got {tools}"
    return True, ""


if __name__ == "__main__":
    raise SystemExit(main())
