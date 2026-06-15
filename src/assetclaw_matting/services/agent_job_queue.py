from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from assetclaw_matting.brain.schemas import BrainMessage
from assetclaw_matting.config import settings
from assetclaw_matting.progress import reset_progress_sender, set_progress_sender
from assetclaw_matting.runtime_context import reset_runtime_context, set_runtime_context
from assetclaw_matting.skills.security import redact_secrets

log = logging.getLogger(__name__)

JobCallback = Callable[[dict[str, Any]], None]


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class AgentJob:
    id: str
    message: BrainMessage
    channel: str
    conversation_id: str
    user_id: str
    trace_id: str
    status: str = "QUEUED"
    queued_at: str = field(default_factory=_now_iso)
    started_at: str = ""
    finished_at: str = ""
    error: str = ""
    response: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    last_progress: str = ""
    callback: JobCallback | None = None

    def public_dict(self, position: int | None = None) -> dict[str, Any]:
        data = {
            "ok": self.status not in {"FAILED"},
            "job_id": self.id,
            "id": self.id,
            "channel": self.channel,
            "conversation_id": self.conversation_id,
            "user_id": self.user_id,
            "trace_id": self.trace_id,
            "status": self.status,
            "queued_at": self.queued_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "response": self.response,
            "last_progress": self.last_progress,
            "text_preview": self.message.text[:160],
        }
        if position is not None:
            data["position"] = position
        return data


class AgentJobQueue:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._wake = threading.Condition(self._lock)
        self._jobs: dict[str, AgentJob] = {}
        self._pending: deque[str] = deque()
        self._active_conversations: set[str] = set()
        self._running = 0
        self._started = False
        self._last_enqueue_monotonic = 0.0
        max_workers = max(1, int(settings.agent_queue_max_workers or 1))
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="agent_job")
        self._store_dir = Path(settings.storage_dir) / "agent_jobs"

    def enqueue(
        self,
        message: BrainMessage,
        *,
        trace_id: str = "",
        context: dict[str, Any] | None = None,
        callback: JobCallback | None = None,
    ) -> dict[str, Any]:
        self._ensure_started()
        job_id = f"AJOB_{uuid.uuid4().hex[:12].upper()}"
        job = AgentJob(
            id=job_id,
            message=message,
            channel=message.channel or "brain",
            conversation_id=message.conversation_id or "global",
            user_id=message.user_id or "",
            trace_id=trace_id or uuid.uuid4().hex,
            context=context or {},
            callback=callback,
        )
        with self._wake:
            self._jobs[job_id] = job
            self._pending.append(job_id)
            self._last_enqueue_monotonic = time.monotonic()
            position = self._queue_position_locked(job_id)
            self._wake.notify_all()
            return job.public_dict(position=position)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id) or self._load(job_id)
            if not job:
                return None
            return job.public_dict(position=self._queue_position_locked(job_id))

    def list(self, conversation_id: str = "", limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._jobs.values())
            if conversation_id:
                items = [job for job in items if job.conversation_id == conversation_id]
            items.sort(key=lambda job: job.queued_at, reverse=True)
            return [job.public_dict(position=self._queue_position_locked(job.id)) for job in items[: max(1, min(limit, 100))]]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            counts = {"QUEUED": len(self._pending), "RUNNING": self._running, "DONE": 0, "FAILED": 0}
            for job in self._jobs.values():
                if job.status == "DONE":
                    counts["DONE"] += 1
                elif job.status == "FAILED":
                    counts["FAILED"] += 1
            return {
                "ok": True,
                "status": "running" if self._running else ("queued" if self._pending else "idle"),
                "queued": counts["QUEUED"],
                "running": counts["RUNNING"],
                "done": counts["DONE"],
                "failed": counts["FAILED"],
                "active_conversations": sorted(self._active_conversations),
                "max_workers": max(1, int(settings.agent_queue_max_workers or 1)),
            }

    def clear_pending(self, conversation_prefix: str = "") -> int:
        with self._wake:
            kept: deque[str] = deque()
            deleted = 0
            for job_id in self._pending:
                job = self._jobs.get(job_id)
                if not job or job.status != "QUEUED":
                    continue
                if conversation_prefix and not job.conversation_id.startswith(conversation_prefix):
                    kept.append(job_id)
                    continue
                job.status = "FAILED"
                job.finished_at = _now_iso()
                job.error = "已由管理员清理未开始的 Agent 队列任务。"
                deleted += 1
            self._pending = kept
            self._wake.notify_all()
            return deleted

    def _ensure_started(self) -> None:
        with self._lock:
            if self._started:
                return
            self._store_dir.mkdir(parents=True, exist_ok=True)
            threading.Thread(target=self._dispatch_loop, name="agent_job_dispatcher", daemon=True).start()
            self._started = True

    def _dispatch_loop(self) -> None:
        while True:
            with self._wake:
                job = self._next_runnable_locked()
                if job is None:
                    self._wake.wait(timeout=max(0.1, float(settings.agent_queue_poll_seconds or 0.25)))
                    continue
                job.status = "RUNNING"
                job.started_at = _now_iso()
                self._running += 1
                self._active_conversations.add(job.conversation_id)
            self._executor.submit(self._run_job, job.id)

    def _next_runnable_locked(self) -> AgentJob | None:
        max_workers = max(1, int(settings.agent_queue_max_workers or 1))
        if self._running >= max_workers:
            return None
        grace = max(0.0, float(settings.agent_queue_dispatch_grace_seconds or 0.0))
        if grace and self._pending and (time.monotonic() - self._last_enqueue_monotonic) < grace:
            return None
        for _ in range(len(self._pending)):
            job_id = self._pending.popleft()
            job = self._jobs.get(job_id)
            if not job or job.status != "QUEUED":
                continue
            if job.conversation_id in self._active_conversations:
                self._pending.append(job_id)
                continue
            return job
        return None

    def _run_job(self, job_id: str) -> None:
        callback: JobCallback | None = None
        public: dict[str, Any] = {}
        try:
            from assetclaw_matting.brain import router as brain_router

            with self._lock:
                job = self._jobs[job_id]

            def _progress_sender(text: str) -> None:
                clean = redact_secrets(text)
                log.debug("agent_job progress job_id=%s text=%s", job.id, clean)
                with self._lock:
                    job.last_progress = clean

            progress_token = set_progress_sender(_progress_sender)
            context_token = set_runtime_context(**job.context)
            try:
                response = brain_router.handle_message(job.message)
            finally:
                reset_runtime_context(context_token)
                reset_progress_sender(progress_token)

            with self._wake:
                job.status = "DONE"
                job.finished_at = _now_iso()
                job.response = response.model_dump()
                self._finish_locked(job)
                callback = job.callback
                public = job.public_dict(position=self._queue_position_locked(job.id))
            self._persist(job)
        except Exception as exc:
            log.exception("agent_job failed job_id=%s", job_id)
            with self._wake:
                job = self._jobs[job_id]
                job.status = "FAILED"
                job.finished_at = _now_iso()
                job.error = str(exc)
                self._finish_locked(job)
                callback = job.callback
                public = job.public_dict(position=self._queue_position_locked(job.id))
            self._persist(job)
        if callback:
            try:
                callback(public)
            except Exception:
                log.exception("agent_job callback failed job_id=%s", job_id)

    def _finish_locked(self, job: AgentJob) -> None:
        self._running = max(0, self._running - 1)
        self._active_conversations.discard(job.conversation_id)
        self._wake.notify_all()

    def _queue_position_locked(self, job_id: str) -> int | None:
        for index, pending_id in enumerate(self._pending, start=1):
            if pending_id == job_id:
                return index
        return None

    def _persist(self, job: AgentJob) -> None:
        try:
            self._store_dir.mkdir(parents=True, exist_ok=True)
            payload = job.public_dict(position=None)
            (self._store_dir / f"{job.id}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            log.exception("persist agent job failed job_id=%s", job.id)

    def _load(self, job_id: str) -> AgentJob | None:
        path = self._store_dir / f"{job_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return AgentJob(
                id=data["id"],
                message=BrainMessage(
                    channel=data.get("channel") or "brain",
                    conversation_id=data.get("conversation_id") or "",
                    user_id=data.get("user_id") or "",
                    text=data.get("text_preview") or "",
                ),
                channel=data.get("channel") or "brain",
                conversation_id=data.get("conversation_id") or "",
                user_id=data.get("user_id") or "",
                trace_id=data.get("trace_id") or "",
                status=data.get("status") or "DONE",
                queued_at=data.get("queued_at") or "",
                started_at=data.get("started_at") or "",
                finished_at=data.get("finished_at") or "",
                error=data.get("error") or "",
                response=data.get("response") or {},
                last_progress=data.get("last_progress") or "",
            )
        except Exception:
            log.exception("load agent job failed job_id=%s", job_id)
            return None


agent_job_queue = AgentJobQueue()


def enqueue_brain_job(
    message: BrainMessage,
    *,
    trace_id: str = "",
    context: dict[str, Any] | None = None,
    callback: JobCallback | None = None,
) -> dict[str, Any]:
    return agent_job_queue.enqueue(message, trace_id=trace_id, context=context, callback=callback)


def get_brain_job(job_id: str) -> dict[str, Any] | None:
    return agent_job_queue.get(job_id)


def list_brain_jobs(conversation_id: str = "", limit: int = 20) -> list[dict[str, Any]]:
    return agent_job_queue.list(conversation_id=conversation_id, limit=limit)


def queue_snapshot() -> dict[str, Any]:
    return agent_job_queue.snapshot()


def clear_pending_brain_jobs(conversation_prefix: str = "") -> int:
    return agent_job_queue.clear_pending(conversation_prefix=conversation_prefix)
