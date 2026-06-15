from __future__ import annotations

import argparse
from pathlib import Path

from tools.animation_automation.core import build_unity_ready, format_unity_ready_summary
from tools.animation_automation.flow import build_flow_plan, dumps_flow_plan, format_flow_plan


def cmd_build_unity_ready(args: argparse.Namespace) -> int:
    date_root = Path(args.date_root).resolve()
    report = build_unity_ready(
        date_root=date_root,
        overwrite=bool(args.overwrite),
        copy_mode=args.copy_mode,
        include_empty_types=bool(args.include_empty_types),
        scene_unity_category=args.scene_unity_category,
        missing_smooth_is_error=not bool(args.missing_smooth_warning),
    )
    print(format_unity_ready_summary(date_root, report))
    return 0


def cmd_import_unity_ready(args: argparse.Namespace) -> int:
    ready_root = Path(args.unity_ready).resolve()
    packages = ["scene", "emoji"] if args.package == "both" else [args.package]
    print("Unity 插件导入路径：")
    for package in packages:
        json_path = ready_root / package / "animation_resource_manifest.json"
        frames_path = ready_root / package / "frames"
        print()
        print(f"{package.capitalize()}:")
        print(f"JSON: {json_path}")
        print(f"Frames: {frames_path}")
    print()
    print("说明：当前 Unity 插件没有项目内可验证的命令行入口，本命令只准备导入参数。")
    print("请在 Unity 插件窗口中分别选择对应 JSON 和 Frames 源根目录导入。")
    if args.unity_project:
        print()
        print("Unity Project:")
        print(str(Path(args.unity_project).resolve()))
        print("需要插件负责人补充 batchmode 方法后，才能安全自动调用 Unity 导入。")
    return 0


def cmd_flow_plan(args: argparse.Namespace) -> int:
    plan = build_flow_plan(
        date_root=Path(args.date_root),
        unity_project=args.unity_project,
        workspace=args.workspace,
        stream=args.stream,
    )
    print(dumps_flow_plan(plan) if args.json else format_flow_plan(plan))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Animation automation utilities.")
    sub = parser.add_subparsers(dest="command")

    ready = sub.add_parser("build-unity-ready", help="Build Unity plugin readable scene/emoji packages.")
    ready.add_argument("--date-root", required=True, help="Date root, e.g. E:/animation_automation/2026-06-09")
    ready.add_argument("--overwrite", action="store_true", help="Remove existing unity_ready and rebuild.")
    ready.add_argument("--copy-mode", choices=("copy", "hardlink"), default="copy")
    ready.add_argument("--include-empty-types", action="store_true")
    ready.add_argument("--scene-unity-category", default="角色动画")
    ready.add_argument("--missing-smooth-warning", action="store_true", help="Warn instead of failing when a smooth directory is missing.")
    ready.set_defaults(func=cmd_build_unity_ready)

    unity = sub.add_parser("import-unity-ready", help="Print Unity plugin import arguments for a unity_ready package.")
    unity.add_argument("--unity-project", default="")
    unity.add_argument("--unity-ready", required=True)
    unity.add_argument("--package", choices=("scene", "emoji", "both"), default="both")
    unity.set_defaults(func=cmd_import_unity_ready)

    flow = sub.add_parser("flow-plan", help="Print the 7-stage animation -> Cherry smooth -> Unity -> P4 shelve-only plan.")
    flow.add_argument("--date-root", required=True)
    flow.add_argument("--unity-project", default="")
    flow.add_argument("--workspace", default="spark_client")
    flow.add_argument("--stream", default="//streams/001")
    flow.add_argument("--json", action="store_true")
    flow.set_defaults(func=cmd_flow_plan)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
