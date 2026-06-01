from __future__ import annotations

import time


def run_forever() -> None:
    from assetclaw_matting.config import settings

    while True:
        time.sleep(settings.worker_poll_interval_seconds)
