from __future__ import annotations

import copy
import json
import logging
import time
from pathlib import Path
from typing import Any

import requests
from requests import HTTPError

from assetclaw_matting.config import settings
from assetclaw_matting.comfyui.workflow_patch import patch_load_image
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

    def upload_image(self, image_path: str | Path) -> str:
        """Upload an image to ComfyUI's input folder. Returns the filename."""
        path = Path(image_path)
        with path.open("rb") as fh:
            resp = requests.post(
                f"{self._base}/upload/image",
                files={"image": (path.name, fh, "image/png")},
                data={"overwrite": "true"},
                timeout=60,
            )
        resp.raise_for_status()
        data = resp.json()
        filename = data.get("name") or data.get("filename") or path.name
        log.debug("Uploaded %s → ComfyUI filename=%s", path.name, filename)
        return filename

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

        # 4. Submit
        prompt_id = self.submit_prompt(workflow)
        log.info("ComfyUI prompt submitted: %s", prompt_id)

        # 5. Wait
        history = self.wait_for_completion(prompt_id)

        # 6. Save history for debugging
        if task_id:
            debug_path = settings.debug_dir / f"history_{task_id}.json"
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            debug_path.write_text(json.dumps(history, indent=2), encoding="utf-8")

        # 7. Resolve + download output
        try:
            output_info = resolve_first_output(history, prompt_id)
        except ValueError:
            if task_id:
                from assetclaw_matting.services.file_store import save_debug_history
                save_debug_history(task_id, json.dumps(history, indent=2))
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
