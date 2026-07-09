from __future__ import annotations

import argparse
import sys


def init_runtime() -> None:
    from assetclaw_matting.config import settings
    from assetclaw_matting.db.schema import create_tables
    from assetclaw_matting.db.sqlite import init_db
    from assetclaw_matting.logging_setup import setup_logging

    settings.ensure_dirs()
    setup_logging(settings.log_dir)
    init_db(settings.data_db_path)
    create_tables()


def cmd_init_db(_: argparse.Namespace) -> None:
    init_runtime()
    from assetclaw_matting.config import settings

    print(f"Database initialised: {settings.data_db_path}")


def cmd_gateway(_: argparse.Namespace) -> None:
    init_runtime()
    import uvicorn
    from assetclaw_matting.config import settings

    uvicorn.run(
        "assetclaw_matting.api.main:app",
        host=settings.gateway_host,
        port=settings.gateway_port,
        log_level="info",
    )


def cmd_worker(_: argparse.Namespace) -> None:
    init_runtime()
    from assetclaw_matting.worker.worker_loop import run_forever

    run_forever()


def cmd_build_unity_ready(args: argparse.Namespace) -> None:
    from pathlib import Path
    from tools.animation_automation.core import build_unity_ready, format_unity_ready_summary

    init_runtime()
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


def main() -> None:
    parser = argparse.ArgumentParser(description="AssetClaw Win3090 Animation Butler")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("init-db")
    sub.add_parser("gateway")
    sub.add_parser("worker")
    ready = sub.add_parser("build-unity-ready")
    ready.add_argument("--date-root", required=True)
    ready.add_argument("--overwrite", action="store_true")
    ready.add_argument("--copy-mode", choices=("copy", "hardlink"), default="copy")
    ready.add_argument("--include-empty-types", action="store_true")
    ready.add_argument("--scene-unity-category", default="角色动画")
    ready.add_argument("--missing-smooth-warning", action="store_true")
    args = parser.parse_args()
    dispatch = {"init-db": cmd_init_db, "gateway": cmd_gateway, "worker": cmd_worker, "build-unity-ready": cmd_build_unity_ready}
    if args.command not in dispatch:
        parser.print_help()
        sys.exit(1)
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
