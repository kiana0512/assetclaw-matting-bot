"""CLI entry point for AssetClaw Matting Bot.

Usage:
    python -m assetclaw_matting.cli.main <command> [options]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _init_db_and_logging() -> None:
    from assetclaw_matting.config import settings
    from assetclaw_matting.db.schema import create_tables
    from assetclaw_matting.db.sqlite import init_db
    from assetclaw_matting.logging_setup import setup_logging

    settings.ensure_dirs()
    setup_logging(settings.log_dir)
    db_path = settings.data_dir / "assetclaw.db"
    init_db(db_path)
    create_tables()


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_init_db(args: argparse.Namespace) -> None:
    _init_db_and_logging()
    from assetclaw_matting.config import settings
    print(f"Database initialised: {settings.data_dir / 'assetclaw.db'}")


def cmd_gateway(args: argparse.Namespace) -> None:
    import uvicorn
    from assetclaw_matting.config import settings

    uvicorn.run(
        "assetclaw_matting.api.main:app",
        host=settings.gateway_host,
        port=settings.gateway_port,
        reload=False,
        log_level="info",
    )


def cmd_worker(args: argparse.Namespace) -> None:
    from assetclaw_matting.worker.worker_loop import run_worker
    run_worker()


def cmd_batch_create(args: argparse.Namespace) -> None:
    _init_db_and_logging()
    from assetclaw_matting.services.batch_service import create_batch

    try:
        batch = create_batch(
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            workflow_type=args.workflow_type,
            notify_chat_id=args.notify_chat_id or None,
            note=args.note or None,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"\nBatch created:")
    print(f"  ID:           {batch.id}")
    print(f"  Workflow:     {batch.workflow_type}")
    print(f"  Total images: {batch.total_count}")
    print(f"  Input dir:    {batch.input_dir}")
    print(f"  Output dir:   {batch.output_dir}")
    print(f"\nTo start processing:")
    print(f"  python -m assetclaw_matting.cli.main batch-start --batch-id {batch.id}")


def cmd_batch_start(args: argparse.Namespace) -> None:
    _init_db_and_logging()
    from assetclaw_matting.services.batch_service import start_batch

    try:
        batch = start_batch(args.batch_id)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Batch {batch.id} started (status={batch.status})")
    print(f"Start worker: python -m assetclaw_matting.cli.main worker")


def cmd_batch_list(args: argparse.Namespace) -> None:
    _init_db_and_logging()
    from assetclaw_matting.db.batch_repo import list_batches

    batches = list_batches(limit=args.limit)
    if not batches:
        print("No batches found.")
        return

    hdr = f"{'BATCH_ID':<22} {'STATUS':<12} {'DONE':>6}/{'>TOTAL':<6} {'WORKFLOW':<14} {'CREATED'}"
    print(hdr)
    print("-" * len(hdr))
    for b in batches:
        done = b.succeeded_count + b.failed_count
        print(
            f"{b.id:<22} {b.status:<12} {done:>6}/{b.total_count:<6} "
            f"{b.workflow_type:<14} {b.created_at[:19]}"
        )


def cmd_batch_status(args: argparse.Namespace) -> None:
    _init_db_and_logging()
    from assetclaw_matting.db.batch_repo import get_batch

    b = get_batch(args.batch_id)
    if b is None:
        print(f"Batch not found: {args.batch_id}", file=sys.stderr)
        sys.exit(1)

    print(f"Batch:        {b.id}")
    print(f"Status:       {b.status}")
    print(f"Workflow:     {b.workflow_type}")
    print(f"Total:        {b.total_count}")
    print(f"Succeeded:    {b.succeeded_count}")
    print(f"Failed:       {b.failed_count}")
    print(f"Canceled:     {b.canceled_count}")
    print(f"Running:      {b.running_count}")
    print(f"Queued:       {b.queued_count}")
    print(f"Input dir:    {b.input_dir}")
    print(f"Output dir:   {b.output_dir}")
    if b.note:
        print(f"Note:         {b.note}")


def cmd_task_list(args: argparse.Namespace) -> None:
    _init_db_and_logging()
    from assetclaw_matting.db.task_repo import list_tasks

    tasks = list_tasks(
        batch_id=args.batch_id or None,
        status=args.status or None,
        limit=args.limit,
    )
    if not tasks:
        print("No tasks found.")
        return

    hdr = f"{'TASK_ID':<38} {'STATUS':<12} {'FILENAME':<30} {'WORKER'}"
    print(hdr)
    print("-" * len(hdr))
    for t in tasks:
        print(
            f"{t.id:<38} {t.status:<12} "
            f"{(t.original_filename or ''):<30} {t.worker_id or ''}"
        )


def cmd_queue(args: argparse.Namespace) -> None:
    _init_db_and_logging()
    from assetclaw_matting.db import batch_repo, task_repo

    stats = task_repo.queue_stats()
    running_batches = batch_repo.running_batch_count()
    print(f"RUNNING batches: {running_batches}")
    print(f"QUEUED tasks:    {stats['QUEUED']}")
    print(f"RUNNING tasks:   {stats['RUNNING']}")
    print(f"FAILED tasks:    {stats['FAILED']}")


# ── Parser setup ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="assetclaw_matting",
        description="AssetClaw Matting Bot CLI",
    )
    sub = parser.add_subparsers(dest="command")

    # init-db
    sub.add_parser("init-db", help="Initialise the SQLite database")

    # gateway
    sub.add_parser("gateway", help="Start the FastAPI gateway")

    # worker
    sub.add_parser("worker", help="Start the worker loop")

    # batch-create
    p_bc = sub.add_parser("batch-create", help="Create a batch from a directory")
    p_bc.add_argument("--input-dir", required=True)
    p_bc.add_argument("--output-dir", required=True)
    p_bc.add_argument("--workflow-type", default="matting_v1")
    p_bc.add_argument("--notify-chat-id", default="")
    p_bc.add_argument("--note", default="")

    # batch-start
    p_bs = sub.add_parser("batch-start", help="Start a CREATED batch")
    p_bs.add_argument("--batch-id", required=True)

    # batch-list
    p_bl = sub.add_parser("batch-list", help="List recent batches")
    p_bl.add_argument("--limit", type=int, default=20)

    # batch-status
    p_bst = sub.add_parser("batch-status", help="Show batch details")
    p_bst.add_argument("--batch-id", required=True)

    # task-list
    p_tl = sub.add_parser("task-list", help="List tasks")
    p_tl.add_argument("--batch-id", default="")
    p_tl.add_argument("--status", default="")
    p_tl.add_argument("--limit", type=int, default=50)

    # queue
    sub.add_parser("queue", help="Show queue statistics")

    args = parser.parse_args()

    dispatch = {
        "init-db": cmd_init_db,
        "gateway": cmd_gateway,
        "worker": cmd_worker,
        "batch-create": cmd_batch_create,
        "batch-start": cmd_batch_start,
        "batch-list": cmd_batch_list,
        "batch-status": cmd_batch_status,
        "task-list": cmd_task_list,
        "queue": cmd_queue,
    }

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    fn = dispatch.get(args.command)
    if fn is None:
        parser.print_help()
        sys.exit(1)

    fn(args)


if __name__ == "__main__":
    main()
