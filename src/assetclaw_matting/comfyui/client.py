from __future__ import annotations

import copy
import json
import logging
import re
import shutil
import time
from pathlib import Path
from typing import Any

import requests
from requests import HTTPError

from assetclaw_matting.config import settings
from assetclaw_matting.comfyui.workflow_patch import find_primary_save_image_node_id, patch_load_image, prepare_api_prompt_for_run
from assetclaw_matting.comfyui.output_resolver import resolve_first_output

log = logging.getLogger(__name__)


class ComfyUIClient:
    """HTTP client for the ComfyUI API."""

    @property
    def _base(self) -> str:
        return settings.comfyui_url.rstrip("/")

    # ── Health ────────────────────────────────────────────────────────────────

    def check_health(self) -> dict[str, Any]:
        resp = requests.get(f"{self._base}/system_stats", timeout=10)
        resp.raise_for_status()
        return resp.json()

    def get_queue(self) -> dict[str, Any]:
        resp = requests.get(f"{self._base}/queue", timeout=10)
        resp.raise_for_status()
        return resp.json()

    def interrupt(self) -> None:
        resp = requests.post(f"{self._base}/interrupt", timeout=10)
        resp.raise_for_status()

    def delete_from_queue(self, prompt_ids: list[str]) -> None:
        if not prompt_ids:
            return
        resp = requests.post(f"{self._base}/queue", json={"delete": prompt_ids}, timeout=10)
        resp.raise_for_status()

    def get_object_info(self) -> dict[str, Any]:
        resp = requests.get(f"{self._base}/object_info", timeout=20)
        resp.raise_for_status()
        return resp.json()

    # ── Upload ────────────────────────────────────────────────────────────────

    def upload_image(self, image_path: str | Path, remote_name: str | None = None) -> str:
        """Upload an image to ComfyUI's input folder. Returns the filename."""
        path = Path(image_path)
        upload_name = _safe_upload_name(remote_name or path.name, suffix=path.suffix or ".png")
        with path.open("rb") as fh:
            resp = requests.post(
                f"{self._base}/upload/image",
                files={"image": (upload_name, fh, "image/png")},
                data={"overwrite": "true"},
                timeout=60,
            )
        resp.raise_for_status()
        data = resp.json()
        filename = data.get("name") or data.get("filename") or upload_name
        log.debug("Uploaded %s → ComfyUI filename=%s", path.name, filename)
        return filename

    def verify_uploaded_image(self, source_path: str | Path, uploaded_filename: str) -> dict[str, str]:
        from assetclaw_matting.skills.sequence_integrity import sha256_file

        source = Path(source_path)
        uploaded = self.resolve_local_output_path(uploaded_filename, "", "input")
        if not uploaded or not uploaded.is_file():
            raise RuntimeError(f"ComfyUI uploaded input cannot be verified locally: {uploaded_filename}")
        source_sha256 = sha256_file(source)
        uploaded_sha256 = sha256_file(uploaded)
        if source_sha256 != uploaded_sha256:
            raise RuntimeError(
                f"ComfyUI input hash mismatch: source={source.name} uploaded={uploaded_filename}"
            )
        return {
            "uploaded_name": uploaded_filename,
            "source_sha256": source_sha256,
            "uploaded_sha256": uploaded_sha256,
        }

    def cleanup_uploaded_image(self, uploaded_filename: str) -> None:
        if not str(uploaded_filename).startswith("assetclaw_"):
            return
        uploaded = self.resolve_local_output_path(uploaded_filename, "", "input")
        if uploaded and uploaded.is_file():
            uploaded.unlink(missing_ok=True)

    # ── Prompt ────────────────────────────────────────────────────────────────

    def submit_prompt(self, workflow: dict[str, Any], client_id: str | None = None) -> str:
        payload: dict[str, Any] = {"prompt": workflow}
        if client_id:
            payload["client_id"] = client_id
        resp = requests.post(
            f"{self._base}/prompt",
            json=payload,
            timeout=30,
        )
        if resp.status_code >= 400:
            detail = resp.text[:2000] if resp.text else resp.reason
            raise HTTPError(f"{resp.status_code} ComfyUI /prompt failed: {detail}", response=resp)
        data = resp.json()
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise RuntimeError(f"ComfyUI /prompt returned no prompt_id: {data}")
        log.debug("Submitted prompt → prompt_id=%s", prompt_id)
        return prompt_id

    # ── History ───────────────────────────────────────────────────────────────

    def get_history(self, prompt_id: str) -> dict[str, Any]:
        resp = requests.get(f"{self._base}/history/{prompt_id}", timeout=15)
        resp.raise_for_status()
        return resp.json()

    def get_all_history(self, max_items: int = 20) -> dict[str, Any]:
        resp = requests.get(f"{self._base}/history", params={"max_items": max_items}, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def wait_for_completion(self, prompt_id: str) -> dict[str, Any]:
        deadline = time.time() + settings.comfyui_timeout_seconds
        interval = settings.comfyui_poll_interval_seconds
        while time.time() < deadline:
            history = self.get_history(prompt_id)
            if prompt_id in history:
                entry = history[prompt_id]
                status = entry.get("status", {})
                if status.get("completed") or status.get("status_str") == "success":
                    log.info("ComfyUI prompt %s completed", prompt_id)
                    return history
                if status.get("status_str") == "error":
                    raise RuntimeError(
                        f"ComfyUI prompt {prompt_id} error: {status.get('messages')}"
                    )
            time.sleep(interval)
        raise TimeoutError(
            f"ComfyUI prompt {prompt_id} did not complete in {settings.comfyui_timeout_seconds}s"
        )

    # ── Download ──────────────────────────────────────────────────────────────

    def download_output(
        self,
        filename: str,
        subfolder: str,
        output_type: str,
        save_path: str | Path,
    ) -> None:
        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        source_path = self.resolve_local_output_path(filename, subfolder, output_type)
        if source_path and source_path.exists():
            shutil.copy2(source_path, save_path)
            log.debug("Copied ComfyUI local output %s -> %s", source_path, save_path)
            return

        resp = requests.get(
            f"{self._base}/view",
            params={"filename": filename, "subfolder": subfolder, "type": output_type},
            stream=True,
            timeout=60,
        )
        resp.raise_for_status()
        with save_path.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=65536):
                fh.write(chunk)
        log.debug("Downloaded ComfyUI output → %s", save_path)

    def resolve_local_output_path(
        self,
        filename: str,
        subfolder: str = "",
        output_type: str = "output",
    ) -> Path | None:
        base_name = {
            "output": "output",
            "input": "input",
            "temp": "temp",
        }.get(str(output_type or "output").lower())
        if not base_name:
            return None
        try:
            parts = [part for part in Path(subfolder or "").parts if part not in {"", "."}]
            relative = Path(*parts, filename) if parts else Path(filename)
            if relative.is_absolute() or any(part == ".." for part in relative.parts):
                return None
            return settings.comfyui_dir / base_name / relative
        except Exception:
            return None

    # ── High-level run ────────────────────────────────────────────────────────

    def run_workflow(
        self,
        input_image_path: str | Path,
        output_image_path: str | Path,
        task_id: str = "",
    ) -> Path:
        """Full pipeline: upload → patch → submit → wait → download → return path.

        If COMFYUI_FAKE_MODE=true, uses Pillow mock instead of calling ComfyUI.
        """
        if settings.comfyui_fake_mode:
            return self._fake_run(input_image_path, output_image_path)
        return self._real_run(input_image_path, output_image_path, task_id)

    def _fake_run(
        self,
        input_image_path: str | Path,
        output_image_path: str | Path,
    ) -> Path:
        """Mock: convert to RGBA and save as output (no GPU required)."""
        from PIL import Image

        out_path = Path(output_image_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with Image.open(str(input_image_path)) as img:
                img.convert("RGBA").save(str(out_path), "PNG")
            log.info("FAKE MODE: saved mock output → %s", out_path)
        except Exception:
            import shutil
            shutil.copy2(str(input_image_path), str(out_path))
            log.warning("FAKE MODE: Pillow failed, raw copy → %s", out_path)
        return out_path

    def _real_run(
        self,
        input_image_path: str | Path,
        output_image_path: str | Path,
        task_id: str = "",
    ) -> Path:
        out_path = Path(output_image_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # 1. Health check
        self.check_health()

        # 2. Upload input
        uploaded_filename = self.upload_image(input_image_path)

        # 3. Load + patch workflow
        wf_path = settings.comfyui_workflow_path
        if not wf_path.exists():
            raise FileNotFoundError(
                f"Workflow not found: {wf_path}\n"
                "Export from ComfyUI (Dev Mode → Save API Format) → workflows/matting_api.json"
            )
        with wf_path.open("r", encoding="utf-8") as fh:
            workflow: dict[str, Any] = json.load(fh)
        workflow = patch_load_image(copy.deepcopy(workflow), uploaded_filename)
        final_save_image_node_id = find_primary_save_image_node_id(workflow)
        if not final_save_image_node_id:
            raise ValueError("当前 ComfyUI workflow 没有找到 SaveImage/保存图像 节点，拒绝下载输出。")

        # 4. Submit
        prompt_id = self.submit_prompt(prepare_api_prompt_for_run(workflow))
        log.info("ComfyUI prompt submitted: %s", prompt_id)

        # 5. Wait
        history = self.wait_for_completion(prompt_id)

        # 6. Save history for debugging
        if task_id:
            debug_path = settings.storage_dir / "debug" / f"history_{task_id}.json"
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            debug_path.write_text(json.dumps(history, indent=2), encoding="utf-8")

        # 7. Resolve + download output
        try:
            output_info = resolve_first_output(history, prompt_id, final_save_image_node_id=final_save_image_node_id)
        except ValueError:
            raise

        self.download_output(
            filename=output_info["filename"],
            subfolder=output_info["subfolder"],
            output_type=output_info["type"],
            save_path=out_path,
        )
        log.info("ComfyUI output downloaded: %s", out_path)
        return out_path


comfyui_client = ComfyUIClient()


def _safe_upload_name(value: str, *, suffix: str = ".png") -> str:
    raw = Path(str(value or "image.png").replace("\\", "/")).name
    stem = re.sub(r"[^0-9A-Za-z._-]+", "_", Path(raw).stem).strip("._-") or "image"
    extension = Path(raw).suffix.lower() or suffix.lower() or ".png"
    if extension not in {".png", ".jpg", ".jpeg", ".webp"}:
        extension = ".png"
    return f"{stem[:180]}{extension}"
