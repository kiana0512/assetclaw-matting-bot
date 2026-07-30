#!/usr/bin/env python3
"""Create and verify an online SQLite backup without stopping the application."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a transactionally consistent SQLite backup.")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    source = args.source.resolve()
    destination = args.destination.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(destination)

    source_conn = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True, timeout=30)
    destination_conn = sqlite3.connect(destination, timeout=30)
    try:
        source_conn.backup(destination_conn, pages=256, sleep=0.05)
        destination_conn.commit()
        integrity = destination_conn.execute("PRAGMA integrity_check").fetchone()[0]
        quick = destination_conn.execute("PRAGMA quick_check").fetchone()[0]
        tables = {
            row[0]: destination_conn.execute(f'SELECT COUNT(*) FROM "{row[0]}"').fetchone()[0]
            for row in destination_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        }
    finally:
        destination_conn.close()
        source_conn.close()

    if integrity != "ok" or quick != "ok":
        raise RuntimeError(f"SQLite backup verification failed: integrity={integrity!r} quick={quick!r}")

    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source),
        "destination": str(destination),
        "size_bytes": destination.stat().st_size,
        "sha256": sha256(destination),
        "integrity_check": integrity,
        "quick_check": quick,
        "table_counts": tables,
    }
    report_path = (args.report or destination.with_suffix(destination.suffix + ".report.json")).resolve()
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
