from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from PIL import Image

from assetclaw_matting.config import settings
from assetclaw_matting.db.sqlite import get_connection
from assetclaw_matting.services.character_identity import (
    CharacterProfileError,
    CharacterReference,
    CharacterReferenceCatalog,
    CharacterReferenceVariant,
    CharacterRegistry,
    CharacterRegistryError,
    CharacterResolutionStatus,
    normalize_name_tokens,
    resolve_character_profile,
)


TOKEN_PATTERN = r"(?<![A-Za-z0-9])(C-[A-F0-9]{8,16})(?![A-F0-9])"
QUESTION_TOKEN_RE = re.compile(TOKEN_PATTERN, re.IGNORECASE)
ASSIGNMENT_RE = re.compile(
    TOKEN_PATTERN + r"\s*\]?\s*(?:=|:|：|是)\s*([\w\-]+)",
    re.IGNORECASE,
)
RUN_KINDS = {"direct_image", "direct_video"}
REFERENCE_CATALOG_MODEL = "dual-profile-v1"
REFERENCE_PROFILES = ("full", "half")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _catalog() -> CharacterReferenceCatalog:
    return CharacterReferenceCatalog.discover(
        settings.cherry_character_full_reference_dir,
        settings.cherry_character_emoji_reference_dir,
    )


def _registry() -> CharacterRegistry:
    """Return the union identity registry; asset selection happens per profile."""

    return _catalog().identity_registry


def _catalog_revision(registry: CharacterRegistry | CharacterReferenceCatalog) -> str:
    if isinstance(registry, CharacterReferenceCatalog):
        return registry.catalog_revision
    payload = "\n".join(f"{item.canonical_id}:{item.sha256}" for item in registry.references)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _token_for_unit(unit_id: str) -> str:
    return "C-" + hashlib.sha256(unit_id.encode("utf-8")).hexdigest()[:12].upper()


def initialize_run_resolutions(
    *,
    run_kind: str,
    run_id: str,
    run_dir: str | Path,
    conversation_id: str,
    chat_id: str,
    user_id: str,
    units: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Create immutable per-input character bindings before background work starts.

    ``units`` are already grouped by business identity. A video is one unit; a
    ZIP/directory sequence is one unit shared by all frames; loose images are
    separate units. The caller stores the returned ``unit_id`` on each item.
    """

    kind = str(run_kind or "").strip().lower()
    if kind not in RUN_KINDS:
        raise ValueError(f"unsupported character-resolution run kind: {run_kind}")
    catalog = _catalog()
    registry = catalog.identity_registry
    revision = catalog.catalog_revision
    rows: list[dict[str, Any]] = []
    for ordinal, raw in enumerate(units, start=1):
        unit_id = str(raw.get("unit_id") or f"{run_id}:item:{ordinal:02d}")
        evidence = _clean_evidence(raw.get("evidence") or [raw.get("source_name") or ""])
        resolution = _resolve_evidence(registry, evidence)
        identity_reference = resolution.reference
        character_id = identity_reference.canonical_id if identity_reference else ""
        available_profiles = list(catalog.available_profiles(character_id)) if character_id else []
        row = {
                "unit_id": unit_id,
                "run_kind": kind,
                "run_id": run_id,
                "run_dir": str(Path(run_dir).resolve()),
                "item_index": int(raw.get("item_index") or ordinal),
                "group_key": str(raw.get("group_key") or unit_id),
                "conversation_id": str(conversation_id or f"local:{run_id}"),
                "chat_id": str(chat_id or ""),
                "user_id": str(user_id or "local"),
                "source_name": str(raw.get("source_name") or unit_id),
                "question_token": _token_for_unit(unit_id),
                "status": "FROZEN" if identity_reference else "PENDING",
                "character_id": character_id,
                "resolution_method": "filename" if identity_reference else "",
                "confidence": 1.0 if identity_reference else 0.0,
                "evidence_json": json.dumps(
                    {
                        "inputs": evidence,
                        "result": resolution.to_dict(),
                        "reference_catalog_model": REFERENCE_CATALOG_MODEL,
                        "reference_profiles_available": available_profiles,
                    },
                    ensure_ascii=False,
                ),
                "catalog_revision": revision,
                "reference_source_path": "",
                "reference_snapshot_path": "",
                "reference_sha256": "",
                "_reference_snapshots": {},
            }
        if identity_reference:
            snapshots = _freeze_available_references(row, catalog, character_id)
            primary = _primary_snapshot(snapshots)
            row["_reference_snapshots"] = snapshots
            row["reference_source_path"] = primary["reference_source_path"]
            row["reference_snapshot_path"] = primary["reference_snapshot_path"]
            row["reference_sha256"] = primary["reference_sha256"]
        rows.append(row)

    question_id = "ROLE_" + uuid.uuid4().hex[:12].upper() if any(row["status"] == "PENDING" for row in rows) else ""
    now = _now()
    with get_connection() as conn:
        if question_id:
            conn.execute(
                """
                INSERT INTO character_resolution_questions
                    (id, conversation_id, chat_id, user_id, run_kind, run_id, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)
                """,
                (
                    question_id,
                    rows[0]["conversation_id"],
                    rows[0]["chat_id"],
                    rows[0]["user_id"],
                    kind,
                    run_id,
                    now,
                    now,
                ),
            )
        for row in rows:
            conn.execute(
                """
                INSERT INTO character_resolutions
                    (unit_id, run_kind, run_id, run_dir, item_index, group_key,
                     conversation_id, chat_id, user_id, source_name, question_id,
                     question_token, status, character_id, resolution_method,
                     confidence, evidence_json, catalog_revision,
                     reference_source_path, reference_snapshot_path, reference_sha256, version,
                     created_at, updated_at, resolved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    row["unit_id"],
                    kind,
                    run_id,
                    row["run_dir"],
                    row["item_index"],
                    row["group_key"],
                    row["conversation_id"],
                    row["chat_id"],
                    row["user_id"],
                    row["source_name"],
                    question_id or None,
                    row["question_token"],
                    row["status"],
                    row["character_id"],
                    row["resolution_method"],
                    row["confidence"],
                    row["evidence_json"],
                    revision,
                    row["reference_source_path"],
                    row["reference_snapshot_path"],
                    row["reference_sha256"],
                    now,
                    now,
                    now if row["status"] == "FROZEN" else None,
                ),
            )
            for snapshot in row.get("_reference_snapshots", {}).values():
                _upsert_reference_snapshot(conn, row["unit_id"], snapshot, now)
    current = get_run_resolutions(kind, run_id)
    return {
        "question_id": question_id,
        "items": current,
        "pending": [item for item in current if item["status"] == "PENDING"],
        "prompt": format_pending_prompt(current),
    }


def get_run_resolutions(run_kind: str, run_id: str) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM character_resolutions
            WHERE run_kind = ? AND run_id = ?
            ORDER BY item_index, unit_id
            """,
            (run_kind, run_id),
        ).fetchall()
    return [dict(row) for row in rows]


def get_run_reference_snapshots(run_kind: str, run_id: str) -> list[dict[str, Any]]:
    """Return immutable per-profile references for one persisted business run."""

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT s.*
            FROM character_reference_snapshots s
            JOIN character_resolutions r ON r.unit_id = s.unit_id
            WHERE r.run_kind = ? AND r.run_id = ?
            ORDER BY r.item_index, s.profile
            """,
            (run_kind, run_id),
        ).fetchall()
    return [dict(row) for row in rows]


def get_pending_for_actor(conversation_id: str, user_id: str) -> list[dict[str, Any]]:
    _prune_terminal_pending_for_actor(conversation_id, user_id)
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT r.* FROM character_resolutions r
            JOIN character_resolution_questions q ON q.id = r.question_id
            WHERE r.conversation_id = ? AND r.user_id = ?
              AND r.status = 'PENDING' AND q.status = 'PENDING'
            ORDER BY q.created_at DESC, q.id DESC, r.item_index, r.unit_id
            """,
            (conversation_id, user_id),
        ).fetchall()
    return [dict(row) for row in rows]


def _prune_terminal_pending_for_actor(conversation_id: str, user_id: str) -> None:
    """Self-heal questions left behind by an older worker or interrupted deploy."""

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT r.run_kind, r.run_id, r.run_dir
            FROM character_resolutions r
            JOIN character_resolution_questions q ON q.id = r.question_id
            WHERE r.conversation_id = ? AND r.user_id = ?
              AND r.status = 'PENDING' AND q.status = 'PENDING'
            """,
            (conversation_id, user_id),
        ).fetchall()
    for raw in rows:
        status_path = Path(str(raw["run_dir"] or "")) / "status.json"
        if not status_path.is_file():
            continue
        try:
            payload = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        status = str(payload.get("status") or "").upper()
        kind = str(raw["run_kind"] or "")
        run_id = str(raw["run_id"] or "")
        if status == "CANCELED":
            cancel_run_resolutions(kind, run_id)
        elif status in {"FAILED", "BLOCKED", "DONE_WITH_ERRORS"}:
            # A processing failure must not consume the user's role question.
            # The task may be recovered from persisted mattes/batches, and a
            # role reply received during the outage must still be durable.
            continue
        elif status == "DONE":
            # A completed run cannot legitimately retain a blocking question.
            cancel_run_resolutions(kind, run_id)


def format_pending_prompt(items: Iterable[dict[str, Any]]) -> str:
    pending = [item for item in items if str(item.get("status") or "") == "PENDING"]
    if not pending:
        return ""
    available = "、".join(item.canonical_id for item in _registry().references)
    lines = [f"有 {len(pending)} 个任务需要确认角色；抠图会继续，后处理会等待确认："]
    for item in pending:
        lines.append(f"[{item['question_token']}] {item['source_name']}")
    lines.append("请回复：" + " ".join(f"{item['question_token']}=角色名" for item in pending))
    lines.append("可选角色：" + available)
    return "\n".join(lines)


def try_resolve_reply(
    *,
    conversation_id: str,
    user_id: str,
    message_id: str,
    text: str,
) -> dict[str, Any]:
    pending = get_pending_for_actor(conversation_id, user_id)
    if not pending:
        tokens = [match.upper() for match in QUESTION_TOKEN_RE.findall(str(text or ""))]
        if tokens and _has_expired_tokens(conversation_id, user_id, tokens):
            return {
                "handled": True,
                "ok": False,
                "message": "任务已因等待角色确认超时失败，请重新提交素材发起任务。",
                "affected_runs": [],
            }
        # Do not let the LLM fabricate a stale "role mapped" acknowledgement
        # for a bare canonical role name when no DB row was actually updated.
        if _exact_answer_reference(_registry(), str(text or "").strip()):
            return {
                "handled": True,
                "ok": False,
                "message": "当前没有待确认的角色任务，本次未执行任何绑定。",
                "affected_runs": [],
            }
        return {"handled": False}
    catalog = _catalog()
    registry = catalog.identity_registry
    assignments = _parse_assignments(text)
    if not assignments:
        if QUESTION_TOKEN_RE.search(str(text or "")):
            return {
                "handled": True,
                "ok": False,
                "message": "角色编号格式已识别，但映射写法不完整。请按 C-编号=角色名 回复。\n" + format_pending_prompt(pending),
            }
        bare_reference = _exact_answer_reference(registry, str(text or "").strip())
        latest_question_id = str(pending[0].get("question_id") or "") if pending else ""
        latest_pending = [
            item for item in pending
            if str(item.get("question_id") or "") == latest_question_id
        ]
        if bare_reference and len(latest_pending) == 1:
            # A natural one-word reply belongs to the latest active question,
            # not to stale/older work in the same chat.  Explicit C-token
            # assignments remain available for resolving any older question.
            assignments = {
                str(latest_pending[0]["question_token"]).upper(): bare_reference.canonical_id
            }
        elif bare_reference and len(latest_pending) > 1:
            return {
                "handled": True,
                "ok": False,
                "message": "最新上传中有多个项目待确认，请带上每项前面的 C-编号，避免角色错位。\n"
                + format_pending_prompt(latest_pending),
            }
        else:
            return {"handled": False}

    pending_by_token = {str(item["question_token"]).upper(): item for item in pending}
    validated: dict[str, CharacterReference] = {}
    errors: list[str] = []
    for token, answer in assignments.items():
        item = pending_by_token.get(token)
        if not item:
            errors.append(f"{token} 不是当前会话中的待确认任务")
            continue
        reference = _exact_answer_reference(registry, answer)
        if not reference:
            errors.append(f"{token} 的角色“{answer}”不存在或不唯一")
            continue
        previous = validated.get(token)
        if previous and previous.canonical_id != reference.canonical_id:
            errors.append(f"{token} 同时指定了两个不同角色")
            continue
        validated[token] = reference
    if errors:
        return {"handled": True, "ok": False, "message": "；".join(errors) + "。本次没有修改任何绑定。"}

    snapshots: dict[str, dict[str, dict[str, Any]]] = {}
    evidence_updates: dict[str, str] = {}
    for token, reference in validated.items():
        row = pending_by_token[token]
        snapshot_row = {**row, "character_id": reference.canonical_id}
        snapshots[token] = _freeze_available_references(
            snapshot_row,
            catalog,
            reference.canonical_id,
        )
        evidence_updates[token] = _updated_evidence_json(
            row.get("evidence_json"),
            reference_profiles_available=list(snapshots[token]),
        )

    now = _now()
    affected: set[tuple[str, str]] = set()
    with get_connection() as conn:
        # Serialize the final status check with the timeout sweeper. Whichever
        # obtains the write lock first wins; the loser returns a normal,
        # user-facing result instead of surfacing a 500/concurrency exception.
        conn.execute("BEGIN IMMEDIATE")
        already_frozen: set[str] = set()
        for token, reference in validated.items():
            row = pending_by_token[token]
            current = conn.execute(
                "SELECT status, character_id FROM character_resolutions WHERE unit_id = ?",
                (row["unit_id"],),
            ).fetchone()
            status = str(current["status"] if current is not None else "MISSING")
            if status == "EXPIRED":
                return {
                    "handled": True,
                    "ok": False,
                    "message": "任务已因等待角色确认超时失败，请重新提交素材发起任务。",
                    "affected_runs": [],
                }
            if status == "FROZEN":
                if str(current["character_id"] or "") == reference.canonical_id:
                    already_frozen.add(token)
                    continue
                return {
                    "handled": True,
                    "ok": False,
                    "message": f"{token} 已由另一条回复确认角色，本次未覆盖已有绑定。",
                    "affected_runs": [],
                }
            if status != "PENDING":
                return {
                    "handled": True,
                    "ok": False,
                    "message": f"{token} 对应任务已经结束，无法再修改角色。",
                    "affected_runs": [],
                }
        for token, reference in validated.items():
            if token in already_frozen:
                continue
            row = pending_by_token[token]
            primary = _primary_snapshot(snapshots[token])
            cursor = conn.execute(
                """
                UPDATE character_resolutions
                SET status = 'FROZEN', character_id = ?, resolution_method = 'user',
                    confidence = 1.0, catalog_revision = ?, reference_source_path = ?,
                    reference_snapshot_path = ?, reference_sha256 = ?, evidence_json = ?,
                    resolved_by_message_id = ?, version = version + 1,
                    resolved_at = ?, updated_at = ?
                WHERE unit_id = ? AND status = 'PENDING'
                """,
                (
                    reference.canonical_id,
                    catalog.catalog_revision,
                    primary["reference_source_path"],
                    primary["reference_snapshot_path"],
                    primary["reference_sha256"],
                    evidence_updates[token],
                    message_id,
                    now,
                    now,
                    row["unit_id"],
                ),
            )
            if cursor.rowcount != 1:
                return {
                    "handled": True,
                    "ok": False,
                    "message": "角色确认状态刚刚发生变化，请查看最新任务状态后重试。",
                    "affected_runs": [],
                }
            for snapshot in snapshots[token].values():
                _upsert_reference_snapshot(conn, row["unit_id"], snapshot, now)
            affected.add((str(row["run_kind"]), str(row["run_id"])))

    _close_resolved_questions()
    remaining = get_pending_for_actor(conversation_id, user_id)
    names = "、".join(f"{token}={reference.canonical_id}" for token, reference in validated.items())
    message = f"角色已对应：{names}。"
    if remaining:
        message += "\n" + format_pending_prompt(remaining)
    else:
        message += " 抠图完成后会直接继续后处理。"
    return {
        "handled": True,
        "ok": True,
        "message": message,
        "affected_runs": [{"run_kind": kind, "run_id": run_id} for kind, run_id in sorted(affected)],
        "remaining": remaining,
    }


def bind_run_items(run_kind: str, run_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    """Bind each item to the frozen reference matching its concrete output profile."""

    reconciliation = reconcile_resolved_units(run_kind, run_id)
    if not reconciliation.get("ok"):
        first = (reconciliation.get("errors") or [{}])[0]
        raise RuntimeError(f"character reference snapshot could not be recovered: {first.get('error') or 'unknown error'}")
    rows = {str(row["unit_id"]): row for row in get_run_resolutions(run_kind, run_id)}
    snapshot_rows = {
        (str(row["unit_id"]), str(row["profile"])): row
        for row in get_run_reference_snapshots(run_kind, run_id)
    }
    missing: list[str] = []
    missing_profiles: list[str] = []
    verified_snapshots: set[tuple[str, str, str, str]] = set()
    for item in items:
        unit_id = str(item.get("character_unit_id") or "")
        row = rows.get(unit_id)
        if not row or str(row.get("status") or "") != "FROZEN":
            missing.append(str(item.get("source_name") or item.get("name") or unit_id or "unknown"))
            continue
        item.update(
            {
                "character_id": str(row["character_id"]),
                "character_name": str(row["character_id"]),
                "character_resolution_method": str(row.get("resolution_method") or ""),
            }
        )
        profile = _profile_for_item(item)
        if profile is None:
            missing_profiles.append(str(item.get("source_name") or item.get("name") or unit_id or "unknown"))
            continue
        snapshot_row = snapshot_rows.get((unit_id, profile))
        if snapshot_row is None and not _uses_dual_profile_catalog(row):
            snapshot_row = _promote_legacy_snapshot(row, profile)
            if snapshot_row is not None:
                snapshot_rows[(unit_id, profile)] = snapshot_row
        if snapshot_row is None:
            # Missing the exact output profile is an expected catalog gap, not
            # a processing failure.  The direct image/video caller must fail
            # closed for postprocessing and deliver its verified matte-only
            # bundle instead of substituting the other profile.
            missing_profiles.append(_missing_profile_message(str(row["character_id"]), profile))
            continue
        snapshot = Path(str(snapshot_row.get("reference_snapshot_path") or ""))
        expected_sha = str(snapshot_row.get("reference_sha256") or "").lower()
        verification_key = (unit_id, profile, str(snapshot), expected_sha)
        if verification_key not in verified_snapshots:
            _verify_profile_snapshot(snapshot, expected_sha, profile, unit_id)
            verified_snapshots.add(verification_key)
        item.update(
            {
                "character_reference_variant": profile,
                "color_reference_path": str(snapshot),
                "color_reference_sha256": expected_sha,
                "position_alignment_reference_path": str(snapshot),
                "position_alignment_reference_sha256": expected_sha,
                "color_correction_status": "READY",
                "position_alignment_enabled": True,
            }
        )
    return {
        "ready": not missing and not missing_profiles,
        "missing": missing,
        "missing_profiles": missing_profiles,
        "items": items,
    }


def all_run_units_frozen(run_kind: str, run_id: str) -> bool:
    rows = get_run_resolutions(run_kind, run_id)
    return bool(rows) and all(str(row.get("status") or "") == "FROZEN" for row in rows)


def mark_run_waiting(
    run_kind: str,
    run_id: str,
    *,
    immediate_if_uninitialized: bool = False,
) -> bool:
    """Persist the start of the user-confirmation gate without resetting it.

    New code calls this when matting reaches ``WAITING_CHARACTER`` and starts
    the normal reminder interval.  Recovery/sidecar scans pass
    ``immediate_if_uninitialized=True`` so tasks created by an older process
    receive one prompt immediately, but still get the complete new reminder
    cycle from the deployment time onward.
    """

    kind = str(run_kind or "").strip().lower()
    if kind not in RUN_KINDS:
        return False
    now = _now()
    first_action = now if immediate_if_uninitialized else _after(now, _reminder_interval_seconds())
    deadline = _after(now, _reminder_interval_seconds() * (_max_reminders() + 1))
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE character_resolution_questions
            SET waiting_started_at = COALESCE(waiting_started_at, ?),
                next_action_at = COALESCE(next_action_at, ?),
                deadline_at = COALESCE(deadline_at, ?),
                reminder_count = COALESCE(reminder_count, 0),
                updated_at = ?
            WHERE run_kind = ? AND run_id = ? AND status = 'PENDING'
              AND (waiting_started_at IS NULL OR next_action_at IS NULL OR deadline_at IS NULL)
            """,
            (now, first_action, deadline, now, kind, str(run_id or "")),
        )
    return cursor.rowcount > 0


def bootstrap_waiting_character_runs() -> dict[str, Any]:
    """Discover WAITING_CHARACTER JSON states created by pre-update workers."""

    discovered: list[dict[str, str]] = []
    resumed: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    roots = (
        ("direct_image", Path(settings.storage_dir) / "direct_image_runs", "IMG_*"),
        ("direct_video", Path(settings.storage_dir) / "direct_video_runs", "VID_*"),
    )
    for run_kind, root, pattern in roots:
        if not root.is_dir():
            continue
        for status_path in root.glob(f"{pattern}/status.json"):
            try:
                payload = json.loads(status_path.read_text(encoding="utf-8"))
                if str(payload.get("status") or "").upper() != "WAITING_CHARACTER":
                    continue
                run_id = str(payload.get("id") or status_path.parent.name)
                reconcile_pending_evidence(run_kind, run_id)
                if all_run_units_frozen(run_kind, run_id):
                    result = _resume_waiting_run(run_kind, run_id)
                    if result.get("ok"):
                        resumed.append({"run_kind": run_kind, "run_id": run_id})
                    continue
                if mark_run_waiting(run_kind, run_id, immediate_if_uninitialized=True):
                    discovered.append({"run_kind": run_kind, "run_id": run_id})
            except Exception as exc:
                errors.append({"path": str(status_path), "error": str(exc)})
    return {"ok": not errors, "discovered": discovered, "resumed": resumed, "errors": errors}


def sweep_character_resolution_questions(
    *,
    lease_owner: str = "",
    send_text: Callable[[str, str], Any] | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Send due reminders and terminally fail unanswered character gates.

    Database leases and state transitions are committed before/after network
    calls, never while a SQLite transaction is open.  This keeps Feishu
    latency away from task persistence and lets a sidecar and a future
    in-process monitor coexist safely.
    """

    owner = str(lease_owner or f"character-monitor-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    sender = send_text or _send_text_to_chat
    claimed = _claim_due_questions(owner, max(1, min(int(limit or 20), 200)))
    reminded: list[str] = []
    expired: list[str] = []
    skipped: list[str] = []
    errors: list[dict[str, str]] = []
    max_reminders = _max_reminders()

    for question in claimed:
        question_id = str(question["id"])
        try:
            recovering_expiration = str(question.get("status") or "") == "EXPIRING"
            pending = (
                _get_expired_for_question(question_id)
                if recovering_expiration
                else _get_pending_for_question(question_id)
            )
            if not pending:
                if recovering_expiration:
                    _complete_expiration(question_id, owner, None)
                else:
                    _resolve_empty_claim(question_id, owner)
                skipped.append(question_id)
                continue

            reminder_count = int(question.get("reminder_count") or 0)
            deadline_reached = _is_due(str(question.get("deadline_at") or ""), _now())
            if not recovering_expiration and not deadline_reached and reminder_count < max_reminders:
                ordinal = reminder_count + 1
                message = _format_reminder_message(pending, ordinal, max_reminders)
                try:
                    receipt = sender(str(question.get("chat_id") or ""), message)
                    if not _receipt_message_id(receipt):
                        raise RuntimeError("Feishu reminder returned no message_id")
                except Exception as exc:
                    _release_question_lease(question_id, owner, retry=True)
                    errors.append({"question_id": question_id, "phase": "reminder", "error": str(exc)})
                    continue
                if not _complete_reminder(question_id, owner, receipt):
                    skipped.append(question_id)
                    continue
                reminded.append(question_id)
                continue

            expiration = (
                {"expired": True, "count": len(pending)}
                if recovering_expiration
                else _expire_claimed_question(question_id, owner)
            )
            if not expiration.get("expired"):
                skipped.append(question_id)
                continue
            reason = (
                f"等待角色确认超时：已提醒 {max_reminders} 次仍未收到有效回复；"
                "抠图结果已保留，但校色和位置矫正未执行。"
            )
            dispatch = _fail_waiting_run(
                str(question.get("run_kind") or ""),
                str(question.get("run_id") or ""),
                reason,
            )
            final_message = _format_timeout_message(question, pending, reason)
            receipt: Any = None
            try:
                receipt = sender(str(question.get("chat_id") or ""), final_message)
                if not _receipt_message_id(receipt):
                    raise RuntimeError("Feishu timeout notice returned no message_id")
            except Exception as exc:
                errors.append({"question_id": question_id, "phase": "timeout_notice", "error": str(exc)})
                _release_question_lease(question_id, owner, retry=True)
                continue
            _complete_expiration(question_id, owner, receipt)
            expired.append(question_id)
            if not dispatch.get("ok"):
                errors.append(
                    {
                        "question_id": question_id,
                        "phase": "run_timeout",
                        "error": str(dispatch.get("error") or "run was no longer waiting"),
                    }
                )
        except Exception as exc:
            _release_question_lease(question_id, owner, retry=True)
            errors.append({"question_id": question_id, "phase": "sweep", "error": str(exc)})

    return {
        "ok": not errors,
        "claimed": len(claimed),
        "reminded": reminded,
        "expired": expired,
        "skipped": skipped,
        "errors": errors,
    }


def cancel_run_resolutions(run_kind: str, run_id: str) -> None:
    now = _now()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE character_resolutions
            SET status = 'CANCELLED', version = version + 1, updated_at = ?
            WHERE run_kind = ? AND run_id = ? AND status IN ('PENDING', 'RESOLVED', 'FROZEN')
            """,
            (now, run_kind, run_id),
        )
        conn.execute(
            """
            UPDATE character_resolution_questions
            SET status = 'CANCELLED', next_action_at = NULL,
                lease_owner = NULL, lease_until = NULL, updated_at = ?
            WHERE run_kind = ? AND run_id = ? AND status IN ('PENDING', 'EXPIRING')
            """,
            (now, run_kind, run_id),
        )


def reactivate_run_resolutions(run_kind: str, run_id: str) -> dict[str, Any]:
    """Restore role bindings and unanswered questions for an in-place rerun."""

    now = _now()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM character_resolutions
            WHERE run_kind = ? AND run_id = ?
            ORDER BY item_index, unit_id
            """,
            (run_kind, run_id),
        ).fetchall()
        if not rows:
            return {
                "found": False,
                "question_id": "",
                "total": 0,
                "pending": 0,
                "items": [],
                "prompt": "",
            }

        question_ids = {str(row["question_id"] or "") for row in rows if row["question_id"]}
        for row in rows:
            character_id = str(row["character_id"] or "").strip()
            restored_status = "FROZEN" if character_id else "PENDING"
            conn.execute(
                """
                UPDATE character_resolutions
                SET status = ?, version = version + 1, updated_at = ?,
                    resolved_at = CASE WHEN ? = 'FROZEN'
                        THEN COALESCE(resolved_at, ?) ELSE NULL END
                WHERE unit_id = ?
                """,
                (restored_status, now, restored_status, now, str(row["unit_id"])),
            )

        pending_count = sum(1 for row in rows if not str(row["character_id"] or "").strip())
        question_status = "PENDING" if pending_count else "RESOLVED"
        for question_id in question_ids:
            conn.execute(
                """
                UPDATE character_resolution_questions
                SET status = ?, outbound_message_id = NULL,
                    waiting_started_at = NULL, next_action_at = NULL,
                    deadline_at = NULL, reminder_count = 0,
                    last_reminded_at = NULL, lease_owner = NULL,
                    lease_until = NULL, failed_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (question_status, now, question_id),
            )

    current = get_run_resolutions(run_kind, run_id)
    pending = [item for item in current if str(item.get("status") or "") == "PENDING"]
    return {
        "found": True,
        "question_id": next(iter(question_ids), "") if pending else "",
        "total": len(current),
        "pending": len(pending),
        "items": current,
        "prompt": format_pending_prompt(current),
    }


def fail_run_resolutions(run_kind: str, run_id: str) -> None:
    """Close role questions when their owning business run fails terminally."""

    now = _now()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE character_resolutions
            SET status = 'FAILED', version = version + 1, updated_at = ?
            WHERE run_kind = ? AND run_id = ? AND status IN ('PENDING', 'RESOLVED')
            """,
            (now, run_kind, run_id),
        )
        conn.execute(
            """
            UPDATE character_resolution_questions
            SET status = 'FAILED', next_action_at = NULL,
                lease_owner = NULL, lease_until = NULL, failed_at = COALESCE(failed_at, ?),
                updated_at = ?
            WHERE run_kind = ? AND run_id = ? AND status IN ('PENDING', 'EXPIRING')
            """,
            (now, now, run_kind, run_id),
        )


def reopen_failed_run_resolutions(run_kind: str, run_id: str) -> int:
    """Reopen role confirmation after a recoverable technical run failure.

    A pipeline failure may close an otherwise valid role question.  When the
    pipeline is subsequently recovered from verified persisted output, the
    user must not be left with a terminal FAILED question that cannot accept
    the original confirmation token.
    """

    now = _now()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE character_resolutions
            SET status = 'PENDING', version = version + 1, updated_at = ?
            WHERE run_kind = ? AND run_id = ? AND status = 'FAILED'
            """,
            (now, run_kind, run_id),
        )
        reopened = int(cursor.rowcount or 0)
        if reopened:
            conn.execute(
                """
                UPDATE character_resolution_questions
                SET status = 'PENDING', next_action_at = NULL,
                    lease_owner = NULL, lease_until = NULL, failed_at = NULL,
                    updated_at = ?
                WHERE run_kind = ? AND run_id = ? AND status = 'FAILED'
                """,
                (now, run_kind, run_id),
            )
    return reopened


def reopen_failed_run_resolutions(run_kind: str, run_id: str) -> int:
    """Reopen role confirmation after a recoverable technical run failure.

    A pipeline failure may close an otherwise valid role question.  When the
    pipeline is subsequently recovered from verified persisted output, the
    user must not be left with a terminal FAILED question that cannot accept
    the original confirmation token.
    """

    now = _now()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE character_resolutions
            SET status = 'PENDING', version = version + 1, updated_at = ?
            WHERE run_kind = ? AND run_id = ? AND status = 'FAILED'
            """,
            (now, run_kind, run_id),
        )
        reopened = int(cursor.rowcount or 0)
        if reopened:
            conn.execute(
                """
                UPDATE character_resolution_questions
                SET status = 'PENDING', next_action_at = NULL,
                    lease_owner = NULL, lease_until = NULL, failed_at = NULL,
                    updated_at = ?
                WHERE run_kind = ? AND run_id = ? AND status = 'FAILED'
                """,
                (now, run_kind, run_id),
            )
    return reopened


def _freeze_unit(unit_id: str) -> dict[str, Any]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM character_resolutions WHERE unit_id = ?", (unit_id,)).fetchone()
    if not row:
        raise KeyError(f"character resolution not found: {unit_id}")
    item = dict(row)
    if str(item.get("status") or "") == "FROZEN":
        return item
    if str(item.get("status") or "") != "RESOLVED":
        raise RuntimeError(f"character resolution is not ready to freeze: {unit_id}")
    catalog = _catalog()
    snapshots = _freeze_available_references(item, catalog, str(item.get("character_id") or ""))
    primary = _primary_snapshot(snapshots)
    evidence_json = _updated_evidence_json(
        item.get("evidence_json"),
        reference_profiles_available=list(snapshots),
    )
    now = _now()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE character_resolutions
            SET status = 'FROZEN', catalog_revision = ?, reference_source_path = ?,
                reference_snapshot_path = ?, reference_sha256 = ?, evidence_json = ?,
                version = version + 1, updated_at = ?
            WHERE unit_id = ? AND status = 'RESOLVED'
            """,
            (
                catalog.catalog_revision,
                primary["reference_source_path"],
                primary["reference_snapshot_path"],
                primary["reference_sha256"],
                evidence_json,
                now,
                unit_id,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError(f"character resolution freeze race: {unit_id}")
        for snapshot in snapshots.values():
            _upsert_reference_snapshot(conn, unit_id, snapshot, now)
    item["status"] = "FROZEN"
    item.update(primary)
    return item


def reconcile_resolved_units(run_kind: str = "", run_id: str = "") -> dict[str, Any]:
    """Repair legacy/interrupted RESOLVED rows without asking the user again."""

    clauses = ["status = 'RESOLVED'"]
    values: list[Any] = []
    if run_kind:
        clauses.append("run_kind = ?")
        values.append(run_kind)
    if run_id:
        clauses.append("run_id = ?")
        values.append(run_id)
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT unit_id FROM character_resolutions WHERE " + " AND ".join(clauses),
            values,
        ).fetchall()
    repaired: list[str] = []
    errors: list[dict[str, str]] = []
    for row in rows:
        unit_id = str(row["unit_id"])
        try:
            _freeze_unit(unit_id)
            repaired.append(unit_id)
        except Exception as exc:
            errors.append({"unit_id": unit_id, "error": str(exc)})
    return {"ok": not errors, "repaired": repaired, "errors": errors}


def reconcile_pending_evidence(run_kind: str, run_id: str) -> dict[str, Any]:
    """Re-evaluate persisted filename evidence after matcher improvements.

    Only a unique registry match is frozen.  This rescues work created by an
    older process without guessing, repeating matting, or asking for a role
    that is already present in the archive name.
    """

    catalog = _catalog()
    repaired: list[str] = []
    skipped: list[str] = []
    errors: list[dict[str, str]] = []
    for row in get_run_resolutions(run_kind, run_id):
        if str(row.get("status") or "") != "PENDING":
            continue
        unit_id = str(row.get("unit_id") or "")
        try:
            evidence_payload = json.loads(str(row.get("evidence_json") or "{}"))
            evidence = _clean_evidence(
                evidence_payload.get("inputs") or [row.get("source_name") or ""]
            )
            resolution = _resolve_evidence(catalog.identity_registry, evidence)
            reference = resolution.reference
            if reference is None:
                skipped.append(unit_id)
                continue
            snapshot_row = {**row, "character_id": reference.canonical_id}
            snapshots = _freeze_available_references(
                snapshot_row,
                catalog,
                reference.canonical_id,
            )
            primary = _primary_snapshot(snapshots)
            evidence_json = _updated_evidence_json(
                row.get("evidence_json"),
                reference_profiles_available=list(snapshots),
            )
            now = _now()
            with get_connection() as conn:
                cursor = conn.execute(
                    """
                    UPDATE character_resolutions
                    SET status = 'FROZEN', character_id = ?,
                        resolution_method = 'filename_reconciled', confidence = 1.0,
                        catalog_revision = ?, reference_source_path = ?,
                        reference_snapshot_path = ?, reference_sha256 = ?, evidence_json = ?,
                        version = version + 1, resolved_at = ?, updated_at = ?
                    WHERE unit_id = ? AND status = 'PENDING'
                    """,
                    (
                        reference.canonical_id,
                        catalog.catalog_revision,
                        primary["reference_source_path"],
                        primary["reference_snapshot_path"],
                        primary["reference_sha256"],
                        evidence_json,
                        now,
                        now,
                        unit_id,
                    ),
                )
                if cursor.rowcount != 1:
                    skipped.append(unit_id)
                    continue
                for snapshot in snapshots.values():
                    _upsert_reference_snapshot(conn, unit_id, snapshot, now)
            repaired.append(unit_id)
        except Exception as exc:
            errors.append({"unit_id": unit_id, "error": str(exc)})
    _close_resolved_questions()
    return {"ok": not errors, "repaired": repaired, "skipped": skipped, "errors": errors}


def _freeze_available_references(
    item: dict[str, Any],
    catalog: CharacterReferenceCatalog,
    character_id: str,
) -> dict[str, dict[str, Any]]:
    """Snapshot every available profile once; later binds are filesystem-only."""

    snapshots: dict[str, dict[str, Any]] = {}
    for profile in REFERENCE_PROFILES:
        reference = catalog.get(character_id, profile)
        if reference is None:
            continue
        width, height = CharacterReferenceVariant(profile).output_size
        snapshots[profile] = {
            "profile": profile,
            "target_width": width,
            "target_height": height,
            "character_id": reference.canonical_id,
            "catalog_revision": catalog.catalog_revision,
            "reference_source_path": str(reference.path),
            "reference_snapshot_path": _snapshot_reference(item, reference, profile=profile),
            "reference_sha256": reference.sha256.lower(),
        }
    if not snapshots:
        raise CharacterRegistryError(f"character {character_id!r} has no usable reference image")
    return snapshots


def _primary_snapshot(snapshots: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Keep legacy columns populated for old readers without using them to bind."""

    for profile in REFERENCE_PROFILES:
        if profile in snapshots:
            return snapshots[profile]
    raise RuntimeError("character reference snapshot set is empty")


def _upsert_reference_snapshot(conn: Any, unit_id: str, snapshot: dict[str, Any], now: str) -> None:
    conn.execute(
        """
        INSERT INTO character_reference_snapshots
            (unit_id, profile, target_width, target_height, character_id,
             catalog_revision, reference_source_path, reference_snapshot_path,
             reference_sha256, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(unit_id, profile) DO UPDATE SET
            target_width = excluded.target_width,
            target_height = excluded.target_height,
            character_id = excluded.character_id,
            catalog_revision = excluded.catalog_revision,
            reference_source_path = excluded.reference_source_path,
            reference_snapshot_path = excluded.reference_snapshot_path,
            reference_sha256 = excluded.reference_sha256,
            updated_at = excluded.updated_at
        """,
        (
            unit_id,
            snapshot["profile"],
            int(snapshot["target_width"]),
            int(snapshot["target_height"]),
            snapshot["character_id"],
            snapshot["catalog_revision"],
            snapshot["reference_source_path"],
            snapshot["reference_snapshot_path"],
            snapshot["reference_sha256"],
            now,
            now,
        ),
    )


def _updated_evidence_json(value: Any, *, reference_profiles_available: list[str]) -> str:
    try:
        payload = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload["reference_catalog_model"] = REFERENCE_CATALOG_MODEL
    payload["reference_profiles_available"] = list(reference_profiles_available)
    return json.dumps(payload, ensure_ascii=False)


def _uses_dual_profile_catalog(row: dict[str, Any]) -> bool:
    try:
        payload = json.loads(str(row.get("evidence_json") or "{}"))
    except (TypeError, ValueError):
        return False
    return isinstance(payload, dict) and payload.get("reference_catalog_model") == REFERENCE_CATALOG_MODEL


def _profile_for_item(item: dict[str, Any]) -> str | None:
    profile_value = str(item.get("cherry_profile") or "").strip()
    size_value = str(item.get("cherry_output_size") or "").strip()
    width: int | None = None
    height: int | None = None
    if size_value:
        match = re.fullmatch(r"\s*(\d+)\s*[xX×]\s*(\d+)\s*", size_value)
        if not match:
            raise CharacterProfileError(f"invalid Cherry output size: {size_value!r}")
        width, height = int(match.group(1)), int(match.group(2))
    normalized_profile: str | None = None
    if profile_value and profile_value.casefold() not in {"auto", "adaptive", "size"}:
        normalized_profile = profile_value
    if normalized_profile is None and width is None and height is None:
        return None
    profile = resolve_character_profile(profile=normalized_profile, width=width, height=height)
    target_width, target_height = CharacterReferenceVariant(profile).output_size
    item["cherry_profile"] = profile
    item["cherry_output_size"] = f"{target_width}x{target_height}"
    return profile


def _verify_profile_snapshot(snapshot: Path, expected_sha: str, profile: str, unit_id: str) -> None:
    if not snapshot.is_file() or not expected_sha or _sha256(snapshot).lower() != expected_sha.lower():
        raise RuntimeError(f"frozen character reference is missing or changed: {unit_id}/{profile}")
    expected_size = CharacterReferenceVariant(profile).output_size
    try:
        with Image.open(snapshot) as image:
            actual_size = tuple(int(value) for value in image.size)
            image.verify()
    except Exception as exc:
        raise RuntimeError(f"frozen character reference is invalid: {unit_id}/{profile}") from exc
    if actual_size != expected_size:
        raise RuntimeError(
            f"frozen character reference profile mismatch: {unit_id}/{profile} "
            f"is {actual_size[0]}x{actual_size[1]}, expected {expected_size[0]}x{expected_size[1]}"
        )


def _promote_legacy_snapshot(row: dict[str, Any], profile: str) -> dict[str, Any] | None:
    """Migrate one pre-dual-profile active task without trusting the old variant label."""

    unit_id = str(row.get("unit_id") or "")
    expected_size = CharacterReferenceVariant(profile).output_size
    legacy_path = Path(str(row.get("reference_snapshot_path") or ""))
    legacy_sha = str(row.get("reference_sha256") or "").lower()
    if legacy_path.is_file() and legacy_sha and _sha256(legacy_path).lower() == legacy_sha:
        try:
            with Image.open(legacy_path) as image:
                legacy_size = tuple(int(value) for value in image.size)
        except Exception:
            legacy_size = (0, 0)
        if legacy_size == expected_size:
            snapshot = {
                "unit_id": unit_id,
                "profile": profile,
                "target_width": expected_size[0],
                "target_height": expected_size[1],
                "character_id": str(row.get("character_id") or ""),
                "catalog_revision": str(row.get("catalog_revision") or "legacy"),
                "reference_source_path": str(row.get("reference_source_path") or legacy_path),
                "reference_snapshot_path": str(legacy_path),
                "reference_sha256": legacy_sha,
            }
            with get_connection() as conn:
                _upsert_reference_snapshot(conn, unit_id, snapshot, _now())
            return snapshot

    catalog = _catalog()
    reference = catalog.get(str(row.get("character_id") or ""), profile)
    if reference is None:
        return None
    target = _snapshot_reference(row, reference, profile=profile)
    snapshot = {
        "unit_id": unit_id,
        "profile": profile,
        "target_width": expected_size[0],
        "target_height": expected_size[1],
        "character_id": reference.canonical_id,
        "catalog_revision": catalog.catalog_revision,
        "reference_source_path": str(reference.path),
        "reference_snapshot_path": target,
        "reference_sha256": reference.sha256.lower(),
    }
    with get_connection() as conn:
        _upsert_reference_snapshot(conn, unit_id, snapshot, _now())
    return snapshot


def _missing_profile_message(character_id: str, profile: str) -> str:
    width, height = CharacterReferenceVariant(profile).output_size
    label = "半身/Emoji" if profile == "half" else "全身/Full"
    other = "384x512 全身" if profile == "half" else "256x256 半身"
    return (
        f"角色“{character_id}”缺少 {width}x{height} {label} 参考图；"
        f"已停止校色/矫正，绝不会改用 {other} 参考图。"
    )


def _snapshot_reference(
    item: dict[str, Any],
    reference: CharacterReference | None = None,
    *,
    profile: str | None = None,
) -> str:
    source = Path(str(reference.path if reference else item.get("reference_source_path") or ""))
    expected_sha = str(reference.sha256 if reference else item.get("reference_sha256") or "")
    unit_id = str(item.get("unit_id") or "")
    character_id = str(reference.canonical_id if reference else item.get("character_id") or "")
    if not source.is_file() or _sha256(source) != expected_sha:
        raise RuntimeError(f"character color reference changed before snapshot: {source}")
    target_dir = Path(str(item["run_dir"])) / "character_refs" / _safe_component(unit_id)
    if profile:
        target_dir /= _safe_component(profile)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{_safe_component(character_id)}-{expected_sha[:12]}{source.suffix.lower()}"
    if target.is_file() and _sha256(target) == expected_sha:
        return str(target)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        shutil.copy2(source, temporary)
        if _sha256(temporary) != expected_sha:
            raise RuntimeError(f"character color reference snapshot verification failed: {unit_id}")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return str(target)


def _resolve_evidence(registry: CharacterRegistry, evidence: list[str]):
    matched: dict[str, Any] = {}
    ambiguous = []
    for value in evidence:
        resolution = registry.resolve(value)
        if resolution.status is CharacterResolutionStatus.AMBIGUOUS:
            ambiguous.extend(resolution.candidates)
        elif resolution.reference:
            matched[resolution.reference.canonical_id] = resolution
    candidate_ids = set(matched) | {candidate.canonical_id for candidate in ambiguous}
    if len(candidate_ids) == 1 and not ambiguous:
        return next(iter(matched.values()))
    # Build one strict combined query so the service returns a standard result.
    return registry.resolve(" ".join(evidence))


def _exact_answer_reference(registry: CharacterRegistry, answer: str) -> CharacterReference | None:
    answer_tokens = normalize_name_tokens(answer)
    if not answer_tokens:
        return None
    matches = {
        reference.canonical_id: reference
        for reference in registry.references
        for alias in reference.aliases
        if normalize_name_tokens(alias) == answer_tokens
    }
    return next(iter(matches.values())) if len(matches) == 1 else None


def _parse_assignments(text: str) -> dict[str, str]:
    assignments: dict[str, str] = {}
    for match in ASSIGNMENT_RE.finditer(str(text or "")):
        token = match.group(1).upper()
        value = match.group(2).strip()
        previous = assignments.get(token)
        if previous and previous.casefold() != value.casefold():
            assignments[token] = previous + "|" + value
        else:
            assignments[token] = value
    return assignments


def _claim_due_questions(owner: str, limit: int) -> list[dict[str, Any]]:
    now = _now()
    lease_until = _after(now, _lease_seconds())
    claimed: list[dict[str, Any]] = []
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        candidates = conn.execute(
            """
            SELECT *
            FROM character_resolution_questions
            WHERE (
                    (status = 'PENDING' AND waiting_started_at IS NOT NULL
                     AND (
                          (deadline_at IS NOT NULL AND deadline_at <= ?)
                          OR (next_action_at IS NOT NULL AND next_action_at <= ?)
                         ))
                    OR status = 'EXPIRING'
                  )
              AND (lease_until IS NULL OR lease_until <= ?)
            ORDER BY COALESCE(next_action_at, failed_at, created_at), id
            LIMIT ?
            """,
            (now, now, now, limit),
        ).fetchall()
        for raw in candidates:
            question_id = str(raw["id"])
            cursor = conn.execute(
                """
                UPDATE character_resolution_questions
                SET lease_owner = ?, lease_until = ?, updated_at = ?
                WHERE id = ?
                  AND status IN ('PENDING', 'EXPIRING')
                  AND (lease_until IS NULL OR lease_until <= ?)
                """,
                (owner, lease_until, now, question_id, now),
            )
            if cursor.rowcount != 1:
                continue
            row = conn.execute(
                "SELECT * FROM character_resolution_questions WHERE id = ?",
                (question_id,),
            ).fetchone()
            if row is not None:
                claimed.append(dict(row))
    return claimed


def _get_pending_for_question(question_id: str) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM character_resolutions
            WHERE question_id = ? AND status = 'PENDING'
            ORDER BY item_index, unit_id
            """,
            (question_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _has_expired_tokens(conversation_id: str, user_id: str, tokens: list[str]) -> bool:
    normalized = sorted({str(token or "").upper() for token in tokens if str(token or "").strip()})
    if not normalized:
        return False
    placeholders = ",".join("?" for _ in normalized)
    with get_connection() as conn:
        row = conn.execute(
            f"""
            SELECT 1 FROM character_resolutions
            WHERE conversation_id = ? AND user_id = ? AND status = 'EXPIRED'
              AND UPPER(question_token) IN ({placeholders})
            LIMIT 1
            """,
            (conversation_id, user_id, *normalized),
        ).fetchone()
    return row is not None


def _get_expired_for_question(question_id: str) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM character_resolutions
            WHERE question_id = ? AND status = 'EXPIRED'
            ORDER BY item_index, unit_id
            """,
            (question_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _complete_reminder(question_id: str, owner: str, receipt: Any) -> bool:
    now = _now()
    next_action = _after(now, _reminder_interval_seconds())
    message_id = _receipt_message_id(receipt)
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE character_resolution_questions
            SET reminder_count = COALESCE(reminder_count, 0) + 1,
                last_reminded_at = ?, next_action_at = ?,
                outbound_message_id = COALESCE(NULLIF(?, ''), outbound_message_id),
                lease_owner = NULL, lease_until = NULL, updated_at = ?
            WHERE id = ? AND status = 'PENDING' AND lease_owner = ?
            """,
            (now, next_action, message_id, now, question_id, owner),
        )
    return cursor.rowcount == 1


def _expire_claimed_question(question_id: str, owner: str) -> dict[str, Any]:
    now = _now()
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        question = conn.execute(
            """
            SELECT status FROM character_resolution_questions
            WHERE id = ? AND lease_owner = ?
            """,
            (question_id, owner),
        ).fetchone()
        if question is None:
            return {"expired": False, "count": 0}
        if str(question["status"]) == "EXPIRING":
            count = conn.execute(
                "SELECT COUNT(*) FROM character_resolutions WHERE question_id = ? AND status = 'EXPIRED'",
                (question_id,),
            ).fetchone()[0]
            return {"expired": bool(count), "count": int(count)}
        cursor = conn.execute(
            """
            UPDATE character_resolutions
            SET status = 'EXPIRED', version = version + 1, updated_at = ?
            WHERE question_id = ? AND status = 'PENDING'
            """,
            (now, question_id),
        )
        if cursor.rowcount <= 0:
            conn.execute(
                """
                UPDATE character_resolution_questions
                SET status = 'RESOLVED', lease_owner = NULL, lease_until = NULL,
                    next_action_at = NULL, updated_at = ?
                WHERE id = ? AND status = 'PENDING' AND lease_owner = ?
                """,
                (now, question_id, owner),
            )
            return {"expired": False, "count": 0}
        question_cursor = conn.execute(
            """
            UPDATE character_resolution_questions
            SET status = 'EXPIRING', failed_at = ?, next_action_at = ?, updated_at = ?
            WHERE id = ? AND status = 'PENDING' AND lease_owner = ?
            """,
            (now, now, now, question_id, owner),
        )
        if question_cursor.rowcount != 1:
            raise RuntimeError(f"character question expiration race: {question_id}")
    return {"expired": True, "count": cursor.rowcount}


def _complete_expiration(question_id: str, owner: str, receipt: Any) -> bool:
    now = _now()
    message_id = _receipt_message_id(receipt)
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE character_resolution_questions
            SET status = 'EXPIRED', next_action_at = NULL,
                outbound_message_id = COALESCE(NULLIF(?, ''), outbound_message_id),
                lease_owner = NULL, lease_until = NULL, updated_at = ?
            WHERE id = ? AND status = 'EXPIRING' AND lease_owner = ?
            """,
            (message_id, now, question_id, owner),
        )
    return cursor.rowcount == 1


def _resolve_empty_claim(question_id: str, owner: str) -> None:
    now = _now()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE character_resolution_questions
            SET status = 'RESOLVED', next_action_at = NULL,
                lease_owner = NULL, lease_until = NULL, updated_at = ?
            WHERE id = ? AND status = 'PENDING' AND lease_owner = ?
            """,
            (now, question_id, owner),
        )


def _release_question_lease(question_id: str, owner: str, *, retry: bool) -> None:
    now = _now()
    retry_at = _after(now, _sweep_interval_seconds()) if retry else None
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE character_resolution_questions
            SET next_action_at = COALESCE(?, next_action_at),
                lease_owner = NULL, lease_until = NULL, updated_at = ?
            WHERE id = ? AND status IN ('PENDING', 'EXPIRING') AND lease_owner = ?
            """,
            (retry_at, now, question_id, owner),
        )


def _format_reminder_message(items: list[dict[str, Any]], ordinal: int, maximum: int) -> str:
    header = f"角色确认提醒 {ordinal}/{maximum}：抠图已经完成，确认角色后才能继续校色和位置矫正。"
    return header + "\n" + format_pending_prompt(items)


def _format_timeout_message(question: dict[str, Any], items: list[dict[str, Any]], reason: str) -> str:
    names = "、".join(str(item.get("source_name") or item.get("question_token") or "任务") for item in items)
    run_id = str(question.get("run_id") or "")
    return (
        f"任务 {run_id} 因等待角色确认超时而失败。\n"
        f"待确认内容：{names}\n{reason}"
    )


def _send_text_to_chat(chat_id: str, text: str) -> Any:
    if not chat_id:
        raise ValueError("character confirmation question has no Feishu chat_id")
    from assetclaw_matting.feishu.client import feishu_client

    return feishu_client.send_text_to_chat(chat_id, text)


def _fail_waiting_run(run_kind: str, run_id: str, reason: str) -> dict[str, Any]:
    if run_kind == "direct_image":
        from assetclaw_matting.skills.direct_image_skills import fail_character_confirmation_timeout

        return fail_character_confirmation_timeout(run_id, reason)
    if run_kind == "direct_video":
        from assetclaw_matting.skills.direct_video_skills import fail_character_confirmation_timeout

        return fail_character_confirmation_timeout(run_id, reason)
    return {"ok": False, "error": f"unsupported run kind: {run_kind}"}


def _resume_waiting_run(run_kind: str, run_id: str) -> dict[str, Any]:
    if run_kind == "direct_image":
        from assetclaw_matting.skills.direct_image_skills import resume_after_character_resolution

        return resume_after_character_resolution(run_id)
    if run_kind == "direct_video":
        from assetclaw_matting.skills.direct_video_skills import resume_after_character_resolution

        return resume_after_character_resolution(run_id)
    return {"ok": False, "error": f"unsupported run kind: {run_kind}"}


def _receipt_message_id(receipt: Any) -> str:
    if isinstance(receipt, dict):
        return str(receipt.get("message_id") or receipt.get("id") or "")
    return ""


def _after(value: str, seconds: int) -> str:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (parsed + timedelta(seconds=max(0, int(seconds)))).isoformat()


def _is_due(value: str, now: str) -> bool:
    if not str(value or "").strip():
        return False
    target = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    current = datetime.fromisoformat(str(now).replace("Z", "+00:00"))
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current >= target


def _reminder_interval_seconds() -> int:
    return max(1, int(settings.character_confirmation_reminder_interval_seconds or 600))


def _max_reminders() -> int:
    return max(1, int(settings.character_confirmation_max_reminders or 2))


def _sweep_interval_seconds() -> int:
    return max(1, int(settings.character_confirmation_sweep_interval_seconds or 30))


def _lease_seconds() -> int:
    return max(_sweep_interval_seconds() * 2, int(settings.character_confirmation_lease_seconds or 120))


def _clean_evidence(values: Iterable[Any]) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned or [""]


def _safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("._")
    return cleaned or "character"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _close_resolved_questions() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE character_resolution_questions
            SET status = 'RESOLVED', updated_at = ?
            WHERE status = 'PENDING'
              AND NOT EXISTS (
                  SELECT 1 FROM character_resolutions r
                  WHERE r.question_id = character_resolution_questions.id
                    AND r.status = 'PENDING'
              )
            """,
            (_now(),),
        )
