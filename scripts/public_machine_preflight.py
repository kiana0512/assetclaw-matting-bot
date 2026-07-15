from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from assetclaw_matting.config import settings
from assetclaw_matting.services.cherry_html_runner import validate_cherry_html_runtime
from assetclaw_matting.skills.matting_pipeline_skills import verify as verify_matting_pipeline


def _check(name: str, ok: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def run_checks() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    versions: dict[str, str] = {}
    for module_name in ("cv2", "numpy", "torch", "flask", "yaml", "psutil", "tqdm"):
        try:
            module = __import__(module_name)
            versions[module_name] = str(getattr(module, "__version__", "installed"))
        except Exception as exc:
            versions[module_name] = f"missing: {exc}"
    checks.append(_check("python_dependencies", all(not value.startswith("missing:") for value in versions.values()), versions))

    expected_paths = {
        "project": Path(settings.assetclaw_root),
        "animation": Path(settings.animation_root),
        "imageclip": Path(settings.matting_pipeline_repo_dir),
        "comfyui": Path(settings.comfyui_dir),
        "comfyui_python": Path(settings.comfyui_python_dir),
    }
    for name, path in expected_paths.items():
        checks.append(_check(name, path.exists(), str(path)))

    pipeline = verify_matting_pipeline()
    checks.append(
        _check(
            "matting_pipeline_assets",
            bool(pipeline.get("ok")),
            {
                "workflow": pipeline.get("workflow_path"),
                "cherry_html": pipeline.get("cherry_html_path"),
                "assets": pipeline.get("assets", []),
                "errors": pipeline.get("errors", []),
            },
        )
    )

    try:
        cherry = validate_cherry_html_runtime(
            Path(settings.cherry_postprocess_html_path),
            Path(settings.cherry_browser_path) if settings.cherry_browser_path else None,
        )
        checks.append(_check("cherry_html_runtime", True, cherry))
    except Exception as exc:
        checks.append(_check("cherry_html_runtime", False, str(exc)))

    checks.append(_check("allowed_roots", bool(settings.allowed_roots_list), settings.allowed_roots_list))
    return {"ok": all(item["ok"] for item in checks), "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only public-machine readiness check.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()
    result = run_checks()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("AssetClaw public-machine preflight")
        for item in result["checks"]:
            print(f"[{'PASS' if item['ok'] else 'FAIL'}] {item['name']}: {item['detail']}")
        print("PASS" if result["ok"] else "FAILED")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
