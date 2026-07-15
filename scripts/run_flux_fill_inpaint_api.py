from __future__ import annotations

import argparse
import copy
import json
import time
from pathlib import Path
from typing import Any

import requests


WORKFLOW_PATH = Path("workflows/flux_fill_inpaint_api.json")
LOAD_IMAGE_NODE = "111"
SAMPLER_NODE = "57"
SAVE_IMAGE_NODE = "9"


def upload_image(base_url: str, image_path: Path) -> str:
    with image_path.open("rb") as fh:
        response = requests.post(
            f"{base_url}/upload/image",
            files={"image": (image_path.name, fh, "image/png")},
            data={"overwrite": "true"},
            timeout=60,
        )
    response.raise_for_status()
    data = response.json()
    return str(data.get("name") or data.get("filename") or image_path.name)


def submit_prompt(base_url: str, prompt: dict[str, Any]) -> str:
    response = requests.post(f"{base_url}/prompt", json={"prompt": prompt}, timeout=30)
    if response.status_code >= 400:
        raise RuntimeError(f"ComfyUI /prompt failed: {response.status_code} {response.text[:2000]}")
    data = response.json()
    prompt_id = data.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(f"ComfyUI /prompt returned no prompt_id: {data}")
    return str(prompt_id)


def wait_for_history(base_url: str, prompt_id: str, timeout_seconds: int) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        response = requests.get(f"{base_url}/history/{prompt_id}", timeout=15)
        response.raise_for_status()
        history = response.json()
        if prompt_id in history:
            status = history[prompt_id].get("status", {})
            if status.get("completed") or status.get("status_str") == "success":
                return history
            if status.get("status_str") == "error":
                raise RuntimeError(f"ComfyUI prompt error: {status.get('messages')}")
        time.sleep(1)
    raise TimeoutError(f"ComfyUI prompt {prompt_id} did not finish in {timeout_seconds}s")


def download_first_output(base_url: str, history: dict[str, Any], prompt_id: str, output_path: Path) -> None:
    outputs = history[prompt_id].get("outputs", {})
    images = outputs.get(SAVE_IMAGE_NODE, {}).get("images") or []
    if not images:
        raise RuntimeError(f"No images found on SaveImage node {SAVE_IMAGE_NODE}")
    image = images[0]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(
        f"{base_url}/view",
        params={
            "filename": image.get("filename", ""),
            "subfolder": image.get("subfolder", ""),
            "type": image.get("type", "output"),
        },
        timeout=60,
    )
    response.raise_for_status()
    output_path.write_bytes(response.content)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the FLUX fill inpaint workflow through the ComfyUI API.")
    parser.add_argument("image", type=Path, help="Input image. For this workflow, the mask should be embedded in the image alpha/mask data.")
    parser.add_argument("output", type=Path, help="Where to save the final image.")
    parser.add_argument("--url", default="http://127.0.0.1:8188", help="ComfyUI base URL.")
    parser.add_argument("--workflow", type=Path, default=WORKFLOW_PATH, help="API workflow JSON path.")
    parser.add_argument("--seed", type=int, default=None, help="Optional KSampler seed.")
    parser.add_argument("--timeout", type=int, default=900, help="Prompt timeout in seconds.")
    args = parser.parse_args()

    base_url = args.url.rstrip("/")
    workflow = json.loads(args.workflow.read_text(encoding="utf-8"))
    prompt = copy.deepcopy(workflow)

    uploaded_name = upload_image(base_url, args.image)
    prompt[LOAD_IMAGE_NODE]["inputs"]["image"] = uploaded_name
    if args.seed is not None:
        prompt[SAMPLER_NODE]["inputs"]["seed"] = args.seed

    prompt_id = submit_prompt(base_url, prompt)
    print(f"prompt_id={prompt_id}")
    history = wait_for_history(base_url, prompt_id, args.timeout)
    download_first_output(base_url, history, prompt_id, args.output)
    print(f"saved={args.output}")


if __name__ == "__main__":
    main()
