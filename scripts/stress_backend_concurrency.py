from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Sample:
    name: str
    ok: bool
    ms: float
    error: str = ""


def _env_value(name: str, default: str = "") -> str:
    path = Path(".env")
    if not path.exists():
        return os.environ.get(name, default)
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        if key.strip().upper() == name.upper():
            return value.strip().strip("'\"")
    return os.environ.get(name, default)


def request_json(method: str, url: str, payload: dict[str, Any] | None = None, headers: dict[str, str] | None = None, timeout: float = 30.0) -> tuple[dict[str, Any], float]:
    body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json; charset=utf-8")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return (json.loads(raw) if raw else {}, (time.perf_counter() - start) * 1000)


def record(name: str, fn) -> Sample:
    start = time.perf_counter()
    try:
        payload, ms = fn()
        return Sample(name, bool(payload.get("ok", True)), ms)
    except Exception as exc:
        return Sample(name, False, (time.perf_counter() - start) * 1000, str(exc))


def print_summary(samples: list[Sample]) -> None:
    by_name: dict[str, list[Sample]] = {}
    for sample in samples:
        by_name.setdefault(sample.name, []).append(sample)
    print("\n=== stress summary ===")
    for name, group in sorted(by_name.items()):
        times = sorted(item.ms for item in group)
        ok = sum(1 for item in group if item.ok)
        p50 = statistics.median(times) if times else 0
        p95 = times[max(0, int(len(times) * 0.95) - 1)] if times else 0
        p99 = times[max(0, int(len(times) * 0.99) - 1)] if times else 0
        worst = times[-1] if times else 0
        print(f"{name:22s} ok={ok:4d}/{len(group):4d} p50={p50:7.1f}ms p95={p95:7.1f}ms p99={p99:7.1f}ms max={worst:7.1f}ms")
        errors = [item.error for item in group if not item.ok and item.error][:3]
        for error in errors:
            print(f"  error: {error[:180]}")


def run_http_stress(base: str, calls: int, concurrency: int) -> list[Sample]:
    token = _env_value("ASSETCLAW_SKILL_TOKEN") or _env_value("SKILL_API_TOKEN") or "please_change_me"
    headers = {"X-Skill-Token": token}
    jobs: list[str] = []
    samples: list[Sample] = []

    def brain_call(index: int) -> Sample:
        def _call():
            payload, ms = request_json(
                "POST",
                f"{base}/brain/test",
                {
                    "text": f"stress hello {index}",
                    "conversation_id": f"webui:stress:{index % max(1, concurrency)}",
                    "source": "external_webui_stress",
                    "async_mode": True,
                },
                timeout=10,
            )
            if payload.get("job_id"):
                jobs.append(payload["job_id"])
            return payload, ms

        return record("brain_ack", _call)

    skill_names = ["queue.status", "custom_pipeline.module_catalog", "custom_pipeline.list_definitions", "memory.list"]

    def skill_call(index: int) -> Sample:
        skill = skill_names[index % len(skill_names)]
        args = {"scope": "global", "limit": 5} if skill == "memory.list" else {}
        return record("skill_call", lambda: request_json("POST", f"{base}/skills/v1/call", {"skill": skill, "arguments": args, "requested_by": "stress"}, headers=headers, timeout=20))

    def admin_call(index: int) -> Sample:
        urls = [
            f"{base}/health",
            f"{base}/admin/queue",
            f"{base}/admin/brain-messages?conversation_id=test&limit=10",
            f"{base}/admin/skill-calls?limit=10",
            f"{base}/admin/memory-pack?scope=global&conversation_id=test&limit=5",
        ]
        return record("admin_read", lambda: request_json("GET", urls[index % len(urls)], timeout=20))

    work = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for index in range(calls):
            work.append(pool.submit(brain_call, index))
            work.append(pool.submit(skill_call, index))
            work.append(pool.submit(admin_call, index))
        for future in as_completed(work):
            samples.append(future.result())

    def poll_job(job_id: str) -> Sample:
        deadline = time.time() + 90
        last: dict[str, Any] = {}
        start = time.perf_counter()
        while time.time() < deadline:
            try:
                last, _ = request_json("GET", f"{base}/brain/jobs/{job_id}", timeout=10)
                if last.get("status") in {"DONE", "FAILED"}:
                    break
            except Exception as exc:
                last = {"status": "POLL_ERROR", "error": str(exc)}
                time.sleep(0.5)
            time.sleep(0.25)
        return Sample("brain_done", last.get("status") == "DONE", (time.perf_counter() - start) * 1000, str(last.get("error") or last))

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for future in as_completed([pool.submit(poll_job, job_id) for job_id in jobs]):
            samples.append(future.result())
    return samples


def run_feishu_processor_stress(calls: int, concurrency: int) -> list[Sample]:
    root = Path.cwd().resolve()
    src = root / "src"
    for item in (str(root), str(src)):
        if item not in sys.path:
            sys.path.insert(0, item)
    os.environ.setdefault("PYTHONPATH", str(src))
    from assetclaw_matting.config import settings
    from assetclaw_matting.db.schema import create_tables
    from assetclaw_matting.db.sqlite import init_db
    from assetclaw_matting.feishu.models import FeishuMessageEvent
    from assetclaw_matting.feishu import processor

    settings.ensure_dirs()
    init_db(settings.data_db_path)
    create_tables()
    processor._try_reply = lambda *args, **kwargs: None
    processor._try_send_tts_reply = lambda *args, **kwargs: None
    processor._try_send_emotional_sticker = lambda *args, **kwargs: None

    def one(index: int) -> Sample:
        event = FeishuMessageEvent(
            trace_id=f"stress-feishu-{index}",
            event_id=f"stress-feishu-event-{time.time_ns()}-{index}",
            message_id=f"stress-message-{index}",
            chat_id=f"stress-chat-{index % max(1, concurrency)}",
            chat_type="group",
            open_id=f"stress-user-{index % max(1, concurrency * 2)}",
            user_id=f"user-{index}",
            text=f"看一下现在状态 stress {index}",
        )
        start = time.perf_counter()
        try:
            result = processor.process_feishu_message(event)
            return Sample("feishu_processor_ack", result.ok, (time.perf_counter() - start) * 1000, str(result.error or ""))
        except Exception as exc:
            return Sample("feishu_processor_ack", False, (time.perf_counter() - start) * 1000, str(exc))

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        samples = [future.result() for future in as_completed([pool.submit(one, index) for index in range(calls)])]

    from assetclaw_matting.services.agent_job_queue import queue_snapshot

    deadline = time.time() + 90
    while time.time() < deadline:
        snap = queue_snapshot()
        if not snap.get("queued") and not snap.get("running"):
            break
        time.sleep(0.5)
    return samples


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:7865")
    parser.add_argument("--calls", type=int, default=40)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--skip-feishu", action="store_true")
    args = parser.parse_args()

    samples = run_http_stress(args.base.rstrip("/"), args.calls, args.concurrency)
    if not args.skip_feishu:
        samples.extend(run_feishu_processor_stress(max(5, args.calls // 2), args.concurrency))
    print_summary(samples)
    failed = [sample for sample in samples if not sample.ok]
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
