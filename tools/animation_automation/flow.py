from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FlowPlan:
    date_root: Path
    unity_ready: Path
    unity_project: str
    workspace: str
    stream: str

    def as_dict(self) -> dict[str, Any]:
        ready = self.unity_ready
        return {
            "ok": True,
            "mode": "shelve_only",
            "submit": "disabled",
            "date_root": str(self.date_root),
            "unity_ready": str(ready),
            "stages": [
                {
                    "id": 1,
                    "name": "feishu_download",
                    "description": "飞书文档读取并下载视频，写入 source_manifest.json。",
                    "outputs": [
                        str(self.date_root / "source_manifest.json"),
                        str(self.date_root / "scene/videos"),
                        str(self.date_root / "emoji/videos"),
                    ],
                },
                {
                    "id": 2,
                    "name": "extract_frames",
                    "description": "按分流目录抽帧，帧号从 0000.png 开始。",
                    "outputs": [
                        str(self.date_root / "scene/frames"),
                        str(self.date_root / "emoji/frames"),
                    ],
                },
                {
                    "id": 3,
                    "name": "matting",
                    "description": "ComfyUI 抠图，同一路 frames -> matte；后处理已集成在抠图工作流中。",
                    "outputs": [
                        str(self.date_root / "scene/matte"),
                        str(self.date_root / "emoji/matte"),
                    ],
                },
                {
                    "id": 4,
                    "name": "unity_ready",
                    "description": "把抠图结果汇总为 Unity 插件可读的 scene / emoji 两包。",
                    "command": f'python -m tools.animation_automation.cli build-unity-ready --date-root "{self.date_root}"',
                    "outputs": [
                        str(ready / "manifest.json"),
                        str(ready / "scene/animation_resource_manifest.json"),
                        str(ready / "scene/frames"),
                        str(ready / "emoji/animation_resource_manifest.json"),
                        str(ready / "emoji/frames"),
                    ],
                },
                {
                    "id": 5,
                    "name": "unity_import",
                    "description": "调用 Unity 插件导入 scene / emoji 包。当前项目不修改插件源码；如无 batchmode 入口则只生成导入参数。",
                    "command": f'python -m tools.animation_automation.cli import-unity-ready --unity-ready "{ready}" --package both',
                    "inputs": [
                        str(ready / "scene/animation_resource_manifest.json"),
                        str(ready / "scene/frames"),
                        str(ready / "emoji/animation_resource_manifest.json"),
                        str(ready / "emoji/frames"),
                    ],
                },
                {
                    "id": 6,
                    "name": "p4_shelve",
                    "description": "P4 同步、预览、单步确认 create-cl/reconcile/shelve，最后生成 Feishu 可读报告。submit 永远禁用。",
                    "safe_commands": [
                        f'python -m tools.p4_assistant.cli status --workspace {self.workspace}',
                        f'python -m tools.p4_assistant.cli workspace-info --workspace {self.workspace}',
                        f'python -m tools.p4_assistant.cli streams --workspace {self.workspace}',
                        f'python -m tools.p4_assistant.cli switch-stream --workspace {self.workspace} --stream "{self.stream}" --preview',
                        f'python -m tools.p4_assistant.cli get-latest --workspace {self.workspace} --scope managed --preview',
                        f'python -m tools.p4_assistant.cli preview --workspace {self.workspace}',
                    ],
                    "confirmed_commands": [
                        f'python -m tools.p4_assistant.cli switch-stream --workspace {self.workspace} --stream "{self.stream}" --yes',
                        f'python -m tools.p4_assistant.cli create-cl --workspace {self.workspace} --desc "UI animation import" --yes',
                        f'python -m tools.p4_assistant.cli reconcile --workspace {self.workspace} --cl <created_cl> --yes',
                        f'python -m tools.p4_assistant.cli shelve --workspace {self.workspace} --cl <created_cl> --yes',
                        f'python -m tools.p4_assistant.cli report --workspace {self.workspace} --cl <created_cl> --unity-ready-manifest "{ready / "manifest.json"}"',
                    ],
                    "red_lines": ["submit", "merge", "copy", "integrate", "stream create", "password/token/ticket persistence"],
                },
            ],
        }


def build_flow_plan(date_root: Path, unity_project: str = "", workspace: str = "spark_client", stream: str = "//streams/001") -> dict[str, Any]:
    root = date_root.resolve()
    plan = FlowPlan(
        date_root=root,
        unity_ready=root / "unity_ready",
        unity_project=unity_project,
        workspace=workspace,
        stream=stream,
    )
    return plan.as_dict()


def format_flow_plan(plan: dict[str, Any]) -> str:
    lines = [
        "动画自动化流程计划",
        f"Date Root: {plan['date_root']}",
        f"Unity Ready: {plan['unity_ready']}",
        "Submit: disabled",
        "",
    ]
    for stage in plan["stages"]:
        lines.append(f"{stage['id']}. {stage['name']}")
        lines.append(f"   {stage['description']}")
        if stage.get("command"):
            lines.append(f"   Command: {stage['command']}")
        for key in ("outputs", "inputs", "safe_commands", "confirmed_commands", "red_lines"):
            values = stage.get(key) or []
            if values:
                lines.append(f"   {key}:")
                lines.extend(f"   - {item}" for item in values)
        lines.append("")
    return "\n".join(lines).rstrip()


def dumps_flow_plan(plan: dict[str, Any]) -> str:
    return json.dumps(plan, ensure_ascii=False, indent=2)
