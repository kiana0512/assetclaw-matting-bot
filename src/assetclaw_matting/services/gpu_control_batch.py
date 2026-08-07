from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import shutil
import time
import unicodedata
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from PIL import Image, UnidentifiedImageError
from requests.adapters import HTTPAdapter

from assetclaw_matting.comfyui.output_resolver import inspect_local_png


TERMINAL_BATCH_STATUSES = {"SUCCEEDED", "PARTIAL_SUCCESS", "FAILED", "CANCELLED"}
RETRYABLE_HTTP_STATUSES = {429, 500, 502, 503, 504}
APPROVED_WORKFLOW_KEY = "imageclip-rgba"
APPROVED_WORKFLOW_VERSION = "2026.07.30-691770c-r1"
APPROVED_PIPELINE_COMMIT = "691770cd6a59fd7c51391456fe900dc57a313233"
APPROVED_PIPELINE_SHA256 = "00e7104762f0a1fdf3a4c20e043bec2b9f088132452d5a5ce4302ba268edac0b"
APPROVED_OUTPUT_NODE = "SaveImage #25"
V2_ALLOWED_INPUT_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
V2_MAX_FRAMES = 5000
V2_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
V2_MAX_FRAME_BYTES = 64 * 1024 * 1024
V2_MAX_IMAGE_PIXELS = 40_000_000
V2_MAX_ARCHIVE_BYTES = 100 * 1024 * 1024 * 1024


class GpuControlError(RuntimeError):
    pass


class _IncompleteArtifactDownload(GpuControlError):
    """A transport interruption that can be retried from the persisted .part file."""


def _normalize_scheduler_capacity(payload: dict[str, Any]) -> dict[str, Any]:
    """Flatten the V3 capacity shape while preserving legacy V2 extension keys."""

    result = dict(payload or {})
    cluster = result.get("cluster") if isinstance(result.get("cluster"), dict) else {}
    client = result.get("client") if isinstance(result.get("client"), dict) else {}
    if result.get("accepting_batches") is None:
        accepting = result.get("accepting")
        if accepting is None and cluster.get("available_slots") is not None:
            accepting = int(cluster.get("available_slots") or 0) > 0
        result["accepting_batches"] = accepting
    result.setdefault("queue_depth", cluster.get("queued_jobs"))
    result.setdefault("active_batches", cluster.get("running_jobs"))
    result.setdefault("idle_nodes", cluster.get("available_slots"))
    result.setdefault("online_nodes", cluster.get("eligible_nodes"))
    if result.get("suggested_max_new_batches") is None:
        available = int(cluster.get("available_slots") or 0)
        client_room = max(0, int(client.get("max_queued") or available) - int(client.get("queued_jobs") or 0))
        result["suggested_max_new_batches"] = min(available, client_room)
    return result


class _CaBundleAdapter(HTTPAdapter):
    """Use the pinned LAN CA while optionally relaxing OpenSSL 3 strict extension checks."""

    def __init__(self, ca_bundle: str, *, allow_missing_key_usage: bool) -> None:
        self.ca_bundle = ca_bundle
        self.allow_missing_key_usage = allow_missing_key_usage
        super().__init__()

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        context = ssl.create_default_context(cafile=self.ca_bundle)
        if self.allow_missing_key_usage and hasattr(ssl, "VERIFY_X509_STRICT"):
            context.verify_flags &= ~ssl.VERIFY_X509_STRICT
        pool_kwargs["ssl_context"] = context
        return super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)


class GpuControlBatchClient:
    def __init__(self) -> None:
        from assetclaw_matting.config import settings

        self.base_url = str(settings.gpu_control_base_url or "").rstrip("/")
        self.api_key = str(settings.gpu_control_api_key or "").strip()
        self.verify = str(settings.gpu_control_ca_bundle) if settings.gpu_control_ca_bundle else bool(settings.gpu_control_verify_tls)
        self.connect_timeout = max(1, int(settings.gpu_control_connect_timeout_seconds or 15))
        self.upload_timeout = max(30, int(settings.gpu_control_upload_timeout_seconds or 86400))
        self.download_timeout = max(30, int(settings.gpu_control_download_timeout_seconds or 1800))
        self.request_timeout = max(5, int(settings.gpu_control_request_timeout_seconds or 30))
        self.retries = max(1, int(settings.gpu_control_request_retries or 3))
        if not self.base_url:
            raise GpuControlError("GPU_CONTROL_BASE_URL is empty")
        self.session = requests.Session()
        self.session.trust_env = False
        if settings.gpu_control_ca_bundle and settings.gpu_control_allow_ca_without_key_usage:
            self.session.mount(
                "https://",
                _CaBundleAdapter(
                    str(settings.gpu_control_ca_bundle),
                    allow_missing_key_usage=True,
                ),
            )
            self.verify = True

    def health_live(self, *, request_id: str | None = None) -> dict[str, Any]:
        response = self.session.get(
            self._url("/health/live"),
            headers=self._headers(request_id=request_id),
            timeout=(self.connect_timeout, self.request_timeout),
            verify=self.verify,
        )
        if response.status_code != 200:
            _raise_response(response, "health live")
        payload = _response_json(response)
        payload["_response_meta"] = _response_meta(response)
        return payload

    def health_ready(self, *, request_id: str | None = None) -> dict[str, Any]:
        response = self.session.get(
            self._url("/health/ready"),
            headers=self._headers(request_id=request_id),
            timeout=(self.connect_timeout, self.request_timeout),
            verify=self.verify,
        )
        if response.status_code != 200:
            _raise_response(response, "health ready")
        payload = _response_json(response)
        payload["_response_meta"] = _response_meta(response)
        return payload

    def scheduler_capacity(self, *, request_id: str | None = None) -> dict[str, Any]:
        """Return the optional scheduler-capacity handshake without weakening V2.

        GPU Control V2 did not freeze a capacity endpoint.  A 404 therefore
        means "extension not installed" and callers must fall back to
        ``/health/ready`` plus their persisted in-flight count.
        """

        response = self.session.get(
            self._url("/api/v1/scheduler/capacity"),
            headers=self._headers(request_id=request_id),
            timeout=(self.connect_timeout, self.request_timeout),
            verify=self.verify,
        )
        if response.status_code == 404:
            return {
                "supported": False,
                "accepting_batches": None,
                "_response_meta": _response_meta(response),
            }
        if response.status_code != 200:
            _raise_response(response, "scheduler capacity")
        payload = _normalize_scheduler_capacity(_response_json(response))
        payload["supported"] = True
        payload["_response_meta"] = _response_meta(response)
        return payload

    def create_batch(
        self,
        archive_path: Path,
        manifest: dict[str, Any],
        *,
        idempotency_key: str,
        request_id: str,
    ) -> dict[str, Any]:
        url = self._url("/api/v1/batches/imageclip-rgba")
        encoded_manifest = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                with archive_path.open("rb") as archive_handle:
                    response = self.session.post(
                        url,
                        headers=self._headers(idempotency_key=idempotency_key, request_id=request_id),
                        files={
                            "archive": (archive_path.name, archive_handle, "application/zip"),
                        },
                        data={"manifest": encoded_manifest},
                        timeout=(self.connect_timeout, self.upload_timeout),
                        verify=self.verify,
                    )
                if response.status_code in {200, 202}:
                    payload = _response_json(response)
                    if not payload.get("batch_id"):
                        raise GpuControlError("GPU Control create response has no batch_id")
                    payload["_response_meta"] = _response_meta(response)
                    return payload
                if response.status_code not in RETRYABLE_HTTP_STATUSES:
                    _raise_response(response, "create batch")
                last_error = GpuControlError(_response_error_text(response, "create batch"))
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
            if attempt < self.retries:
                time.sleep(min(2 ** (attempt - 1), 8))
        raise GpuControlError(f"create batch failed after {self.retries} attempts: {last_error}")

    def get_batch(self, batch_id: str, *, request_id: str | None = None) -> dict[str, Any]:
        response = self.session.get(
            self._url(f"/api/v1/batches/{batch_id}"),
            headers=self._headers(request_id=request_id),
            timeout=(self.connect_timeout, self.request_timeout),
            verify=self.verify,
        )
        if response.status_code != 200:
            _raise_response(response, "get batch")
        payload = _response_json(response)
        payload["_response_meta"] = _response_meta(response)
        return payload

    def get_batch_manifest(
        self,
        batch_id: str,
        *,
        offset: int = 0,
        limit: int = 500,
        status: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        if offset < 0 or not 1 <= limit <= 500:
            raise GpuControlError("manifest pagination requires offset >= 0 and limit 1-500")
        params: dict[str, Any] = {"offset": offset, "limit": limit}
        if status:
            params["status"] = status
        response = self.session.get(
            self._url(f"/api/v1/batches/{batch_id}/manifest"),
            headers=self._headers(request_id=request_id),
            params=params,
            timeout=(self.connect_timeout, self.request_timeout),
            verify=self.verify,
        )
        if response.status_code != 200:
            _raise_response(response, "get batch manifest")
        payload = _response_json(response)
        payload["_response_meta"] = _response_meta(response)
        return payload

    def cancel_batch(self, batch_id: str, *, idempotency_key: str, request_id: str | None = None) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                response = self.session.post(
                    self._url(f"/api/v1/batches/{batch_id}/cancel"),
                    headers=self._headers(idempotency_key=idempotency_key, request_id=request_id),
                    timeout=(self.connect_timeout, self.request_timeout),
                    verify=self.verify,
                )
                if response.status_code in {200, 202}:
                    payload = _response_json(response)
                    payload["_response_meta"] = _response_meta(response)
                    return payload
                if response.status_code not in RETRYABLE_HTTP_STATUSES:
                    _raise_response(response, "cancel batch")
                last_error = GpuControlError(_response_error_text(response, "cancel batch"))
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
            if attempt < self.retries:
                time.sleep(min(2 ** (attempt - 1), 8))
        raise GpuControlError(f"cancel batch failed after {self.retries} attempts: {last_error}")

    def download_artifact(self, artifact: dict[str, Any], destination: Path, *, request_id: str | None = None) -> dict[str, Any]:
        raw_url = str(artifact.get("download_url") or "")
        expected_sha = str(artifact.get("sha256") or "").lower()
        expected_size = int(artifact.get("size_bytes") or 0)
        if not raw_url or len(expected_sha) != 64 or expected_size <= 0:
            raise GpuControlError("result artifact is missing download_url, sha256, or size_bytes")
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")
        if destination.is_file() and destination.stat().st_size == expected_size:
            existing_sha = _sha256_file(destination)
            if existing_sha == expected_sha:
                return {
                    "path": str(destination),
                    "sha256": existing_sha,
                    "header_sha256": expected_sha,
                    "size_bytes": expected_size,
                    "request_id": "",
                    "attempts": 0,
                    "resumed_from_bytes": expected_size,
                    "cache_hit": True,
                }

        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            response: requests.Response | None = None
            offset = partial.stat().st_size if partial.is_file() else 0
            if offset > expected_size:
                partial.unlink()
                offset = 0
            if offset == expected_size:
                actual_sha = _sha256_file(partial)
                if actual_sha == expected_sha:
                    os.replace(partial, destination)
                    return {
                        "path": str(destination),
                        "sha256": actual_sha,
                        "header_sha256": expected_sha,
                        "size_bytes": expected_size,
                        "request_id": "",
                        "attempts": attempt - 1,
                        "resumed_from_bytes": expected_size,
                        "cache_hit": True,
                    }
                partial.unlink()
                offset = 0

            digest = hashlib.sha256()
            if offset:
                with partial.open("rb") as existing:
                    for chunk in iter(lambda: existing.read(1024 * 1024), b""):
                        digest.update(chunk)
            headers = self._headers(request_id=request_id)
            if offset:
                headers["Range"] = f"bytes={offset}-"
            try:
                response = self.session.get(
                    self._url(raw_url),
                    headers=headers,
                    timeout=(self.connect_timeout, self.download_timeout),
                    verify=self.verify,
                    stream=True,
                )
                if response.status_code not in {200, 206}:
                    if response.status_code in RETRYABLE_HTTP_STATUSES:
                        raise _IncompleteArtifactDownload(_response_error_text(response, "download artifact"))
                    _raise_response(response, "download artifact")
                response_header_sha = str(response.headers.get("X-Artifact-SHA256") or "").lower()
                if response_header_sha != expected_sha:
                    if partial.exists():
                        partial.unlink()
                    raise GpuControlError(
                        f"artifact response header sha256 mismatch: expected {expected_sha}, got {response_header_sha or 'missing'}"
                    )

                resumed_from = offset if offset and response.status_code == 206 else 0
                if resumed_from:
                    content_range = str(response.headers.get("Content-Range") or "")
                    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range)
                    if not match or int(match.group(1)) != offset or int(match.group(3)) != expected_size:
                        partial.unlink(missing_ok=True)
                        raise GpuControlError(f"invalid Content-Range for resumed artifact download: {content_range or 'missing'}")
                    mode = "ab"
                else:
                    offset = 0
                    digest = hashlib.sha256()
                    mode = "wb"

                with partial.open(mode) as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        handle.write(chunk)
                        digest.update(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
                actual_size = partial.stat().st_size
                if actual_size < expected_size:
                    raise _IncompleteArtifactDownload(
                        f"result archive download ended early: expected {expected_size}, got {actual_size}"
                    )
                if actual_size > expected_size:
                    partial.unlink(missing_ok=True)
                    raise GpuControlError(
                        f"result archive size mismatch: expected {expected_size}, got {actual_size}"
                    )
                actual_sha = digest.hexdigest()
                if actual_sha != expected_sha:
                    partial.unlink(missing_ok=True)
                    raise GpuControlError(f"result archive sha256 mismatch: expected {expected_sha}, got {actual_sha}")
                os.replace(partial, destination)
                return {
                    "path": str(destination),
                    "sha256": actual_sha,
                    "header_sha256": response_header_sha,
                    "size_bytes": destination.stat().st_size,
                    "request_id": response.headers.get("X-Request-ID") or "",
                    "attempts": attempt,
                    "resumed_from_bytes": resumed_from,
                    "cache_hit": False,
                }
            except (requests.Timeout, requests.ConnectionError, _IncompleteArtifactDownload) as exc:
                last_error = exc
            finally:
                if response is not None:
                    response.close()
            if attempt < self.retries:
                time.sleep(min(2 ** (attempt - 1), 8))
        raise GpuControlError(f"download artifact failed after {self.retries} attempts: {last_error}")

    def _headers(self, *, idempotency_key: str | None = None, request_id: str | None = None) -> dict[str, str]:
        resolved_request_id = request_id or f"assetclaw-{uuid.uuid4().hex}"
        if len(resolved_request_id) > 64 or not re.fullmatch(r"[A-Za-z0-9._:-]+", resolved_request_id):
            raise GpuControlError("X-Request-ID must use at most 64 characters from [A-Za-z0-9._:-]")
        headers = {"X-Request-ID": resolved_request_id}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        if idempotency_key:
            if not 1 <= len(idempotency_key) <= 128 or any(ord(char) < 32 or ord(char) > 126 for char in idempotency_key):
                raise GpuControlError("Idempotency-Key must be 1-128 printable ASCII characters")
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def _url(self, path_or_url: str) -> str:
        if path_or_url.lower().startswith(("http://", "https://")):
            requested = urlparse(path_or_url)
            configured = urlparse(self.base_url)
            if (requested.scheme.lower(), requested.netloc.lower()) != (configured.scheme.lower(), configured.netloc.lower()):
                raise GpuControlError("GPU Control artifact URL points outside the configured service origin")
            return path_or_url
        return urljoin(self.base_url + "/", path_or_url.lstrip("/"))


def build_input_batch(
    run_id: str,
    input_root: Path,
    files: list[Path],
    workspace: Path,
    *,
    preserve_structure: bool,
    external_batch_id: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create the immutable ZIP+manifest handoff for one COMFY parent run."""

    workspace.mkdir(parents=True, exist_ok=True)
    archive_path = workspace / "input.zip"
    manifest_path = workspace / "input_manifest.json"
    frames: list[dict[str, Any]] = []
    output_keys: set[str] = set()
    input_keys: set[str] = set()
    if not 1 <= len(files) <= V2_MAX_FRAMES:
        raise GpuControlError(f"V2 batch must contain 1-{V2_MAX_FRAMES} frames")
    if parameters:
        raise GpuControlError("GPU Control V2 requires parameters to be exactly {}")
    for ordinal, file_path in enumerate(files):
        _validate_v2_input_image(file_path)
        relative_path = _input_relative_path(input_root, file_path, preserve_structure)
        input_key = _normalized_collision_key(relative_path)
        output_relative_path = str(PurePosixPath(relative_path).with_suffix(".png"))
        output_key = _normalized_collision_key(output_relative_path)
        if input_key in input_keys:
            raise GpuControlError(f"duplicate normalized input path: {relative_path}")
        if output_key in output_keys:
            raise GpuControlError(f"OUTPUT_PATH_CONFLICT: {output_relative_path}")
        input_keys.add(input_key)
        output_keys.add(output_key)
        frames.append(
            {
                "ordinal": ordinal,
                "relative_path": relative_path,
                "size_bytes": file_path.stat().st_size,
                "sha256": _sha256_file(file_path),
                "source_path": str(file_path),
                "output_relative_path": output_relative_path,
            }
        )

    public_frames = [{key: value for key, value in item.items() if key not in {"source_path", "output_relative_path"}} for item in frames]
    resolved_external_id = external_batch_id or f"assetclaw:{run_id}:matting:g1"
    if (
        not 1 <= len(resolved_external_id) <= 128
        or resolved_external_id.strip() != resolved_external_id
        or any(ord(char) < 32 or ord(char) > 126 for char in resolved_external_id)
    ):
        raise GpuControlError("external_batch_id must be 1-128 printable ASCII characters")
    manifest = {
        "schema_version": "1.0",
        "external_batch_id": resolved_external_id,
        "failure_policy": "all_or_nothing",
        "output_naming": "preserve_stem_png",
        "parameters": dict(parameters or {}),
        "frames": public_frames,
    }
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    if len(manifest_bytes) > V2_MAX_MANIFEST_BYTES:
        raise GpuControlError("GPU Control V2 manifest exceeds 4 MiB")
    manifest_sha = hashlib.sha256(_canonical_json(manifest)).hexdigest()
    if archive_path.exists() or manifest_path.exists():
        if not archive_path.is_file() or not manifest_path.is_file():
            raise GpuControlError("incomplete persisted GPU Control input handoff; refusing an ambiguous retry")
        try:
            persisted_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise GpuControlError("persisted GPU Control input manifest is unreadable") from exc
        if _canonical_json(persisted_manifest) != _canonical_json(manifest):
            raise GpuControlError("input files changed after this idempotency key was allocated")
        _verify_input_archive(archive_path, frames)
    else:
        archive_partial = archive_path.with_suffix(".zip.part")
        manifest_partial = manifest_path.with_suffix(".json.part")
        try:
            with zipfile.ZipFile(archive_partial, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                for item in frames:
                    archive.write(item["source_path"], item["relative_path"])
            manifest_partial.write_bytes(manifest_bytes)
            os.replace(archive_partial, archive_path)
            os.replace(manifest_partial, manifest_path)
        finally:
            for partial in (archive_partial, manifest_partial):
                if partial.exists():
                    partial.unlink()
    if archive_path.stat().st_size > V2_MAX_ARCHIVE_BYTES:
        raise GpuControlError("GPU Control V2 input archive exceeds 100 GiB")
    return {
        "archive_path": str(archive_path),
        "manifest_path": str(manifest_path),
        "manifest": manifest,
        "manifest_sha256": manifest_sha,
        "frames": frames,
        "external_batch_id": resolved_external_id,
        "idempotency_key": resolved_external_id,
    }


def verify_and_publish_result(
    archive_path: Path,
    artifact_sha256: str,
    expected_batch: dict[str, Any],
    output_root: Path,
    run_id: str,
    *,
    strict_frame_identity: bool = False,
    preserve_existing: bool = False,
    expected_batch_id: str = "",
    expected_external_batch_id: str = "",
    allow_partial: bool = False,
    partial_status: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Verify every mapping/hash/image, then atomically publish one task tree."""

    if _sha256_file(archive_path) != str(artifact_sha256).lower():
        raise GpuControlError("downloaded result archive no longer matches the accepted artifact sha256")
    parent = output_root.parent.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    staging = parent / f".{output_root.name}.{run_id}.staging"
    backup = parent / f".{output_root.name}.{run_id}.backup"
    for candidate in (staging, backup):
        _assert_child(candidate, parent)
        if candidate.exists():
            shutil.rmtree(candidate)
    staging.mkdir(parents=True, exist_ok=False)
    if preserve_existing and output_root.is_dir():
        shutil.copytree(output_root, staging, dirs_exist_ok=True)

    expected_frames = list(expected_batch.get("frames") or [])
    expected_by_ordinal = {int(item["ordinal"]): item for item in expected_frames}
    if sorted(expected_by_ordinal) != list(range(len(expected_frames))):
        raise GpuControlError("local input manifest ordinal is not continuous")

    published: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            damaged = archive.testzip()
            if damaged:
                raise GpuControlError(f"result zip integrity check failed: {damaged}")
            member_map = _validated_archive_members(archive)
            manifest_name = "manifest.json"
            if manifest_name not in member_map:
                raise GpuControlError("result zip has no manifest.json")
            if member_map[manifest_name].compress_type != zipfile.ZIP_STORED:
                raise GpuControlError("result manifest.json is not ZIP_STORED")
            result_manifest = json.loads(archive.read(member_map[manifest_name]).decode("utf-8"))
            if str(result_manifest.get("schema_version") or "") != "1.0":
                raise GpuControlError("result manifest schema_version must be 1.0")
            if expected_batch_id and str(result_manifest.get("batch_id") or "") != expected_batch_id:
                raise GpuControlError("result manifest batch_id mismatch")
            if expected_external_batch_id and str(result_manifest.get("external_batch_id") or "") != expected_external_batch_id:
                raise GpuControlError("result manifest external_batch_id mismatch")
            required_manifest_fields = {"schema_version", "batch_id", "external_batch_id", "total", "items"}
            workflow_identity_fields = {
                "workflow_key",
                "workflow_version",
                "pipeline_commit",
                "pipeline_sha256",
                "output_node",
            }
            manifest_fields = set(result_manifest)
            missing_manifest_fields = required_manifest_fields - manifest_fields
            unknown_manifest_fields = manifest_fields - required_manifest_fields - workflow_identity_fields
            if missing_manifest_fields:
                raise GpuControlError(
                    f"result manifest is missing required fields: {sorted(missing_manifest_fields)}"
                )
            if unknown_manifest_fields:
                raise GpuControlError(
                    f"result manifest contains unsupported fields: {sorted(unknown_manifest_fields)}"
                )
            if manifest_fields & workflow_identity_fields:
                validate_workflow_identity(result_manifest)
            result_items = list(result_manifest.get("items") or [])
            if int(result_manifest.get("total") or len(result_items)) != len(expected_frames):
                raise GpuControlError("result manifest total does not match the submitted frame count")
            if not allow_partial and len(result_items) != len(expected_frames):
                raise GpuControlError("result manifest item count does not match the submitted frame count")
            if allow_partial and not 0 < len(result_items) < len(expected_frames):
                raise GpuControlError("partial result manifest must contain a non-empty proper subset of frames")

            expected_archive_entries: set[str] = {"manifest.json"}
            result_ordinals = [int(item.get("ordinal", -1)) for item in result_items]
            if allow_partial:
                if result_ordinals != sorted(set(result_ordinals)):
                    raise GpuControlError("partial result manifest ordinals must be unique and sorted")
            elif result_ordinals != list(range(len(expected_frames))):
                raise GpuControlError("result manifest ordinal order is not exactly 0..N-1")
            for item in result_items:
                if set(item) != {
                    "ordinal",
                    "input_relative_path",
                    "input_sha256",
                    "output_relative_path",
                    "output_sha256",
                    "status",
                    "job_id",
                    "node_id",
                    "attempts",
                }:
                    raise GpuControlError("result item fields do not match GPU Control V2")
                ordinal = int(item.get("ordinal", -1))
                if ordinal not in expected_by_ordinal:
                    raise GpuControlError(f"unexpected result ordinal: {ordinal}")
                expected = expected_by_ordinal[ordinal]
                input_relative = _normalize_relative_path(str(item.get("input_relative_path") or item.get("relative_path") or ""))
                if input_relative != expected["relative_path"]:
                    raise GpuControlError(f"result input mapping mismatch at ordinal {ordinal}")
                if str(item.get("input_sha256") or "").lower() != expected["sha256"]:
                    raise GpuControlError(f"result input sha256 mismatch at ordinal {ordinal}")
                item_status = str(item.get("status") or "SUCCEEDED").upper()
                if item_status != "SUCCEEDED":
                    raise GpuControlError(f"result item is not SUCCEEDED at ordinal {ordinal}: {item_status}")
                output_relative = _normalize_relative_path(str(item.get("output_relative_path") or ""))
                if output_relative != expected["output_relative_path"]:
                    raise GpuControlError(f"result output mapping mismatch at ordinal {ordinal}")
                output_sha = str(item.get("output_sha256") or "").lower()
                if len(output_sha) != 64:
                    raise GpuControlError(f"result output sha256 missing at ordinal {ordinal}")
                member_name = _normalize_relative_path(f"results/{output_relative}")
                expected_archive_entries.add(member_name)
                if member_name not in member_map:
                    raise GpuControlError(f"result zip is missing {member_name}")
                if member_map[member_name].compress_type != zipfile.ZIP_STORED:
                    raise GpuControlError(f"result zip entry is not ZIP_STORED: {member_name}")
                target = (staging / Path(*PurePosixPath(output_relative).parts)).resolve()
                _assert_child(target, staging.resolve())
                target.parent.mkdir(parents=True, exist_ok=True)
                payload = archive.read(member_map[member_name])
                if hashlib.sha256(payload).hexdigest() != output_sha:
                    raise GpuControlError(f"result frame sha256 mismatch at ordinal {ordinal}")
                target.write_bytes(payload)
                quality = inspect_local_png(target)
                if not quality.get("valid"):
                    raise GpuControlError(
                        f"result is not a valid final transparent PNG at ordinal {ordinal}: {quality.get('reason')}"
                    )
                if strict_frame_identity:
                    from assetclaw_matting.skills.sequence_integrity import validate_matte_identity

                    identity = validate_matte_identity(expected["source_path"], target)
                else:
                    identity = {}
                published.append(
                    {
                        "ordinal": ordinal,
                        "src_path": expected["source_path"],
                        "rel_path": output_relative,
                        "dst_path": str(output_root / Path(*PurePosixPath(output_relative).parts)),
                        "input_sha256": expected["sha256"],
                        "output_sha256": output_sha,
                        "job_id": item.get("job_id") or "",
                        "node_id": item.get("node_id") or "",
                        "attempts": int(item.get("attempts") or 0),
                        "identity_verification": identity,
                    }
                )

            actual_entries = set(member_map)
            if actual_entries != expected_archive_entries:
                extra = sorted(actual_entries - expected_archive_entries)[:5]
                missing = sorted(expected_archive_entries - actual_entries)[:5]
                raise GpuControlError(f"result zip file set mismatch; extra={extra}, missing={missing}")

        if allow_partial:
            if not partial_status:
                raise GpuControlError("partial result publication requires the PARTIAL_SUCCESS status response")
            validate_partial_failed_items(
                partial_status,
                expected_batch,
                {int(item["ordinal"]) for item in published},
            )

        output_resolved = output_root.resolve()
        _assert_child(output_resolved, parent)
        if output_root.exists():
            os.replace(output_root, backup)
        try:
            os.replace(staging, output_root)
        except Exception:
            if backup.exists() and not output_root.exists():
                os.replace(backup, output_root)
            raise
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        return sorted(published, key=lambda item: int(item["ordinal"]))
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def result_artifact(batch_status: dict[str, Any]) -> dict[str, Any]:
    matches = [
        dict(item)
        for item in batch_status.get("artifacts") or []
        if str(item.get("kind") or "") == "result_archive"
    ]
    if len(matches) != 1:
        raise GpuControlError(f"SUCCEEDED batch must have exactly one result_archive artifact, got {len(matches)}")
    artifact = matches[0]
    batch_id = str(batch_status.get("batch_id") or "")
    expected_filename = f"{batch_id}-rgba.zip" if batch_id else ""
    if not str(artifact.get("id") or ""):
        raise GpuControlError("result_archive artifact has no id")
    if expected_filename and str(artifact.get("filename") or "") != expected_filename:
        raise GpuControlError("result_archive filename does not match batch_id")
    if str(artifact.get("content_type") or "").lower() != "application/zip":
        raise GpuControlError("result_archive content_type must be application/zip")
    if int(artifact.get("size_bytes") or 0) <= 0:
        raise GpuControlError("result_archive size_bytes must be positive")
    if len(str(artifact.get("sha256") or "")) != 64 or not str(artifact.get("download_url") or ""):
        raise GpuControlError("result_archive is missing sha256 or download_url")
    return artifact


def validate_partial_failed_items(
    batch_status: dict[str, Any],
    expected_batch: dict[str, Any],
    successful_ordinals: set[int],
) -> list[dict[str, Any]]:
    """Validate the 1.5.10 PARTIAL_SUCCESS partition before publishing it.

    A partial archive proves the successful subset.  The status response must
    independently and exactly identify the complementary failed subset.  This
    prevents a malformed response from silently dropping, duplicating, or
    associating a failed frame with another input.
    """

    if str(batch_status.get("status") or "").upper() != "PARTIAL_SUCCESS":
        raise GpuControlError("partial failed_items are only valid for PARTIAL_SUCCESS")
    expected_frames = list(expected_batch.get("frames") or [])
    expected_by_ordinal = {int(item["ordinal"]): item for item in expected_frames}
    expected_ordinals = set(expected_by_ordinal)
    if not successful_ordinals or not successful_ordinals < expected_ordinals:
        raise GpuControlError("PARTIAL_SUCCESS must contain a non-empty proper successful subset")

    counts = dict(batch_status.get("counts") or {})
    missing_ordinals = expected_ordinals - successful_ordinals
    required_counts = {
        "total": len(expected_frames),
        "succeeded": len(successful_ordinals),
        "failed": len(missing_ordinals),
        "pending": 0,
        "queued": 0,
        "running": 0,
        "cancelled": 0,
    }
    for name, expected_value in required_counts.items():
        try:
            actual_value = int(counts.get(name))
        except (TypeError, ValueError):
            raise GpuControlError(f"PARTIAL_SUCCESS counts.{name} is missing or invalid") from None
        if actual_value != expected_value:
            raise GpuControlError(
                f"PARTIAL_SUCCESS counts.{name} mismatch: expected {expected_value}, got {actual_value}"
            )

    raw_failed = batch_status.get("failed_items")
    if not isinstance(raw_failed, list) or len(raw_failed) != len(missing_ordinals):
        raise GpuControlError("PARTIAL_SUCCESS failed_items count does not match the missing frame count")
    validated: list[dict[str, Any]] = []
    observed_ordinals: set[int] = set()
    for raw_item in raw_failed:
        if not isinstance(raw_item, dict):
            raise GpuControlError("PARTIAL_SUCCESS failed_items must contain objects")
        required = {
            "ordinal",
            "input_relative_path",
            "input_sha256",
            "code",
            "message",
            "node_id",
            "attempts",
            "attempted_node_ids",
        }
        missing_fields = required - set(raw_item)
        if missing_fields:
            raise GpuControlError(
                f"PARTIAL_SUCCESS failed item is missing fields: {sorted(missing_fields)}"
            )
        try:
            ordinal = int(raw_item.get("ordinal"))
        except (TypeError, ValueError):
            raise GpuControlError("PARTIAL_SUCCESS failed item ordinal is invalid") from None
        if ordinal in observed_ordinals:
            raise GpuControlError(f"PARTIAL_SUCCESS contains duplicate failed ordinal: {ordinal}")
        if ordinal not in missing_ordinals:
            raise GpuControlError(f"PARTIAL_SUCCESS failed ordinal is not in the missing subset: {ordinal}")
        expected = expected_by_ordinal[ordinal]
        relative_path = _normalize_relative_path(str(raw_item.get("input_relative_path") or ""))
        if relative_path != expected["relative_path"]:
            raise GpuControlError(f"PARTIAL_SUCCESS failed input mapping mismatch at ordinal {ordinal}")
        input_sha = str(raw_item.get("input_sha256") or "").lower()
        if input_sha != expected["sha256"]:
            raise GpuControlError(f"PARTIAL_SUCCESS failed input sha256 mismatch at ordinal {ordinal}")
        try:
            attempts = int(raw_item.get("attempts"))
        except (TypeError, ValueError):
            raise GpuControlError(f"PARTIAL_SUCCESS attempts is invalid at ordinal {ordinal}") from None
        attempted_node_ids = raw_item.get("attempted_node_ids")
        if not 1 <= attempts <= 3:
            raise GpuControlError(f"PARTIAL_SUCCESS attempts must be 1-3 at ordinal {ordinal}")
        if (
            not isinstance(attempted_node_ids, list)
            or len(attempted_node_ids) != attempts
            or len(set(map(str, attempted_node_ids))) != len(attempted_node_ids)
            or any(not str(node_id).strip() for node_id in attempted_node_ids)
        ):
            raise GpuControlError(f"PARTIAL_SUCCESS attempted_node_ids mismatch at ordinal {ordinal}")
        node_id = str(raw_item.get("node_id") or "").strip()
        if not node_id or node_id != str(attempted_node_ids[-1]):
            raise GpuControlError(f"PARTIAL_SUCCESS node_id does not match the final attempt at ordinal {ordinal}")
        code = str(raw_item.get("code") or "").strip()
        message = str(raw_item.get("message") or "").strip()
        if not code or not message:
            raise GpuControlError(f"PARTIAL_SUCCESS error detail is empty at ordinal {ordinal}")
        observed_ordinals.add(ordinal)
        validated.append(
            {
                **raw_item,
                "ordinal": ordinal,
                "input_relative_path": relative_path,
                "input_sha256": input_sha,
                "node_id": node_id,
                "attempts": attempts,
                "attempted_node_ids": [str(item) for item in attempted_node_ids],
                "code": code,
                "message": message,
            }
        )
    if observed_ordinals != missing_ordinals:
        raise GpuControlError("PARTIAL_SUCCESS successful and failed ordinals do not partition the input batch")
    return sorted(validated, key=lambda item: int(item["ordinal"]))


def compact_remote_state(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "batch_id": payload.get("batch_id") or "",
        "external_batch_id": payload.get("external_batch_id") or "",
        "status": str(payload.get("status") or "").upper(),
        "progress": payload.get("progress") or 0,
        "counts": dict(payload.get("counts") or {}),
        "node_distribution": dict(payload.get("node_distribution") or {}),
        "error": payload.get("error"),
        "artifacts": list(payload.get("artifacts") or []),
        "failed_items": list(payload.get("failed_items") or []),
        "workflow_key": payload.get("workflow_key") or "",
        "workflow_version": payload.get("workflow_version") or "",
        "pipeline_commit": payload.get("pipeline_commit") or payload.get("imageclip_commit") or "",
        "pipeline_sha256": payload.get("pipeline_sha256") or "",
        "output_node": payload.get("output_node") or "",
        "created_at": payload.get("created_at") or "",
        "validated_at": payload.get("validated_at") or "",
        "queued_at": payload.get("queued_at") or "",
        "started_at": payload.get("started_at") or "",
        "last_progress_at": payload.get("last_progress_at") or "",
        "execution_finished_at": payload.get("execution_finished_at") or "",
        "assembling_at": payload.get("assembling_at") or payload.get("assembling_started_at") or "",
        "assembling_started_at": payload.get("assembling_at") or payload.get("assembling_started_at") or "",
        "artifact_ready_at": payload.get("artifact_ready_at") or "",
        "finished_at": payload.get("finished_at") or "",
        "updated_at": payload.get("updated_at") or payload.get("finished_at") or "",
        "performance": dict(payload.get("performance") or {}),
        "response_meta": dict(payload.get("_response_meta") or {}),
    }


def merge_remote_state(current: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Merge one server observation while keeping user-visible progress monotonic."""

    merged = dict(current or {})
    previous_progress = float(merged.get("progress") or 0)
    observation = compact_remote_state(payload)
    # Create/cancel responses are intentionally sparse. Do not erase facts
    # learned from a richer parent-status response merely because a later
    # action response omitted those fields.
    optional_sources = {
        "counts": ("counts",),
        "node_distribution": ("node_distribution",),
        "error": ("error",),
        "artifacts": ("artifacts",),
        "failed_items": ("failed_items",),
        "workflow_key": ("workflow_key",),
        "workflow_version": ("workflow_version",),
        "pipeline_commit": ("pipeline_commit", "imageclip_commit"),
        "pipeline_sha256": ("pipeline_sha256",),
        "output_node": ("output_node",),
        "created_at": ("created_at",),
        "validated_at": ("validated_at",),
        "queued_at": ("queued_at",),
        "started_at": ("started_at",),
        "last_progress_at": ("last_progress_at",),
        "execution_finished_at": ("execution_finished_at",),
        "assembling_at": ("assembling_at", "assembling_started_at"),
        "assembling_started_at": ("assembling_at", "assembling_started_at"),
        "artifact_ready_at": ("artifact_ready_at",),
        "finished_at": ("finished_at",),
        "updated_at": ("updated_at", "finished_at"),
        "performance": ("performance",),
        "response_meta": ("_response_meta",),
    }
    for field, source_keys in optional_sources.items():
        if not any(key in payload for key in source_keys):
            observation.pop(field, None)
    observed_progress = float(observation.get("progress") or 0)
    observation["progress"] = max(previous_progress, min(100.0, max(0.0, observed_progress)))
    merged.update(observation)
    return merged


def validate_workflow_identity(payload: dict[str, Any]) -> dict[str, Any]:
    """Fail closed on an observed workflow mismatch without requiring undeployed fields.

    GPU Control currently returns key/version more consistently than commit/SHA. Missing
    identity remains visible as partial evidence, while any value the server does return
    must match the jointly approved 2026-07-30 baseline.
    """

    observed = {
        "workflow_key": str(payload.get("workflow_key") or ""),
        "workflow_version": str(payload.get("workflow_version") or ""),
        "pipeline_commit": str(payload.get("pipeline_commit") or payload.get("imageclip_commit") or ""),
        "pipeline_sha256": str(payload.get("pipeline_sha256") or "").lower(),
        "output_node": str(payload.get("output_node") or ""),
    }
    approved = {
        "workflow_key": APPROVED_WORKFLOW_KEY,
        "workflow_version": APPROVED_WORKFLOW_VERSION,
        "pipeline_commit": APPROVED_PIPELINE_COMMIT,
        "pipeline_sha256": APPROVED_PIPELINE_SHA256,
        "output_node": APPROVED_OUTPUT_NODE,
    }
    mismatches = {
        key: {"expected": approved[key], "actual": actual}
        for key, actual in observed.items()
        if actual and actual != approved[key]
    }
    if mismatches:
        detail = ", ".join(f"{key}={item['actual']}" for key, item in mismatches.items())
        raise GpuControlError(f"GPU Control workflow identity mismatch: {detail}")
    present = sorted(key for key, value in observed.items() if value)
    missing = sorted(key for key, value in observed.items() if not value)
    return {
        "status": "VERIFIED" if not missing else ("PARTIAL_MATCH" if present else "UNVERIFIED_MISSING"),
        "approved": approved,
        "observed": observed,
        "present_fields": present,
        "missing_fields": missing,
    }


def _validated_archive_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    result: dict[str, zipfile.ZipInfo] = {}
    collision_keys: set[str] = set()
    for member in archive.infolist():
        if member.is_dir():
            raise GpuControlError(f"result zip contains an explicit directory entry: {member.filename}")
        unix_mode = (member.external_attr >> 16) & 0xF000
        if unix_mode == 0xA000:
            raise GpuControlError(f"result zip contains a symbolic link: {member.filename}")
        name = _normalize_relative_path(member.filename)
        key = _normalized_collision_key(name)
        if key in collision_keys:
            raise GpuControlError(f"duplicate normalized result path: {name}")
        collision_keys.add(key)
        result[name] = member
    return result


def _verify_input_archive(archive_path: Path, frames: list[dict[str, Any]]) -> None:
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            damaged = archive.testzip()
            if damaged:
                raise GpuControlError(f"persisted input zip integrity check failed: {damaged}")
            members = _validated_archive_members(archive)
            expected_names = {str(item["relative_path"]) for item in frames}
            if set(members) != expected_names:
                raise GpuControlError("persisted input zip file set no longer matches its manifest")
            for item in frames:
                if members[str(item["relative_path"])].compress_type != zipfile.ZIP_STORED:
                    raise GpuControlError(f"persisted input zip entry is not ZIP_STORED: {item['relative_path']}")
                payload = archive.read(members[str(item["relative_path"])])
                if len(payload) != int(item["size_bytes"]) or hashlib.sha256(payload).hexdigest() != item["sha256"]:
                    raise GpuControlError(f"persisted input zip content mismatch: {item['relative_path']}")
    except zipfile.BadZipFile as exc:
        raise GpuControlError("persisted GPU Control input archive is invalid") from exc


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _validate_v2_input_image(path: Path) -> None:
    if path.suffix.lower() not in V2_ALLOWED_INPUT_SUFFIXES:
        raise GpuControlError(f"GPU Control V2 only accepts JPEG, PNG, or WebP: {path.name}")
    size = path.stat().st_size
    if not 1 <= size <= V2_MAX_FRAME_BYTES:
        raise GpuControlError(f"GPU Control V2 frame size is outside 1-64 MiB: {path.name}")
    try:
        with Image.open(path) as image:
            width, height = image.size
            actual_format = str(image.format or "").upper()
            image.verify()
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        raise GpuControlError(f"GPU Control V2 input image is not decodable: {path.name}") from exc
    if width <= 0 or height <= 0 or width * height > V2_MAX_IMAGE_PIXELS:
        raise GpuControlError(f"GPU Control V2 input image exceeds 40,000,000 pixels: {path.name}")
    if actual_format not in {"JPEG", "PNG", "WEBP"}:
        raise GpuControlError(f"GPU Control V2 input image format is not JPEG, PNG, or WebP: {path.name}")


def _input_relative_path(root: Path, path: Path, preserve_structure: bool) -> str:
    relative = path.relative_to(root) if preserve_structure else Path(path.name)
    return _normalize_relative_path(relative.as_posix())


def _normalize_relative_path(value: str) -> str:
    normalized = unicodedata.normalize("NFC", str(value or ""))
    if normalized != str(value or "") or len(normalized) > 2048:
        raise GpuControlError(f"relative path must be NFC and at most 2048 characters: {value!r}")
    if "\\" in normalized or "\x00" in normalized:
        raise GpuControlError(f"invalid relative path: {value!r}")
    if any(part == "" for part in normalized.split("/")):
        raise GpuControlError(f"relative path contains an empty segment: {value!r}")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise GpuControlError(f"invalid relative path: {value!r}")
    if ":" in path.parts[0]:
        raise GpuControlError(f"invalid relative path: {value!r}")
    return str(path)


def _normalized_collision_key(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _assert_child(path: Path, parent: Path) -> None:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError as exc:
        raise GpuControlError(f"path escapes task root: {path}") from exc


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _response_json(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise GpuControlError(f"GPU Control returned non-JSON response: HTTP {response.status_code}") from exc
    if not isinstance(payload, dict):
        raise GpuControlError("GPU Control returned a non-object JSON response")
    return payload


def _response_meta(response: requests.Response) -> dict[str, Any]:
    return {
        "http_status": int(response.status_code),
        "request_id": response.headers.get("X-Request-ID") or "",
    }


def _response_error_text(response: requests.Response, label: str) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = response.text[:1000]
    return f"GPU Control {label} failed: HTTP {response.status_code} {payload}"


def _raise_response(response: requests.Response, label: str) -> None:
    raise GpuControlError(_response_error_text(response, label))
