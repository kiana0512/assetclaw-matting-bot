from __future__ import annotations


import csv
import subprocess
from io import StringIO
from pathlib import Path
from typing import Any


def comfyui_status() -> dict[str, Any]:
    from assetclaw_matting.config import settings
    from assetclaw_matting.comfyui.client import comfyui_client

    result: dict[str, Any] = {
        "ok": True,
        "fake_mode": settings.comfyui_fake_mode,
        "url": settings.comfyui_url,
        "workflow_path": str(settings.comfyui_workflow_path),
        "workflow_exists": settings.comfyui_workflow_path.exists(),
        "aki_root": str(settings.comfyui_aki_root),
        "aki_root_exists": settings.comfyui_aki_root.exists(),
        "comfyui_dir": str(settings.comfyui_dir),
        "comfyui_dir_exists": settings.comfyui_dir.exists(),
        "comfyui_python_dir": str(settings.comfyui_python_dir),
        "comfyui_python_dir_exists": settings.comfyui_python_dir.exists(),
        "comfyui_version": _read_comfyui_version(settings.comfyui_dir),
    }
    if settings.comfyui_fake_mode:
        result.update({"reachable": False, "mode_note": "fake mode enabled; GPU/ComfyUI is not used"})
        return result
    try:
        stats = comfyui_client.check_health()
        result.update({"reachable": True, "system_stats": stats})
    except Exception as exc:
        result.update({"reachable": False, "error": str(exc)})
    try:
        result["processes"] = process_status(["ComfyUI", "python", "绘世"])
    except Exception as exc:
        result["processes"] = {"ok": False, "error": str(exc)}
    return result


def gpu_status() -> dict[str, Any]:
    query = [
        "nvidia-smi",
        "--query-gpu=index,name,driver_version,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu,power.draw",
        "--format=csv,noheader,nounits",
    ]
    try:
        proc = subprocess.run(query, capture_output=True, text=True, timeout=10, check=False)
    except FileNotFoundError:
        return {"ok": True, "available": False, "error": "nvidia-smi not found"}
    except Exception as exc:
        return {"ok": True, "available": False, "error": str(exc)}
    if proc.returncode != 0:
        return {"ok": True, "available": False, "error": (proc.stderr or proc.stdout).strip()}

    rows = []
    reader = csv.reader(StringIO(proc.stdout))
    for row in reader:
        if len(row) < 9:
            continue
        rows.append({
            "index": row[0].strip(),
            "name": row[1].strip(),
            "driver_version": row[2].strip(),
            "memory_total_mb": _to_number(row[3]),
            "memory_used_mb": _to_number(row[4]),
            "memory_free_mb": _to_number(row[5]),
            "utilization_gpu_percent": _to_number(row[6]),
            "temperature_c": _to_number(row[7]),
            "power_draw_w": _to_number(row[8]),
        })
    return {"ok": True, "available": bool(rows), "gpus": rows}


def process_status(names: list[str] | None = None) -> dict[str, Any]:
    import psutil

    targets = [name.lower() for name in (names or ["python", "ComfyUI", "nvidia-smi"])]
    items = []
    for proc in psutil.process_iter(["pid", "name", "status", "cpu_percent", "memory_info", "cmdline"]):
        try:
            info = proc.info
            name = info.get("name") or ""
            cmdline = " ".join(info.get("cmdline") or [])
            haystack = f"{name} {cmdline}".lower()
            if not any(target.lower() in haystack for target in targets):
                continue
            mem = info.get("memory_info")
            items.append({
                "pid": info.get("pid"),
                "name": name,
                "status": info.get("status"),
                "memory_mb": round((mem.rss if mem else 0) / (1024 * 1024), 1),
                "cmdline": cmdline[:300],
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return {"ok": True, "count": len(items), "items": items[:30]}


def _to_number(value: str) -> float | int | str:
    value = value.strip()
    try:
        number = float(value)
    except ValueError:
        return value
    return int(number) if number.is_integer() else number


def _read_comfyui_version(comfyui_dir: Path) -> str:
    version_file = comfyui_dir / "comfyui_version.py"
    if not version_file.exists():
        return ""
    try:
        text = version_file.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    for line in text.splitlines():
        if "__version__" not in line:
            continue
        parts = line.split("=", 1)
        if len(parts) == 2:
            return parts[1].strip().strip('"').strip("'")
    return ""
