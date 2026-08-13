#!/usr/bin/env python3
"""Stop only the ComfyUI container when WSL dxg allocation errors storm."""

from __future__ import annotations

import collections
import logging
import subprocess
import time


CONTAINER = "gpu-control-node-comfyui-1"
WINDOW_SECONDS = 15
TRIP_COUNT = 3
COOLDOWN_SECONDS = 300
PATTERNS = (
    "create_existing_sysmem: establish_gpadl failed: -122",
    "dxgkio_create_allocation: Ioctl failed: -12",
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


def container_running() -> bool:
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def stop_comfyui(reason: str) -> None:
    if not container_running():
        return
    logging.critical("DXG circuit breaker tripped: %s", reason)
    result = subprocess.run(
        ["docker", "stop", "--time", "5", CONTAINER],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        logging.critical(
            "Stopped %s; SSH and Node Agent remain online", CONTAINER
        )
    else:
        logging.error("Failed to stop %s: %s", CONTAINER, result.stderr.strip())


def main() -> None:
    events: collections.deque[float] = collections.deque()
    last_trip = 0.0
    while True:
        process = subprocess.Popen(
            ["journalctl", "-k", "-f", "-n", "0", "-o", "cat"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            if not any(pattern in line for pattern in PATTERNS):
                continue
            now = time.monotonic()
            events.append(now)
            while events and now - events[0] > WINDOW_SECONDS:
                events.popleft()
            logging.warning("DXG allocation failure %d/%d: %s", len(events), TRIP_COUNT, line.strip())
            if len(events) >= TRIP_COUNT and now - last_trip >= COOLDOWN_SECONDS:
                stop_comfyui(line.strip())
                last_trip = now
                events.clear()
        logging.warning("kernel journal stream ended; retrying")
        time.sleep(2)


if __name__ == "__main__":
    main()
