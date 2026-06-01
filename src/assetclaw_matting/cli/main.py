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


def main() -> None:
    parser = argparse.ArgumentParser(description="AssetClaw Win3090 Animation Butler")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("init-db")
    sub.add_parser("gateway")
    sub.add_parser("worker")
    args = parser.parse_args()
    dispatch = {"init-db": cmd_init_db, "gateway": cmd_gateway, "worker": cmd_worker}
    if args.command not in dispatch:
        parser.print_help()
        sys.exit(1)
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
