from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
import urllib.error
import urllib.request


def post_json(url: str, payload: dict, timeout: float = 30.0) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw else {}


def get_json(url: str, timeout: float = 30.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw else {}


async def timed(label: str, fn, *args):
    started = time.perf_counter()
    try:
        payload = await asyncio.to_thread(fn, *args)
        ok = payload.get("ok", True) is not False
        return label, ok, (time.perf_counter() - started) * 1000, payload.get("error", "")
    except (urllib.error.URLError, TimeoutError, Exception) as exc:
        return label, False, (time.perf_counter() - started) * 1000, str(exc)


async def worker(base: str, index: int, include_brain: bool):
    skill_payload = {
        "skill": "agent.current_work",
        "arguments": {"include_gpu": False},
        "requested_by": f"webui_proxy_stress:{index}",
    }
    brain_payload = {
        "text": f"webui proxy stress status ping {index}",
        "conversation_id": f"webui_proxy_stress_{index % 8}",
        "async_mode": True,
    }
    routes = [
        timed("health", get_json, f"{base}/health"),
        timed("skill", post_json, f"{base}/skills/call", skill_payload),
        timed("memory", get_json, f"{base}/admin/memory-pack?scope=global&conversation_id=test&limit=5"),
        timed("logs", get_json, f"{base}/admin/skill-calls?limit=20"),
    ]
    if include_brain:
        routes.append(timed("brain_ack", post_json, f"{base}/brain/test", brain_payload))
    return await asyncio.gather(*routes)


def summarize(rows):
    flat = [item for group in rows for item in group]
    by_label = {}
    for label, ok, ms, error in flat:
        by_label.setdefault(label, []).append((ok, ms, error))
    for label in sorted(by_label):
        items = by_label[label]
        times = sorted(ms for _, ms, _ in items)
        errors = [err for ok, _, err in items if not ok]
        p50 = statistics.median(times)
        p95 = times[min(len(times) - 1, int(len(times) * 0.95))]
        p99 = times[min(len(times) - 1, int(len(times) * 0.99))]
        print(f"{label:10s} ok={len(items)-len(errors)}/{len(items)} p50={p50:.1f}ms p95={p95:.1f}ms p99={p99:.1f}ms max={max(times):.1f}ms")
        if errors:
            print(f"  sample_error={errors[0][:180]}")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:5180/api")
    parser.add_argument("--calls", type=int, default=40)
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument("--no-brain", action="store_true", help="Do not enqueue Agent brain jobs; useful for pure WebUI read pressure.")
    args = parser.parse_args()
    sem = asyncio.Semaphore(args.concurrency)

    async def guarded(i: int):
        async with sem:
            return await worker(args.base.rstrip("/"), i, not args.no_brain)

    started = time.perf_counter()
    rows = await asyncio.gather(*(guarded(i) for i in range(args.calls)))
    summarize(rows)
    print(f"total={(time.perf_counter() - started):.2f}s")


if __name__ == "__main__":
    asyncio.run(main())
