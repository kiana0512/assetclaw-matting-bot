from __future__ import annotations

from pathlib import Path


def pytest_sessionstart(session: object) -> None:
    """Keep the shared test database repeatable across local test runs."""
    root = Path(__file__).resolve().parents[1]
    database = root / "data" / "test_assetclaw.db"
    for path in (database, Path(f"{database}-wal"), Path(f"{database}-shm")):
        path.unlink(missing_ok=True)
