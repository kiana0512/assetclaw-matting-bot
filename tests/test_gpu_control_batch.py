from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from assetclaw_matting.services.gpu_control_batch import (
    APPROVED_OUTPUT_NODE,
    APPROVED_PIPELINE_COMMIT,
    APPROVED_PIPELINE_SHA256,
    APPROVED_WORKFLOW_KEY,
    APPROVED_WORKFLOW_VERSION,
    GpuControlBatchClient,
    GpuControlError,
    _normalize_relative_path,
    _normalize_scheduler_capacity,
    build_input_batch,
    compact_remote_state,
    merge_remote_state,
    result_artifact,
    validate_partial_failed_items,
    validate_workflow_identity,
    verify_and_publish_result,
)


def test_v3_scheduler_capacity_accepting_field_is_normalized() -> None:
    payload = _normalize_scheduler_capacity({
        "accepting": True,
        "cluster": {
            "eligible_nodes": 3,
            "available_slots": 3,
            "queued_jobs": 0,
            "running_jobs": 0,
        },
        "client": {"queued_jobs": 0, "max_queued": 20},
    })

    assert payload["accepting_batches"] is True
    assert payload["suggested_max_new_batches"] == 3
    assert payload["idle_nodes"] == 3
    assert payload["active_batches"] == 0
from assetclaw_matting.services import hybrid_matting_router
from assetclaw_matting.skills import comfyui_skills


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rgba_result(path: Path, seed: int) -> None:
    image = Image.new("RGBA", (16, 16))
    pixels = []
    for y in range(16):
        for x in range(16):
            alpha = 0 if x < 4 else 255
            pixels.append(((x * 17 + seed) % 256, (y * 19 + seed) % 256, ((x + y) * 13 + seed) % 256, alpha))
    image.putdata(pixels)
    image.save(path)


def _result_archive(
    path: Path,
    prepared: dict,
    *,
    bad_mapping: bool = False,
    manifest_extra: dict | None = None,
) -> tuple[Path, str]:
    work = path / "result_source"
    work.mkdir()
    items = []
    result_files = []
    for frame in prepared["frames"]:
        output_relative = frame["output_relative_path"]
        target = work / Path(*output_relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        _rgba_result(target, int(frame["ordinal"]) + 1)
        items.append(
            {
                "ordinal": frame["ordinal"],
                "input_relative_path": "wrong.png" if bad_mapping and frame["ordinal"] == 0 else frame["relative_path"],
                "input_sha256": frame["sha256"],
                "output_relative_path": output_relative,
                "output_sha256": _sha256(target),
                "status": "SUCCEEDED",
                "job_id": f"job-{frame['ordinal']}",
                "node_id": "worker-3090-a",
                "attempts": 1,
            }
        )
        result_files.append((target, f"results/{output_relative}"))
    manifest = {
        "schema_version": "1.0",
        "batch_id": "batch-test",
        "external_batch_id": prepared["external_batch_id"],
        "total": len(items),
        "items": items,
    }
    manifest.update(manifest_extra or {})
    archive_path = path / "result.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        for source, member in result_files:
            archive.write(source, member)
    return archive_path, _sha256(archive_path)


def test_build_input_batch_preserves_paths_hashes_and_retry_bytes(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    first = input_root / "角色甲" / "idle" / "frame_0001.jpg"
    second = input_root / "角色乙" / "run" / "frame_0002.png"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    Image.new("RGB", (12, 10), (255, 20, 30)).save(first)
    Image.new("RGB", (10, 12), (20, 255, 30)).save(second)

    prepared = build_input_batch(
        "COMFY_TEST",
        input_root,
        [first, second],
        tmp_path / "handoff",
        preserve_structure=True,
        external_batch_id="assetclaw:test:matting:g1",
    )
    archive_path = Path(prepared["archive_path"])
    original_archive_sha = _sha256(archive_path)
    assert [frame["ordinal"] for frame in prepared["manifest"]["frames"]] == [0, 1]
    assert prepared["manifest"]["frames"][0]["relative_path"] == "角色甲/idle/frame_0001.jpg"
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.namelist() == ["角色甲/idle/frame_0001.jpg", "角色乙/run/frame_0002.png"]

    retried = build_input_batch(
        "COMFY_TEST",
        input_root,
        [first, second],
        tmp_path / "handoff",
        preserve_structure=True,
        external_batch_id="assetclaw:test:matting:g1",
    )
    assert retried["manifest_sha256"] == prepared["manifest_sha256"]
    assert _sha256(archive_path) == original_archive_sha


def test_build_input_batch_rejects_changed_input_for_same_idempotency_key(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    source = input_root / "frame.png"
    source.parent.mkdir()
    Image.new("RGB", (8, 8), (255, 0, 0)).save(source)
    workspace = tmp_path / "handoff"
    build_input_batch("COMFY_TEST", input_root, [source], workspace, preserve_structure=True)
    Image.new("RGB", (8, 8), (0, 255, 0)).save(source)

    with pytest.raises(GpuControlError, match="changed"):
        build_input_batch("COMFY_TEST", input_root, [source], workspace, preserve_structure=True)


def test_build_input_batch_rejects_output_name_collision(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    jpg = input_root / "frame.jpg"
    png = input_root / "frame.png"
    input_root.mkdir()
    Image.new("RGB", (8, 8), (255, 0, 0)).save(jpg)
    Image.new("RGB", (8, 8), (0, 255, 0)).save(png)

    with pytest.raises(GpuControlError, match="OUTPUT_PATH_CONFLICT"):
        build_input_batch("COMFY_TEST", input_root, [jpg, png], tmp_path / "handoff", preserve_structure=True)


def test_result_is_verified_then_atomically_published(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    source = input_root / "shot" / "frame_0001.png"
    source.parent.mkdir(parents=True)
    Image.new("RGB", (16, 16), (255, 40, 20)).save(source)
    prepared = build_input_batch("COMFY_TEST", input_root, [source], tmp_path / "handoff", preserve_structure=True)
    archive_path, archive_sha = _result_archive(tmp_path, prepared)
    output_root = tmp_path / "matte"
    output_root.mkdir()
    (output_root / "old.txt").write_text("old", encoding="utf-8")

    published = verify_and_publish_result(archive_path, archive_sha, prepared, output_root, "COMFY_TEST")

    assert len(published) == 1
    assert not (output_root / "old.txt").exists()
    assert (output_root / "shot" / "frame_0001.png").is_file()


def test_result_accepts_and_verifies_approved_workflow_identity_fields(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    source = input_root / "frame.png"
    input_root.mkdir()
    Image.new("RGB", (16, 16), (255, 40, 20)).save(source)
    prepared = build_input_batch("COMFY_IDENTITY", input_root, [source], tmp_path / "handoff", preserve_structure=True)
    archive_path, archive_sha = _result_archive(
        tmp_path,
        prepared,
        manifest_extra={
            "workflow_key": APPROVED_WORKFLOW_KEY,
            "workflow_version": APPROVED_WORKFLOW_VERSION,
            "pipeline_commit": APPROVED_PIPELINE_COMMIT,
            "pipeline_sha256": APPROVED_PIPELINE_SHA256,
            "output_node": APPROVED_OUTPUT_NODE,
        },
    )

    published = verify_and_publish_result(
        archive_path,
        archive_sha,
        prepared,
        tmp_path / "matte",
        "COMFY_IDENTITY",
    )

    assert len(published) == 1


def test_result_rejects_unknown_manifest_extensions(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    source = input_root / "frame.png"
    input_root.mkdir()
    Image.new("RGB", (16, 16), (255, 40, 20)).save(source)
    prepared = build_input_batch("COMFY_UNKNOWN", input_root, [source], tmp_path / "handoff", preserve_structure=True)
    archive_path, archive_sha = _result_archive(tmp_path, prepared, manifest_extra={"unexpected": "value"})

    with pytest.raises(GpuControlError, match="unsupported fields"):
        verify_and_publish_result(
            archive_path,
            archive_sha,
            prepared,
            tmp_path / "matte",
            "COMFY_UNKNOWN",
        )


def test_invalid_result_does_not_touch_existing_output(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    source = input_root / "frame.png"
    input_root.mkdir()
    Image.new("RGB", (16, 16), (255, 40, 20)).save(source)
    prepared = build_input_batch("COMFY_TEST", input_root, [source], tmp_path / "handoff", preserve_structure=True)
    archive_path, archive_sha = _result_archive(tmp_path, prepared, bad_mapping=True)
    output_root = tmp_path / "matte"
    output_root.mkdir()
    marker = output_root / "old.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(GpuControlError, match="mapping mismatch"):
        verify_and_publish_result(archive_path, archive_sha, prepared, output_root, "COMFY_TEST")

    assert marker.read_text(encoding="utf-8") == "keep"


def test_result_archive_rejects_explicit_directory_entries(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    source = input_root / "frame.png"
    input_root.mkdir()
    Image.new("RGB", (16, 16), (255, 40, 20)).save(source)
    prepared = build_input_batch("COMFY_TEST", input_root, [source], tmp_path / "handoff", preserve_structure=True)
    archive_path, _ = _result_archive(tmp_path, prepared)
    with zipfile.ZipFile(archive_path, "a", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("results/", b"")

    with pytest.raises(GpuControlError, match="explicit directory"):
        verify_and_publish_result(archive_path, _sha256(archive_path), prepared, tmp_path / "matte", "COMFY_TEST")


def test_relative_path_rejects_empty_segments() -> None:
    with pytest.raises(GpuControlError, match="empty segment"):
        _normalize_relative_path("shot//frame.png")


def test_remote_progress_merge_is_monotonic_and_keeps_v3_identity() -> None:
    merged = merge_remote_state(
        {"progress": 72.5},
        {
            "batch_id": "batch-1",
            "status": "RUNNING",
            "progress": 68.0,
            "workflow_key": "imageclip-rgba",
            "workflow_version": APPROVED_WORKFLOW_VERSION,
            "pipeline_commit": APPROVED_PIPELINE_COMMIT,
            "pipeline_sha256": APPROVED_PIPELINE_SHA256,
        },
    )

    assert merged["progress"] == 72.5
    assert merged["workflow_version"] == APPROVED_WORKFLOW_VERSION
    assert merged["pipeline_commit"] == APPROVED_PIPELINE_COMMIT

    cancelled = merge_remote_state(
        merged,
        {
            "batch_id": "batch-1",
            "status": "CANCELLING",
            "_response_meta": {"http_status": 202, "request_id": "cancel-1"},
        },
    )
    assert cancelled["workflow_version"] == APPROVED_WORKFLOW_VERSION
    assert cancelled["pipeline_commit"] == APPROVED_PIPELINE_COMMIT
    assert cancelled["progress"] == 72.5


def test_current_gpu_control_identity_is_approved_and_mismatch_fails_closed() -> None:
    approved = validate_workflow_identity(
        {
            "workflow_key": APPROVED_WORKFLOW_KEY,
            "workflow_version": APPROVED_WORKFLOW_VERSION,
            "pipeline_commit": APPROVED_PIPELINE_COMMIT,
            "pipeline_sha256": APPROVED_PIPELINE_SHA256,
            "output_node": APPROVED_OUTPUT_NODE,
        }
    )
    assert approved["status"] == "VERIFIED"

    legacy_production = validate_workflow_identity(
        {
            "workflow_key": APPROVED_WORKFLOW_KEY,
            "workflow_version": APPROVED_WORKFLOW_VERSION,
            "pipeline_commit": APPROVED_PIPELINE_COMMIT,
            "pipeline_sha256": APPROVED_PIPELINE_SHA256,
        }
    )
    assert legacy_production["status"] == "PARTIAL_MATCH"
    assert legacy_production["missing_fields"] == ["output_node"]

    with pytest.raises(GpuControlError, match="identity mismatch"):
        validate_workflow_identity(
            {
                "workflow_key": APPROVED_WORKFLOW_KEY,
                "workflow_version": "unexpected-version",
            }
        )

    with pytest.raises(GpuControlError, match="identity mismatch"):
        validate_workflow_identity(
            {
                "workflow_key": APPROVED_WORKFLOW_KEY,
                "workflow_version": APPROVED_WORKFLOW_VERSION,
                "pipeline_commit": APPROVED_PIPELINE_COMMIT,
                "pipeline_sha256": APPROVED_PIPELINE_SHA256,
                "output_node": "SaveImage #99",
            }
        )


def test_compact_remote_state_accepts_candidate_assembling_at_and_output_node() -> None:
    state = compact_remote_state(
        {
            "batch_id": "batch-1",
            "status": "ASSEMBLING",
            "assembling_at": "2026-07-30T08:00:00Z",
            "output_node": APPROVED_OUTPUT_NODE,
        }
    )

    assert state["assembling_at"] == "2026-07-30T08:00:00Z"
    assert state["assembling_started_at"] == "2026-07-30T08:00:00Z"
    assert state["output_node"] == APPROVED_OUTPUT_NODE


def test_download_artifact_resumes_a_verified_partial_file(tmp_path: Path) -> None:
    payload = b"0123456789" * 100
    expected_sha = hashlib.sha256(payload).hexdigest()
    destination = tmp_path / "result.zip"
    partial = tmp_path / "result.zip.part"
    split = 430
    partial.write_bytes(payload[:split])
    observed_headers: dict[str, str] = {}

    class FakeResponse:
        status_code = 206
        headers = {
            "X-Artifact-SHA256": expected_sha,
            "Content-Range": f"bytes {split}-{len(payload) - 1}/{len(payload)}",
            "X-Request-ID": "download-response-1",
        }

        def iter_content(self, chunk_size):
            assert chunk_size == 1024 * 1024
            yield payload[split:]

        def close(self):
            return None

    class FakeSession:
        def get(self, _url, *, headers, **_kwargs):
            observed_headers.update(headers)
            return FakeResponse()

    client = object.__new__(GpuControlBatchClient)
    client.base_url = "https://gpu-control.test"
    client.connect_timeout = 1
    client.download_timeout = 30
    client.retries = 2
    client.verify = False
    client.api_key = ""
    client.session = FakeSession()
    result = client.download_artifact(
        {
            "download_url": "/artifact/result.zip",
            "sha256": expected_sha,
            "size_bytes": len(payload),
        },
        destination,
        request_id="download-1",
    )

    assert observed_headers["Range"] == f"bytes={split}-"
    assert destination.read_bytes() == payload
    assert not partial.exists()
    assert result["resumed_from_bytes"] == split
    assert result["attempts"] == 1


def test_result_artifact_requires_exactly_one_complete_archive() -> None:
    valid = {
        "batch_id": "batch-1",
        "artifacts": [
            {
                "id": "artifact-1",
                "kind": "result_archive",
                "filename": "batch-1-rgba.zip",
                "content_type": "application/zip",
                "size_bytes": 123,
                "sha256": "a" * 64,
                "download_url": "/api/v1/batches/batch-1/artifacts/artifact-1",
            }
        ],
    }
    assert result_artifact(valid)["id"] == "artifact-1"
    with pytest.raises(GpuControlError, match="exactly one"):
        result_artifact({"batch_id": "batch-1", "artifacts": []})


def test_partial_skip_run_preserves_preexisting_outputs_on_publish(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    source = input_root / "new" / "frame.png"
    source.parent.mkdir(parents=True)
    Image.new("RGB", (16, 16), (255, 40, 20)).save(source)
    prepared = build_input_batch("COMFY_TEST", input_root, [source], tmp_path / "handoff", preserve_structure=True)
    archive_path, archive_sha = _result_archive(tmp_path, prepared)
    output_root = tmp_path / "matte"
    existing = output_root / "finished" / "frame.png"
    existing.parent.mkdir(parents=True)
    _rgba_result(existing, 99)
    existing_sha = _sha256(existing)

    verify_and_publish_result(
        archive_path,
        archive_sha,
        prepared,
        output_root,
        "COMFY_TEST",
        preserve_existing=True,
    )

    assert _sha256(output_root / "finished" / "frame.png") == existing_sha
    assert (output_root / "new" / "frame.png").is_file()


def test_partial_result_publishes_only_successful_ordinals_without_erasing_existing(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    sources = []
    for index in range(3):
        source = input_root / f"{index:04d}.png"
        source.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (16, 16), (index * 20, 40, 80)).save(source)
        sources.append(source)
    prepared = build_input_batch("COMFY_PARTIAL", input_root, sources, tmp_path / "handoff", preserve_structure=True)

    result_root = tmp_path / "partial_source"
    items = []
    files = []
    for ordinal in (0, 2):
        frame = prepared["frames"][ordinal]
        output = result_root / frame["output_relative_path"]
        output.parent.mkdir(parents=True, exist_ok=True)
        _rgba_result(output, ordinal + 1)
        items.append({
            "ordinal": ordinal,
            "input_relative_path": frame["relative_path"],
            "input_sha256": frame["sha256"],
            "output_relative_path": frame["output_relative_path"],
            "output_sha256": _sha256(output),
            "status": "SUCCEEDED",
            "job_id": f"job-{ordinal}",
            "node_id": "worker-3090-a",
            "attempts": 1,
        })
        files.append((output, f"results/{frame['output_relative_path']}"))
    manifest = {
        "schema_version": "1.0",
        "batch_id": "batch-partial",
        "external_batch_id": prepared["external_batch_id"],
        "total": 3,
        "items": items,
    }
    archive_path = tmp_path / "partial.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for source, member in files:
            archive.write(source, member)

    output_root = tmp_path / "matte"
    existing = output_root / "previous.png"
    existing.parent.mkdir(parents=True)
    _rgba_result(existing, 99)
    existing_sha = _sha256(existing)
    partial_status = {
        "status": "PARTIAL_SUCCESS",
        "counts": {
            "total": 3,
            "pending": 0,
            "queued": 0,
            "running": 0,
            "succeeded": 2,
            "failed": 1,
            "cancelled": 0,
        },
        "failed_items": [{
            "ordinal": 1,
            "input_relative_path": prepared["frames"][1]["relative_path"],
            "input_sha256": prepared["frames"][1]["sha256"],
            "code": "COMFY_TIMEOUT",
            "message": "timed out after three nodes",
            "node_id": "control-4090",
            "attempts": 3,
            "attempted_node_ids": ["worker-3090-a", "worker-3090-b", "control-4090"],
        }],
    }
    tampered_status = json.loads(json.dumps(partial_status))
    tampered_status["failed_items"][0]["input_relative_path"] = "other/0001.png"
    with pytest.raises(GpuControlError, match="input mapping mismatch"):
        verify_and_publish_result(
            archive_path,
            _sha256(archive_path),
            prepared,
            output_root,
            "COMFY_PARTIAL_TAMPERED",
            preserve_existing=True,
            expected_batch_id="batch-partial",
            expected_external_batch_id=prepared["external_batch_id"],
            allow_partial=True,
            partial_status=tampered_status,
        )
    assert _sha256(existing) == existing_sha
    assert not (output_root / "0000.png").exists()

    published = verify_and_publish_result(
        archive_path,
        _sha256(archive_path),
        prepared,
        output_root,
        "COMFY_PARTIAL",
        preserve_existing=True,
        expected_batch_id="batch-partial",
        expected_external_batch_id=prepared["external_batch_id"],
        allow_partial=True,
        partial_status=partial_status,
    )

    assert [item["ordinal"] for item in published] == [0, 2]
    assert _sha256(existing) == existing_sha
    assert (output_root / "0000.png").is_file()
    assert not (output_root / "0001.png").exists()
    assert (output_root / "0002.png").is_file()


def test_partial_failed_items_require_exact_identity_partition(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    sources = []
    for index in range(3):
        source = input_root / f"{index:04d}.png"
        source.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), (index * 30, 20, 60)).save(source)
        sources.append(source)
    prepared = build_input_batch("COMFY_PARTITION", input_root, sources, tmp_path / "handoff", preserve_structure=True)
    failed_frame = prepared["frames"][1]
    status = {
        "status": "PARTIAL_SUCCESS",
        "counts": {
            "total": 3,
            "pending": 0,
            "queued": 0,
            "running": 0,
            "succeeded": 2,
            "failed": 1,
            "cancelled": 0,
        },
        "failed_items": [{
            "ordinal": 1,
            "input_relative_path": failed_frame["relative_path"],
            "input_sha256": failed_frame["sha256"],
            "code": "GPU_OOM",
            "message": "allocation failed",
            "node_id": "control-4090",
            "attempts": 3,
            "attempted_node_ids": ["worker-3090-a", "worker-3090-b", "control-4090"],
        }],
    }

    validated = validate_partial_failed_items(status, prepared, {0, 2})

    assert [item["ordinal"] for item in validated] == [1]
    tampered = json.loads(json.dumps(status))
    tampered["failed_items"][0]["input_sha256"] = "0" * 64
    with pytest.raises(GpuControlError, match="sha256 mismatch"):
        validate_partial_failed_items(tampered, prepared, {0, 2})


def test_compact_remote_state_preserves_failed_items() -> None:
    failed_items = [{"ordinal": 93, "code": "COMFY_TIMEOUT"}]

    compact = compact_remote_state({"status": "PARTIAL_SUCCESS", "failed_items": failed_items})
    merged = merge_remote_state({"status": "RUNNING"}, {"status": "PARTIAL_SUCCESS", "failed_items": failed_items})

    assert compact["failed_items"] == failed_items
    assert merged["failed_items"] == failed_items


def test_legacy_hybrid_mode_routes_every_real_task_to_gpu_control(monkeypatch) -> None:
    from assetclaw_matting.config import settings

    monkeypatch.setattr(settings, "comfyui_fake_mode", False)
    monkeypatch.setattr(settings, "matting_backend_mode", "hybrid")
    monkeypatch.setattr(settings, "gpu_control_base_url", "https://10.3.34.11")
    monkeypatch.setattr(settings, "gpu_control_large_batch_threshold", 64)
    monkeypatch.setattr(settings, "gpu_control_max_batch_frames", 5000)
    assert hybrid_matting_router.select_matting_backend(12)[0] == "gpu_control"
    assert hybrid_matting_router.select_matting_backend(12)[0] == "gpu_control"
    assert hybrid_matting_router.select_matting_backend(64)[0] == "gpu_control"


def test_gpu_control_only_router_rejects_an_oversized_task(monkeypatch) -> None:
    from assetclaw_matting.config import settings

    monkeypatch.setattr(settings, "comfyui_fake_mode", False)
    monkeypatch.setattr(settings, "matting_backend_mode", "hybrid")
    monkeypatch.setattr(settings, "gpu_control_base_url", "https://10.3.34.11")
    monkeypatch.setattr(settings, "gpu_control_max_batch_frames", 100)
    with pytest.raises(RuntimeError, match="exceeding configured limit"):
        hybrid_matting_router.select_matting_backend(101)


def test_hybrid_router_persists_accepted_scheduler_handshake(monkeypatch) -> None:
    from assetclaw_matting.config import settings

    monkeypatch.setattr(settings, "comfyui_fake_mode", False)
    monkeypatch.setattr(settings, "matting_backend_mode", "hybrid")
    monkeypatch.setattr(settings, "gpu_control_base_url", "https://10.3.34.11")
    monkeypatch.setattr(settings, "gpu_control_large_batch_threshold", 64)
    monkeypatch.setattr(settings, "gpu_control_max_batch_frames", 5000)
    monkeypatch.setattr(hybrid_matting_router, "_active_local_run_count", lambda: 1)
    handshake = {
        "checked": True,
        "ready": True,
        "capacity_supported": True,
        "accepting_batches": True,
        "active_batches": 2,
    }
    monkeypatch.setattr(hybrid_matting_router, "_cluster_handshake", lambda: handshake)

    backend, reason, recorded = hybrid_matting_router.select_matting_backend(12, include_handshake=True)

    assert backend == "gpu_control"
    assert "GPU Control" in reason
    assert recorded == handshake


def test_hybrid_router_keeps_large_batch_remote_when_cluster_is_draining(monkeypatch) -> None:
    from assetclaw_matting.config import settings

    monkeypatch.setattr(settings, "comfyui_fake_mode", False)
    monkeypatch.setattr(settings, "matting_backend_mode", "hybrid")
    monkeypatch.setattr(settings, "gpu_control_base_url", "https://10.3.34.11")
    monkeypatch.setattr(hybrid_matting_router, "_cluster_handshake", lambda: {
        "checked": True,
        "ready": True,
        "capacity_supported": True,
        "accepting_batches": False,
        "reason": "scheduler capacity is full",
    })

    backend, reason, recorded = hybrid_matting_router.select_matting_backend(120, include_handshake=True)

    assert backend == "gpu_control"
    assert "submitting to the server queue" in reason
    assert recorded["accepting_batches"] is False


def test_hybrid_router_queues_small_overflow_remotely_when_local_is_busy(monkeypatch) -> None:
    from assetclaw_matting.config import settings

    monkeypatch.setattr(settings, "comfyui_fake_mode", False)
    monkeypatch.setattr(settings, "matting_backend_mode", "hybrid")
    monkeypatch.setattr(settings, "gpu_control_base_url", "https://10.3.34.11")
    monkeypatch.setattr(settings, "gpu_control_large_batch_threshold", 64)
    monkeypatch.setattr(hybrid_matting_router, "_active_local_run_count", lambda: 1)
    monkeypatch.setattr(hybrid_matting_router, "_cluster_handshake", lambda: {
        "checked": True,
        "ready": True,
        "capacity_supported": True,
        "accepting_batches": False,
        "reason": "scheduler capacity is full",
    })

    backend, reason, recorded = hybrid_matting_router.select_matting_backend(12, include_handshake=True)

    assert backend == "gpu_control"
    assert "server queue" in reason
    assert recorded["accepting_batches"] is False


def test_remote_admission_is_advisory_and_never_waits_for_capacity(monkeypatch) -> None:
    row = {"status": "RUNNING"}
    saved: list[dict] = []

    class FakeClient:
        def scheduler_capacity(self, *, request_id):
            assert request_id == "comfy_advisory_test-admission-000001"
            return {
                "supported": True,
                "accepting_batches": False,
                "suggested_max_new_batches": 0,
                "queue_depth": 9,
                "active_batches": 3,
                "idle_nodes": 0,
            }

    monkeypatch.setattr(comfyui_skills, "_get_run", lambda _run_id: row)
    monkeypatch.setattr(
        comfyui_skills,
        "_save_run_progress",
        lambda _run_id, _prompt_ids, options: saved.append(json.loads(json.dumps(options))),
    )
    options: dict = {}
    remote_state: dict = {}

    comfyui_skills._wait_for_gpu_control_admission(
        "COMFY_ADVISORY_TEST",
        FakeClient(),
        options,
        remote_state,
        [],
    )

    admission = saved[-1]["gpu_control"]["admission"]
    assert admission["status"] == "SUBMITTING"
    assert admission["policy"] == "server_queue_authoritative"
    assert admission["accepting_batches"] is False
    assert admission["suggested_max_new_batches"] == 0


def test_remote_worker_submits_validates_and_publishes_one_isolated_batch(monkeypatch, tmp_path: Path) -> None:
    from assetclaw_matting.config import settings

    input_root = tmp_path / "input"
    source = input_root / "video_01" / "frame_0001.png"
    source.parent.mkdir(parents=True)
    Image.new("RGB", (16, 16), (220, 30, 50)).save(source)
    output_root = tmp_path / "matte"
    run_id = "COMFY_REMOTE_TEST"
    options = {
        "matting_backend": "gpu_control",
        "external_batch_id": "assetclaw:VID_TEST:matting:g1",
        "preserve_structure": True,
        "skip_existing": False,
        "strict_frame_identity": False,
        "cluster_parameters": {},
        "prompt_map": [],
    }
    row = {
        "id": run_id,
        "status": "RUNNING",
        "workflow_path": str(tmp_path / "unused.json"),
        "input_dir": str(input_root),
        "output_dir": str(output_root),
        "total": 1,
        "files_json": json.dumps([str(source)]),
        "prompt_ids_json": "[]",
        "options_json": json.dumps(options),
    }
    calls: dict[str, str] = {}

    class FakeClient:
        def __init__(self) -> None:
            self.result_archive = tmp_path / "fake_remote_result.zip"
            self.artifact_sha = ""

        def create_batch(self, archive_path, manifest, *, idempotency_key, request_id):
            calls["idempotency_key"] = idempotency_key
            calls["request_id"] = request_id
            frame = manifest["frames"][0]
            rgba = tmp_path / "remote.png"
            _rgba_result(rgba, 7)
            result_manifest = {
                "schema_version": "1.0",
                "batch_id": "batch-1",
                "external_batch_id": manifest["external_batch_id"],
                "total": 1,
                "items": [
                    {
                        "ordinal": 0,
                        "input_relative_path": frame["relative_path"],
                        "input_sha256": frame["sha256"],
                        "output_relative_path": "video_01/frame_0001.png",
                        "output_sha256": _sha256(rgba),
                        "status": "SUCCEEDED",
                        "job_id": "job-1",
                        "node_id": "worker-3090-a",
                        "attempts": 1,
                    }
                ],
            }
            with zipfile.ZipFile(self.result_archive, "w", compression=zipfile.ZIP_STORED) as archive:
                archive.writestr("manifest.json", json.dumps(result_manifest))
                archive.write(rgba, "results/video_01/frame_0001.png")
            self.artifact_sha = _sha256(self.result_archive)
            return {
                "batch_id": "batch-1",
                "external_batch_id": manifest["external_batch_id"],
                "status": "QUEUED",
            }

        def get_batch(self, batch_id, *, request_id=None):
            return {
                "batch_id": batch_id,
                "external_batch_id": options["external_batch_id"],
                "status": "SUCCEEDED",
                "progress": 100,
                "counts": {"total": 1, "queued": 0, "running": 0, "succeeded": 1, "failed": 0, "cancelled": 0},
                "node_distribution": {"worker-3090-a": 1},
                "artifacts": [
                    {
                        "id": "artifact-1",
                        "kind": "result_archive",
                        "filename": "batch-1-rgba.zip",
                        "content_type": "application/zip",
                        "size_bytes": self.result_archive.stat().st_size,
                        "sha256": self.artifact_sha,
                        "download_url": "/artifact/result.zip",
                    }
                ],
            }

        def download_artifact(self, artifact, destination, *, request_id=None):
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.result_archive, destination)
            return {"path": str(destination), "sha256": self.artifact_sha, "size_bytes": destination.stat().st_size}

        def cancel_batch(self, *args, **kwargs):
            calls["cancel_count"] = int(calls.get("cancel_count") or 0) + 1
            raise AssertionError("local watchdog must never cancel the remote parent")

    def fake_get_run(_run_id):
        return row

    def fake_save_progress(_run_id, prompt_ids, updated_options):
        row["prompt_ids_json"] = json.dumps(prompt_ids)
        row["options_json"] = json.dumps(updated_options)

    def fake_set_status(_run_id, status):
        row["status"] = status

    monkeypatch.setattr(settings, "storage_dir", tmp_path / "storage")
    monkeypatch.setattr(settings, "gpu_control_execution_timeout_seconds", -1)
    monkeypatch.setattr(comfyui_skills, "_get_run", fake_get_run)
    monkeypatch.setattr(comfyui_skills, "_save_run_progress", fake_save_progress)
    monkeypatch.setattr(comfyui_skills, "_set_run_status", fake_set_status)
    monkeypatch.setattr(comfyui_skills, "_notify", lambda *args, **kwargs: None)
    monkeypatch.setattr("assetclaw_matting.services.gpu_control_batch.GpuControlBatchClient", FakeClient)

    comfyui_skills._run_gpu_control_worker(run_id)

    assert row["status"] == "DONE"
    assert calls["idempotency_key"] == options["external_batch_id"]
    assert calls["request_id"] == "comfy_remote_test-create-01"
    assert (output_root / "video_01" / "frame_0001.png").is_file()
    saved_options = json.loads(row["options_json"])
    assert saved_options["gpu_control"]["batch_id"] == "batch-1"
    assert saved_options["gpu_control"]["watchdog_action"] == "OBSERVE_AND_RECONCILE_WITHOUT_CANCEL"
    assert int(calls.get("cancel_count") or 0) == 0
    assert saved_options["prompt_map"][0]["src_path"] == str(source)


def test_remote_cancel_waits_for_terminal_and_uses_frozen_key(monkeypatch) -> None:
    row = {
        "id": "COMFY_CANCEL_TEST",
        "status": "RUNNING",
        "prompt_ids_json": "[]",
        "options_json": json.dumps(
            {
                "matting_backend": "gpu_control",
                "external_batch_id": "assetclaw:VID_CANCEL:matting:g1",
                "gpu_control": {
                    "batch_id": "batch-cancel",
                    "status": "RUNNING",
                    "progress": 30,
                },
            }
        ),
    }
    calls: dict[str, str] = {}

    class FakeClient:
        def cancel_batch(self, batch_id, *, idempotency_key, request_id=None):
            persisted_before_request = json.loads(row["options_json"])["gpu_control"]["cancel_intent"]
            assert persisted_before_request["reason"] == "explicit_user_request"
            assert persisted_before_request["idempotency_key"] == "assetclaw:VID_CANCEL:matting:g1:cancel"
            calls.update(
                batch_id=batch_id,
                idempotency_key=idempotency_key,
                request_id=request_id or "",
            )
            return {
                "batch_id": batch_id,
                "external_batch_id": "assetclaw:VID_CANCEL:matting:g1",
                "status": "CANCELLING",
                "progress": 30,
                "_response_meta": {"http_status": 202, "request_id": "server-cancel-1"},
            }

    def save_progress(_run_id, prompt_ids, options):
        row["prompt_ids_json"] = json.dumps(prompt_ids)
        row["options_json"] = json.dumps(options)

    monkeypatch.setattr(comfyui_skills, "_get_run", lambda _run_id: row)
    monkeypatch.setattr(comfyui_skills, "_save_run_progress", save_progress)
    monkeypatch.setattr(comfyui_skills, "_set_run_status", lambda _run_id, status: row.update(status=status))
    monkeypatch.setattr(comfyui_skills, "_notify", lambda *args, **kwargs: None)
    monkeypatch.setattr("assetclaw_matting.services.gpu_control_batch.GpuControlBatchClient", FakeClient)

    result = comfyui_skills.run_cancel(row["id"], notify=False)

    assert result["ok"] is True
    assert result["status"] == "CANCELING"
    assert row["status"] == "CANCELING"
    assert calls["idempotency_key"] == "assetclaw:VID_CANCEL:matting:g1:cancel"
    assert calls["request_id"] == "comfy_cancel_test-cancel-01"
    saved = json.loads(row["options_json"])["gpu_control"]
    assert saved["status"] == "CANCELLING"
    assert saved["cancel_response_meta"]["request_id"] == "server-cancel-1"
    assert saved["cancel_intent"]["actor"] == "assetclaw_operator"
    assert saved["cancel_intent"]["accepted_at"]


def test_remote_cancelled_without_local_intent_fails_closed(monkeypatch, tmp_path: Path) -> None:
    from assetclaw_matting.config import settings

    input_root = tmp_path / "input"
    source = input_root / "frame.png"
    input_root.mkdir()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(source)
    run_id = "COMFY_ILLEGAL_CANCEL"
    options = {
        "matting_backend": "gpu_control",
        "external_batch_id": "assetclaw:VID_ILLEGAL:matting:g1",
        "preserve_structure": True,
        "skip_existing": False,
        "strict_frame_identity": False,
        "cluster_parameters": {},
        "prompt_map": [],
    }
    row = {
        "id": run_id,
        "status": "RUNNING",
        "workflow_path": str(tmp_path / "unused.json"),
        "input_dir": str(input_root),
        "output_dir": str(tmp_path / "output"),
        "total": 1,
        "files_json": json.dumps([str(source)]),
        "prompt_ids_json": "[]",
        "options_json": json.dumps(options),
    }

    class FakeClient:
        def create_batch(self, _archive_path, manifest, *, idempotency_key, request_id):
            return {
                "batch_id": "batch-illegal-cancel",
                "external_batch_id": manifest["external_batch_id"],
                "status": "QUEUED",
            }

        def get_batch(self, batch_id, *, request_id=None):
            return {
                "batch_id": batch_id,
                "external_batch_id": options["external_batch_id"],
                "status": "CANCELLED",
                "progress": 10,
            }

        def cancel_batch(self, *args, **kwargs):
            raise AssertionError("worker must not manufacture a cancel intent")

    def save_progress(_run_id, prompt_ids, updated_options):
        row["prompt_ids_json"] = json.dumps(prompt_ids)
        row["options_json"] = json.dumps(updated_options)

    monkeypatch.setattr(settings, "storage_dir", tmp_path / "storage")
    monkeypatch.setattr(settings, "gpu_control_execution_timeout_seconds", 30)
    monkeypatch.setattr(comfyui_skills, "_get_run", lambda _run_id: row)
    monkeypatch.setattr(comfyui_skills, "_save_run_progress", save_progress)
    monkeypatch.setattr(comfyui_skills, "_set_run_status", lambda _run_id, status: row.update(status=status))
    monkeypatch.setattr(comfyui_skills, "_notify", lambda *args, **kwargs: None)
    monkeypatch.setattr("assetclaw_matting.services.gpu_control_batch.GpuControlBatchClient", FakeClient)

    comfyui_skills._run_gpu_control_worker(run_id)

    assert row["status"] == "FAILED"
    remote = json.loads(row["options_json"])["gpu_control"]
    assert remote["illegal_remote_cancelled"]["reason"] == "remote_cancelled_without_local_cancel_intent"
    assert "without a persisted local cancel intent" in remote["client_error"]
