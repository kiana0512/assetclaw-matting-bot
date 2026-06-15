from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any


def local_ocr_image(image_path: Path, timeout_seconds: int = 30) -> dict[str, Any]:
    winrt = _ocr_with_winrt(image_path, timeout_seconds)
    if winrt.get("available"):
        return winrt
    tesseract = _ocr_with_tesseract(image_path, timeout_seconds)
    if tesseract.get("available"):
        return tesseract
    return {
        "available": False,
        "text": "",
        "engine": "none",
        "errors": [*(winrt.get("errors") or []), *(tesseract.get("errors") or [])],
    }


def _ocr_with_winrt(image_path: Path, timeout_seconds: int) -> dict[str, Any]:
    script = Path("scripts/local_ocr_winrt.ps1")
    pwsh = shutil.which("powershell") or shutil.which("pwsh")
    if not pwsh:
        return {"available": False, "engine": "windows_ocr", "errors": ["pwsh/powershell not found"]}
    if not script.exists():
        return {"available": False, "engine": "windows_ocr", "errors": [f"{script} not found"]}
    try:
        completed = subprocess.run(
            [
                pwsh,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-Path",
                str(image_path),
            ],
            cwd=str(Path.cwd()),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
        )
    except Exception as exc:
        return {"available": False, "engine": "windows_ocr", "errors": [str(exc)]}
    text = (completed.stdout or "").strip()
    if completed.returncode == 0:
        return {"available": True, "engine": "windows_ocr", "text": text}
    error = (completed.stderr or completed.stdout or "").strip()
    return {"available": False, "engine": "windows_ocr", "errors": [error[:500] or f"exit {completed.returncode}"]}


def _ocr_with_tesseract(image_path: Path, timeout_seconds: int) -> dict[str, Any]:
    exe = shutil.which("tesseract")
    if not exe:
        return {"available": False, "engine": "tesseract", "errors": ["tesseract.exe not found in PATH"]}
    try:
        completed = subprocess.run(
            [exe, str(image_path), "stdout", "-l", "chi_sim+eng"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
        )
    except Exception as exc:
        return {"available": False, "engine": "tesseract", "errors": [str(exc)]}
    text = (completed.stdout or "").strip()
    if completed.returncode == 0:
        return {"available": True, "engine": "tesseract", "text": text}
    error = (completed.stderr or completed.stdout or "").strip()
    return {"available": False, "engine": "tesseract", "errors": [error[:500] or f"exit {completed.returncode}"]}
