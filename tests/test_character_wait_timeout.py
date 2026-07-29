from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image

from assetclaw_matting.config import settings
from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import get_connection, init_db
from assetclaw_matting.services import character_resolution
from assetclaw_matting.services.character_resolution import (
    get_run_resolutions,
    initialize_run_resolutions,
    mark_run_waiting,
    sweep_character_resolution_questions,
    try_resolve_reply,
)
from assetclaw_matting.skills import agent_ops_skills, direct_image_skills, direct_video_skills


UTC = timezone.utc


class Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now_text(self) -> str:
        return self.value.isoformat()

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def wait_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Clock, Path]:
    full_refs = tmp_path / "CharactorFull"
    emoji_refs = tmp_path / "CharactorEmoji"
    full_refs.mkdir()
    emoji_refs.mkdir()
    Image.new("RGBA", (384, 512), (20, 40, 220, 255)).save(full_refs / "huggy.png")
    Image.new("RGBA", (256, 256), (20, 40, 220, 255)).save(emoji_refs / "huggy.png")
    monkeypatch.setattr(settings, "cherry_character_full_reference_dir", full_refs)
    monkeypatch.setattr(settings, "cherry_character_emoji_reference_dir", emoji_refs)
    monkeypatch.setattr(settings, "cherry_character_reference_dir", full_refs)
    monkeypatch.setattr(settings, "character_confirmation_reminder_interval_seconds", 60)
    monkeypatch.setattr(settings, "character_confirmation_max_reminders", 2)
    monkeypatch.setattr(settings, "character_confirmation_sweep_interval_seconds", 5)
    monkeypatch.setattr(settings, "character_confirmation_lease_seconds", 30)
    init_db(tmp_path / "assetclaw.db")
    create_tables()

    runs_root = tmp_path / "runs"
    monkeypatch.setattr(direct_image_skills, "RUNS_ROOT", runs_root)
    monkeypatch.setattr(direct_video_skills, "RUNS_ROOT", runs_root)
    monkeypatch.setattr(settings, "storage_dir", tmp_path)
    clock = Clock(datetime(2026, 7, 29, 4, 0, tzinfo=UTC))
    monkeypatch.setattr(character_resolution, "_now", clock.now_text)
    return clock, runs_root


def _question(question_id: str) -> dict[str, object]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM character_resolution_questions WHERE id = ?",
            (question_id,),
        ).fetchone()
    assert row is not None
    return dict(row)


def _create_waiting_image(
    tmp_path: Path,
    *,
    run_id: str = "IMG_WAIT_ROLE",
    source_name: str = "unknown.png",
) -> tuple[dict[str, object], str]:
    state = initialize_run_resolutions(
        run_kind="direct_image",
        run_id=run_id,
        run_dir=tmp_path / "runs" / run_id,
        conversation_id="feishu:chat:user",
        chat_id="chat",
        user_id="user",
        units=[
            {
                "unit_id": f"{run_id}:image:01",
                "item_index": 1,
                "source_name": source_name,
                "evidence": [source_name],
            }
        ],
    )
    token = str(state["pending"][0]["question_token"])
    run: dict[str, object] = {
        "id": run_id,
        "status": "WAITING_CHARACTER",
        "stage": "waiting_character",
        "worker_pid": 0,
        "conversation_id": "feishu:chat:user",
        "chat_id": "chat",
        "user_id": "user",
        "images": [
            {
                "character_unit_id": f"{run_id}:image:01",
                "source_name": source_name,
                "cherry_profile": "full",
                "cherry_output_size": "384x512",
            }
        ],
        "children": {},
        "character_question": str(state["prompt"]),
        "character_resolution": {
            "question_id": state["question_id"],
            "total": 1,
            "pending": 1,
        },
        "log": [],
        "created_at": character_resolution._now(),
        "updated_at": character_resolution._now(),
    }
    direct_image_skills._save(run)
    return state, token


def _make_due(clock: Clock) -> None:
    clock.advance(int(settings.character_confirmation_reminder_interval_seconds) + 1)


def _exhaust_reminders(question_id: str, clock: Clock) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE character_resolution_questions
            SET reminder_count = ?, next_action_at = ?, lease_owner = NULL, lease_until = NULL
            WHERE id = ?
            """,
            (
                int(settings.character_confirmation_max_reminders),
                (clock.value - timedelta(seconds=1)).isoformat(),
                question_id,
            ),
        )


def test_due_reminder_is_sent_once_and_persisted(wait_env, tmp_path: Path) -> None:
    clock, _ = wait_env
    state, token = _create_waiting_image(tmp_path)
    question_id = str(state["question_id"])
    mark_run_waiting("direct_image", "IMG_WAIT_ROLE")
    _make_due(clock)
    sent: list[tuple[str, str]] = []

    def send_text(chat_id: str, text: str) -> dict[str, str]:
        sent.append((chat_id, text))
        return {"message_id": "om-reminder-1"}

    sweep_character_resolution_questions(lease_owner="test-one", send_text=send_text)
    sweep_character_resolution_questions(lease_owner="test-two", send_text=send_text)

    row = _question(question_id)
    assert row["status"] == "PENDING"
    assert row["reminder_count"] == 1
    assert row["last_reminded_at"] == clock.now_text()
    assert len(sent) == 1
    assert sent[0][0] == "chat"
    assert token in sent[0][1]
    assert "unknown.png" in sent[0][1]
    assert direct_image_skills._load("IMG_WAIT_ROLE")["status"] == "WAITING_CHARACTER"


def test_failed_reminder_delivery_is_not_counted_and_does_not_fail_early(wait_env, tmp_path: Path) -> None:
    clock, _ = wait_env
    state, _token = _create_waiting_image(tmp_path)
    question_id = str(state["question_id"])
    mark_run_waiting("direct_image", "IMG_WAIT_ROLE")
    _make_due(clock)

    def send_text(_chat_id: str, _text: str) -> dict[str, str]:
        raise TimeoutError("write timeout")

    result = sweep_character_resolution_questions(lease_owner="delivery-failure", send_text=send_text)

    row = _question(question_id)
    assert row["status"] == "PENDING"
    assert row["reminder_count"] == 0
    assert direct_image_skills._load("IMG_WAIT_ROLE")["status"] == "WAITING_CHARACTER"
    assert result.get("errors") or result.get("send_failures")


def test_absolute_deadline_fails_even_when_every_reminder_delivery_failed(wait_env, tmp_path: Path) -> None:
    clock, _ = wait_env
    state, _token = _create_waiting_image(tmp_path, run_id="IMG_DELIVERY_DEADLINE")
    question_id = str(state["question_id"])
    mark_run_waiting("direct_image", "IMG_DELIVERY_DEADLINE")

    def fail_delivery(_chat_id: str, _text: str) -> dict[str, str]:
        raise TimeoutError("Feishu unavailable")

    # Retry twice before the absolute deadline. Failed sends must not be
    # counted as delivered reminders, but they also must not create an
    # unbounded WAITING_CHARACTER task.
    _make_due(clock)
    sweep_character_resolution_questions(lease_owner="deadline-failure-one", send_text=fail_delivery)
    _make_due(clock)
    sweep_character_resolution_questions(lease_owner="deadline-failure-two", send_text=fail_delivery)
    assert _question(question_id)["reminder_count"] == 0
    assert direct_image_skills._load("IMG_DELIVERY_DEADLINE")["status"] == "WAITING_CHARACTER"

    deadline = datetime.fromisoformat(str(_question(question_id)["deadline_at"]))
    clock.value = deadline + timedelta(seconds=1)
    notices: list[str] = []
    sweep_character_resolution_questions(
        lease_owner="deadline-expiry",
        send_text=lambda _chat_id, text: notices.append(text) or {"message_id": "om-deadline-expired"},
    )

    assert _question(question_id)["status"] == "EXPIRED"
    assert _question(question_id)["reminder_count"] == 0
    assert get_run_resolutions("direct_image", "IMG_DELIVERY_DEADLINE")[0]["status"] == "EXPIRED"
    run = direct_image_skills._load("IMG_DELIVERY_DEADLINE")
    assert run["status"] == "FAILED"
    assert run["stage"] == "character_confirmation_timeout"
    assert notices and "超时" in notices[-1]


def test_empty_message_receipt_is_not_counted_as_a_delivered_reminder(wait_env, tmp_path: Path) -> None:
    clock, _ = wait_env
    state, _token = _create_waiting_image(tmp_path)
    question_id = str(state["question_id"])
    mark_run_waiting("direct_image", "IMG_WAIT_ROLE")
    _make_due(clock)

    result = sweep_character_resolution_questions(
        lease_owner="empty-receipt",
        send_text=lambda *_args: {},
    )

    assert _question(question_id)["status"] == "PENDING"
    assert _question(question_id)["reminder_count"] == 0
    assert direct_image_skills._load("IMG_WAIT_ROLE")["status"] == "WAITING_CHARACTER"
    assert result.get("errors") or result.get("send_failures")


def test_exhausted_reminders_fail_parent_and_release_active_queue(wait_env, tmp_path: Path) -> None:
    clock, _ = wait_env
    state, _token = _create_waiting_image(tmp_path)
    question_id = str(state["question_id"])
    mark_run_waiting("direct_image", "IMG_WAIT_ROLE")
    _exhaust_reminders(question_id, clock)
    notices: list[str] = []

    sweep_character_resolution_questions(
        lease_owner="expiry",
        send_text=lambda _chat_id, text: notices.append(text) or {"message_id": "om-expired"},
    )

    question = _question(question_id)
    resolution = get_run_resolutions("direct_image", "IMG_WAIT_ROLE")[0]
    run = direct_image_skills._load("IMG_WAIT_ROLE")
    assert question["status"] == "EXPIRED"
    assert question["failed_at"] == clock.now_text()
    assert resolution["status"] == "EXPIRED"
    assert run["status"] == "FAILED"
    assert run["stage"] == "character_confirmation_timeout"
    assert "角色" in run["error"] and "超时" in run["error"]
    assert run["worker_pid"] == 0
    assert notices and "失败" in notices[-1]
    assert direct_image_skills.list_runs(include_finished=False)["items"] == []
    assert agent_ops_skills._media_item_status("FAILED", run["stage"], 1, 1, 0) == "失败"


def test_final_failure_is_durable_when_failure_notice_cannot_be_sent(wait_env, tmp_path: Path) -> None:
    clock, _ = wait_env
    state, _token = _create_waiting_image(tmp_path)
    question_id = str(state["question_id"])
    mark_run_waiting("direct_image", "IMG_WAIT_ROLE")
    _exhaust_reminders(question_id, clock)

    sweep_character_resolution_questions(
        lease_owner="expiry-send-failure",
        send_text=lambda *_args: (_ for _ in ()).throw(TimeoutError("network down")),
    )

    # The business task is terminal immediately, while the question remains in
    # a durable outbox state until the final Feishu notice gets a receipt.
    assert _question(question_id)["status"] == "EXPIRING"
    assert get_run_resolutions("direct_image", "IMG_WAIT_ROLE")[0]["status"] == "EXPIRED"
    assert direct_image_skills._load("IMG_WAIT_ROLE")["status"] == "FAILED"

    clock.advance(int(settings.character_confirmation_sweep_interval_seconds) + 1)
    sweep_character_resolution_questions(
        lease_owner="expiry-send-recovery",
        send_text=lambda *_args: {"message_id": "om-final-retry"},
    )
    assert _question(question_id)["status"] == "EXPIRED"
    assert direct_image_skills._load("IMG_WAIT_ROLE")["status"] == "FAILED"


def test_two_sweepers_share_one_db_lease_and_send_one_reminder(wait_env, tmp_path: Path) -> None:
    clock, _ = wait_env
    state, _token = _create_waiting_image(tmp_path)
    question_id = str(state["question_id"])
    mark_run_waiting("direct_image", "IMG_WAIT_ROLE")
    _make_due(clock)
    start = threading.Barrier(3)
    sent: list[str] = []
    sent_lock = threading.Lock()

    def sender(_chat_id: str, text: str) -> dict[str, str]:
        with sent_lock:
            sent.append(text)
        return {"message_id": "om-race"}

    def sweep(owner: str) -> dict[str, object]:
        start.wait()
        return sweep_character_resolution_questions(lease_owner=owner, send_text=sender)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(sweep, owner) for owner in ("sweeper-a", "sweeper-b")]
        start.wait()
        for future in futures:
            future.result(timeout=10)

    assert len(sent) == 1
    assert _question(question_id)["reminder_count"] == 1


def test_reply_and_timeout_race_never_resurrects_or_mixes_terminal_state(
    wait_env,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock, _ = wait_env
    state, token = _create_waiting_image(tmp_path)
    question_id = str(state["question_id"])
    mark_run_waiting("direct_image", "IMG_WAIT_ROLE")
    _exhaust_reminders(question_id, clock)
    monkeypatch.setattr(direct_image_skills, "_start_recovery_worker", lambda _run_id: True)
    start = threading.Barrier(3)

    def resolve() -> object:
        start.wait()
        result = try_resolve_reply(
            conversation_id="feishu:chat:user",
            user_id="user",
            message_id="om-role-race",
            text=f"{token}=huggy",
        )
        for target in result.get("affected_runs") or []:
            direct_image_skills.resume_after_character_resolution(str(target["run_id"]))
        return result

    def expire() -> object:
        start.wait()
        return sweep_character_resolution_questions(
            lease_owner="expiry-race",
            send_text=lambda *_args: {"message_id": "om-expiry-race"},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(resolve), pool.submit(expire)]
        start.wait()
        results = [future.result(timeout=15) for future in futures]

    assert all(isinstance(result, dict) for result in results)
    resolution_status = get_run_resolutions("direct_image", "IMG_WAIT_ROLE")[0]["status"]
    parent_status = direct_image_skills._load("IMG_WAIT_ROLE")["status"]
    assert (resolution_status, parent_status) in {("FROZEN", "QUEUED"), ("EXPIRED", "FAILED")}
    assert _question(question_id)["status"] in {"RESOLVED", "EXPIRED"}


def test_restart_finishes_an_expiring_question_after_crash_before_parent_update(
    wait_env,
    tmp_path: Path,
) -> None:
    """The DB transition precedes JSON/Feishu I/O and must be restart-safe."""

    clock, _ = wait_env
    state, _token = _create_waiting_image(tmp_path, run_id="IMG_CRASH_EXPIRING")
    question_id = str(state["question_id"])
    mark_run_waiting("direct_image", "IMG_CRASH_EXPIRING")
    _exhaust_reminders(question_id, clock)
    claimed = character_resolution._claim_due_questions("dead-process", 1)
    assert [row["id"] for row in claimed] == [question_id]
    assert character_resolution._expire_claimed_question(question_id, "dead-process")["expired"] is True
    assert _question(question_id)["status"] == "EXPIRING"
    assert direct_image_skills._load("IMG_CRASH_EXPIRING")["status"] == "WAITING_CHARACTER"

    # Simulate process death: no parent JSON transition and no final notice.
    clock.advance(int(settings.character_confirmation_lease_seconds) + 1)
    notices: list[str] = []
    sweep_character_resolution_questions(
        lease_owner="replacement-process",
        send_text=lambda _chat_id, text: notices.append(text) or {"message_id": "om-recovered-expiry"},
    )

    assert _question(question_id)["status"] == "EXPIRED"
    assert direct_image_skills._load("IMG_CRASH_EXPIRING")["status"] == "FAILED"
    assert notices and "超时" in notices[-1]


def test_restart_backfills_old_wait_without_immediate_failure_or_duplicate_notice(
    wait_env,
    tmp_path: Path,
) -> None:
    clock, _ = wait_env
    state, _token = _create_waiting_image(tmp_path, run_id="IMG_OLD_WAIT")
    question_id = str(state["question_id"])
    clock.advance(7 * 24 * 60 * 60)
    sent: list[str] = []
    sender = lambda _chat_id, text: sent.append(text) or {"message_id": "om-old-wait"}

    first_recovery = direct_image_skills.recover_incomplete_runs()
    sweep_character_resolution_questions(lease_owner="restart-one", send_text=sender)
    second_recovery = direct_image_skills.recover_incomplete_runs()
    sweep_character_resolution_questions(lease_owner="restart-two", send_text=sender)

    question = _question(question_id)
    assert "IMG_OLD_WAIT" in first_recovery["waiting_character"]
    assert "IMG_OLD_WAIT" in second_recovery["waiting_character"]
    assert question["status"] == "PENDING"
    assert question["waiting_started_at"] == clock.now_text()
    assert question["reminder_count"] == 1
    assert len(sent) == 1
    assert direct_image_skills._load("IMG_OLD_WAIT")["status"] == "WAITING_CHARACTER"


@pytest.mark.parametrize(
    ("module", "run_id", "media_key"),
    [
        (direct_image_skills, "IMG_TIMEOUT_CAS", "images"),
        (direct_video_skills, "VID_TIMEOUT_CAS", "videos"),
    ],
)
def test_parent_timeout_transition_is_cas_and_never_overwrites_cancel(
    wait_env,
    module,
    run_id: str,
    media_key: str,
) -> None:
    run = {
        "id": run_id,
        "status": "WAITING_CHARACTER",
        "stage": "waiting_character",
        "worker_pid": 0,
        media_key: [],
        "children": {},
        "character_question": "question",
        "character_resolution": {"pending": 1},
        "log": [],
    }
    module._save(run)
    result = module.fail_character_confirmation_timeout(run_id, "等待角色确认超时")
    saved = module._load(run_id)
    assert result["ok"] is True
    assert saved["status"] == "FAILED"
    assert saved["stage"] == "character_confirmation_timeout"
    assert saved["worker_pid"] == 0

    canceled = dict(saved)
    canceled["status"] = "CANCELED"
    canceled["stage"] = "canceled"
    module._save(canceled)
    second = module.fail_character_confirmation_timeout(run_id, "late timeout")
    assert second.get("changed") is False or second.get("status") == "CANCELED"
    assert module._load(run_id)["status"] == "CANCELED"


def test_waiting_character_has_explicit_user_facing_status() -> None:
    assert agent_ops_skills._media_item_status("WAITING_CHARACTER", "waiting_character", 1, 1, 0) == "等待确认角色"
