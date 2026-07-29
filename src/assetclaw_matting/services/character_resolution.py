from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from assetclaw_matting.config import settings
from assetclaw_matting.db.sqlite import get_connection
from assetclaw_matting.services.character_identity import (
    CharacterReference,
    CharacterRegistry,
    CharacterResolutionStatus,
    normalize_name_tokens,
)


TOKEN_PATTERN = r"(?<![A-Za-z0-9])(C-[A-F0-9]{8,16})(?![A-F0-9])"
QUESTION_TOKEN_RE = re.compile(TOKEN_PATTERN, re.IGNORECASE)
ASSIGNMENT_RE = re.compile(
    TOKEN_PATTERN + r"\s*\]?\s*(?:=|:|：|是)\s*([\w\-]+)",
    re.IGNORECASE,
)
RUN_KINDS = {"direct_image", "direct_video"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _registry() -> CharacterRegistry:
    return CharacterRegistry.discover(settings.cherry_character_reference_dir)


def _catalog_revision(registry: CharacterRegistry) -> str:
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
    registry = _registry()
    revision = _catalog_revision(registry)
    rows: list[dict[str, Any]] = []
    for ordinal, raw in enumerate(units, start=1):
        unit_id = str(raw.get("unit_id") or f"{run_id}:item:{ordinal:02d}")
        evidence = _clean_evidence(raw.get("evidence") or [raw.get("source_name") or ""])
        resolution = _resolve_evidence(registry, evidence)
        reference = resolution.reference
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
                "status": "FROZEN" if reference else "PENDING",
                "character_id": reference.canonical_id if reference else "",
                "resolution_method": "filename" if reference else "",
                "confidence": 1.0 if reference else 0.0,
                "evidence_json": json.dumps(
                    {
                        "inputs": evidence,
                        "result": resolution.to_dict(),
                    },
                    ensure_ascii=False,
                ),
                "catalog_revision": revision,
                "reference_source_path": str(reference.path) if reference else "",
                "reference_snapshot_path": "",
                "reference_sha256": reference.sha256 if reference else "",
            }
        if reference:
            row["reference_snapshot_path"] = _snapshot_reference(row, reference)
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


def get_pending_for_actor(conversation_id: str, user_id: str) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM character_resolutions
            WHERE conversation_id = ? AND user_id = ? AND status = 'PENDING'
            ORDER BY created_at, item_index, unit_id
            """,
            (conversation_id, user_id),
        ).fetchall()
    return [dict(row) for row in rows]


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
        return {"handled": False}
    registry = _registry()
    assignments = _parse_assignments(text)
    if not assignments:
        if QUESTION_TOKEN_RE.search(str(text or "")):
            return {
                "handled": True,
                "ok": False,
                "message": "角色编号格式已识别，但映射写法不完整。请按 C-编号=角色名 回复。\n" + format_pending_prompt(pending),
            }
        bare_reference = _exact_answer_reference(registry, str(text or "").strip())
        if bare_reference and len(pending) == 1:
            assignments = {str(pending[0]["question_token"]).upper(): bare_reference.canonical_id}
        elif bare_reference and len(pending) > 1:
            return {
                "handled": True,
                "ok": False,
                "message": "现在有多个任务待确认，请带上每项前面的 C-编号，避免角色错位。\n" + format_pending_prompt(pending),
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

    snapshots: dict[str, str] = {}
    for token, reference in validated.items():
        row = pending_by_token[token]
        snapshots[token] = _snapshot_reference(
            {
                **row,
                "character_id": reference.canonical_id,
                "reference_source_path": str(reference.path),
                "reference_sha256": reference.sha256,
            },
            reference,
        )

    now = _now()
    affected: set[tuple[str, str]] = set()
    with get_connection() as conn:
        for token, reference in validated.items():
            row = pending_by_token[token]
            cursor = conn.execute(
                """
                UPDATE character_resolutions
                SET status = 'FROZEN', character_id = ?, resolution_method = 'user',
                    confidence = 1.0, catalog_revision = ?, reference_source_path = ?,
                    reference_snapshot_path = ?, reference_sha256 = ?, resolved_by_message_id = ?, version = version + 1,
                    resolved_at = ?, updated_at = ?
                WHERE unit_id = ? AND status = 'PENDING'
                """,
                (
                    reference.canonical_id,
                    _catalog_revision(registry),
                    str(reference.path),
                    snapshots[token],
                    reference.sha256,
                    message_id,
                    now,
                    now,
                    row["unit_id"],
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"character resolution changed concurrently: {token}")
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
    """Copy frozen DB bindings into the persisted task immediately before Cherry."""

    reconciliation = reconcile_resolved_units(run_kind, run_id)
    if not reconciliation.get("ok"):
        first = (reconciliation.get("errors") or [{}])[0]
        raise RuntimeError(f"character reference snapshot could not be recovered: {first.get('error') or 'unknown error'}")
    rows = {str(row["unit_id"]): row for row in get_run_resolutions(run_kind, run_id)}
    missing: list[str] = []
    for item in items:
        unit_id = str(item.get("character_unit_id") or "")
        row = rows.get(unit_id)
        if not row or str(row.get("status") or "") != "FROZEN":
            missing.append(str(item.get("source_name") or item.get("name") or unit_id or "unknown"))
            continue
        snapshot = Path(str(row.get("reference_snapshot_path") or ""))
        expected_sha = str(row.get("reference_sha256") or "")
        if not snapshot.is_file() or _sha256(snapshot) != expected_sha:
            raise RuntimeError(f"frozen color reference is missing or changed: {unit_id}")
        item.update(
            {
                "character_id": str(row["character_id"]),
                "character_name": str(row["character_id"]),
                "character_resolution_method": str(row.get("resolution_method") or ""),
                "color_reference_path": str(snapshot),
                "color_reference_sha256": expected_sha,
                "color_correction_status": "READY",
                "position_alignment_enabled": True,
            }
        )
    return {"ready": not missing, "missing": missing, "items": items}


def all_run_units_frozen(run_kind: str, run_id: str) -> bool:
    rows = get_run_resolutions(run_kind, run_id)
    return bool(rows) and all(str(row.get("status") or "") == "FROZEN" for row in rows)


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
            SET status = 'CANCELLED', updated_at = ?
            WHERE run_kind = ? AND run_id = ? AND status = 'PENDING'
            """,
            (now, run_kind, run_id),
        )


def _freeze_unit(unit_id: str) -> dict[str, Any]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM character_resolutions WHERE unit_id = ?", (unit_id,)).fetchone()
    if not row:
        raise KeyError(f"character resolution not found: {unit_id}")
    item = dict(row)
    if str(item.get("status") or "") == "FROZEN":
        snapshot = Path(str(item.get("reference_snapshot_path") or ""))
        if snapshot.is_file() and _sha256(snapshot) == str(item.get("reference_sha256") or ""):
            return item
    if str(item.get("status") or "") != "RESOLVED":
        raise RuntimeError(f"character resolution is not ready to freeze: {unit_id}")
    target = Path(_snapshot_reference(item))
    now = _now()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE character_resolutions
            SET status = 'FROZEN', reference_snapshot_path = ?, version = version + 1, updated_at = ?
            WHERE unit_id = ? AND status = 'RESOLVED'
            """,
            (str(target), now, unit_id),
        )
        if cursor.rowcount != 1:
            raise RuntimeError(f"character resolution freeze race: {unit_id}")
    item["status"] = "FROZEN"
    item["reference_snapshot_path"] = str(target)
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


def _snapshot_reference(item: dict[str, Any], reference: CharacterReference | None = None) -> str:
    source = Path(str(reference.path if reference else item.get("reference_source_path") or ""))
    expected_sha = str(reference.sha256 if reference else item.get("reference_sha256") or "")
    unit_id = str(item.get("unit_id") or "")
    character_id = str(reference.canonical_id if reference else item.get("character_id") or "")
    if not source.is_file() or _sha256(source) != expected_sha:
        raise RuntimeError(f"character color reference changed before snapshot: {source}")
    target_dir = Path(str(item["run_dir"])) / "character_refs" / _safe_component(unit_id)
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
