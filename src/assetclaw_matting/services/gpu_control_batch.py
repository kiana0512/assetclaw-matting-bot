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


TERMINAL_BATCH_STATUSES = {"SUCCEEDED", "FAILED", "CANCELLED"}
RETRYABLE_HTTP_STATUSES = {429, 502, 503, 504}
V2_ALLOWED_INPUT_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
V2_MAX_FRAMES = 5000
V2_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
V2_MAX_FRAME_BYTES = 64 * 1024 * 1024
V2_MAX_IMAGE_PIXELS = 40_000_000
V2_MAX_ARCHIVE_BYTES = 100 * 1024 * 1024 * 1024


class GpuControlError(RuntimeError):
    pass


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
        payload = _response_json(response)
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
        if not raw_url or len(expected_sha) != 64:
            raise GpuControlError("result artifact is missing download_url or sha256")
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")
        if partial.exists():
            partial.unlink()
        digest = hashlib.sha256()
        response = self.session.get(
            self._url(raw_url),
            headers=self._headers(request_id=request_id),
            timeout=(self.connect_timeout, self.download_timeout),
            verify=self.verify,
            stream=True,
        )
        if response.status_code != 200:
            _raise_response(response, "download artifact")
        try:
            response_header_sha = str(response.headers.get("X-Artifact-SHA256") or "").lower()
            if response_header_sha != expected_sha:
                raise GpuControlError(
                    f"artifact response header sha256 mismatch: expected {expected_sha}, got {response_header_sha or 'missing'}"
                )
            with partial.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    digest.update(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            actual_sha = digest.hexdigest()
            if actual_sha != expected_sha:
                raise GpuControlError(f"result archive sha256 mismatch: expected {expected_sha}, got {actual_sha}")
            os.replace(partial, destination)
            return {
                "path": str(destination),
                "sha256": actual_sha,
                "header_sha256": response_header_sha,
                "size_bytes": destination.stat().st_size,
                "request_id": response.headers.get("X-Request-ID") or "",
            }
        finally:
            response.close()
            if partial.exists():
                partial.unlink()

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
            if set(result_manifest) != {"schema_version", "batch_id", "external_batch_id", "total", "items"}:
                raise GpuControlError("result manifest fields do not match GPU Control V2")
            result_items = list(result_manifest.get("items") or [])
            if int(result_manifest.get("total") or len(result_items)) != len(expected_frames):
                raise GpuControlError("result manifest total does not match the submitted frame count")
            if len(result_items) != len(expected_frames):
                raise GpuControlError("result manifest item count does not match the submitted frame count")

            expected_archive_entries: set[str] = {"manifest.json"}
            if [int(item.get("ordinal", -1)) for item in result_items] != list(range(len(expected_frames))):
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
    for item in batch_status.get("artifacts") or []:
        if str(item.get("kind") or "") == "result_archive":
            return dict(item)
    raise GpuControlError("SUCCEEDED batch has no result_archive artifact")


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
        "updated_at": payload.get("updated_at") or payload.get("finished_at") or "",
        "response_meta": dict(payload.get("_response_meta") or {}),
    }


def _validated_archive_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    result: dict[str, zipfile.ZipInfo] = {}
    collision_keys: set[str] = set()
    for member in archive.infolist():
        if member.is_dir():
            continue
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
