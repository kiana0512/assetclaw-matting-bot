from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ASSET_KINDS = ("scene", "emoji")
PROCESS_VARIANTS = ("default",)
SKIP_PROGRESS = {"已完成", "不处理"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".m4v"}

ASSET_FIELD_CANDIDATES = ("父记录", "父記錄", "类型", "動畫類型", "动画类型", "分类", "assetKind", "category")
PROCESS_OPTION_FIELDS = ("处理选项", "處理選項", "processOption", "process_option")
TYPE_FIELD_CANDIDATES = ("类型", "動畫類型", "动画类型", "分类", "assetKind", "category")


def safe_name(value: Any) -> str:
    text = str(value or "").strip()
    return re.sub(r'[\\/:*?"<>|\s]+', "_", text).strip("_") or "unnamed"


def field_texts(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        return [text] if text else []
    if isinstance(raw, dict):
        values = []
        for key in ("text", "name", "value"):
            if raw.get(key) not in (None, ""):
                values.extend(field_texts(raw.get(key)))
        if raw.get("text_arr"):
            values.extend(field_texts(raw.get("text_arr")))
        return values
    if isinstance(raw, list):
        values: list[str] = []
        for item in raw:
            values.extend(field_texts(item))
        return values
    text = str(raw).strip()
    return [text] if text else []


def first_text(raw: Any) -> str:
    values = field_texts(raw)
    return values[0] if values else ""


def candidate_texts(fields: dict[str, Any], candidates: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    for name in candidates:
        if name in fields:
            values.extend(field_texts(fields.get(name)))
    return values


def attachments(raw: Any) -> list[dict[str, Any]]:
    if not raw:
        return []
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def video_attachments(raw: Any) -> list[dict[str, Any]]:
    result = [
        item
        for item in attachments(raw)
        if str(item.get("type", "")).startswith("video")
        or Path(str(item.get("name") or "")).suffix.lower() in VIDEO_EXTS
    ]
    return result or attachments(raw)


def classify_asset_kind(fields: dict[str, Any], fallback_texts: list[str] | None = None) -> str:
    texts = candidate_texts(fields, ASSET_FIELD_CANDIDATES) + list(fallback_texts or [])
    lowered = " ".join(texts).lower()
    if any(token in lowered for token in ("表情", "订单", "劇情", "剧情", "emoji", "order")):
        return "emoji"
    if any(token in lowered for token in ("场景", "場景", "角色动画", "角色動畫", "scene", "character animation")):
        return "scene"
    return "emoji"


def classify_process_variant(fields: dict[str, Any]) -> str:
    # The main animation automation flow keeps Cherry post-processing as an
    # explicit smooth stage after ComfyUI matting.
    return "default"


def unity_types(fields: dict[str, Any], asset_kind: str, scene_default: str = "角色动画") -> list[str]:
    values = []
    for text in candidate_texts(fields, TYPE_FIELD_CANDIDATES):
        if text in {"scene", "emoji", "default", "temporal_smooth"}:
            continue
        values.append(text)
    if asset_kind == "scene" and not values:
        values.append(scene_default)
    seen: set[str] = set()
    result: list[str] = []
    for item in values:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def task_key(character: str, animation: str) -> str:
    return f"{safe_name(character)}-{safe_name(animation)}"


def source_manifest_path(date_root: Path) -> Path:
    return date_root / "source_manifest.json"


def load_source_manifest(date_root: Path) -> dict[str, Any]:
    path = source_manifest_path(date_root)
    if not path.exists():
        raise FileNotFoundError(str(path))
    return json.loads(path.read_text(encoding="utf-8"))


def routed_stage_dir(date_root: Path, asset_kind: str, variant: str, stage: str, key: str | None = None) -> Path:
    path = date_root / asset_kind / stage
    return path / key if key else path


def write_source_manifest(date_root: Path, manifest: dict[str, Any]) -> Path:
    date_root.mkdir(parents=True, exist_ok=True)
    path = source_manifest_path(date_root)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def ensure_split_dirs(date_root: Path) -> None:
    for asset_kind in ASSET_KINDS:
        for stage in ("videos", "frames", "matte", "smooth"):
            (date_root / asset_kind / stage).mkdir(parents=True, exist_ok=True)


def detect_frame_sequence(files: list[Path]) -> tuple[list[Path], list[str]]:
    pngs = sorted([path for path in files if path.is_file() and path.suffix.lower() == ".png"], key=lambda p: p.name.lower())
    warnings: list[str] = []
    nums: list[int] = []
    for path in pngs:
        match = re.search(r"(\d+)(?=\.png$)", path.name, flags=re.IGNORECASE)
        if match:
            nums.append(int(match.group(1)))
    if nums:
        expected = set(range(min(nums), max(nums) + 1))
        missing = sorted(expected - set(nums))
        if missing:
            warnings.append("缺帧: " + ", ".join(f"{num:04d}.png" for num in missing[:20]))
    return pngs, warnings


def copy_or_link(src: Path, dst: Path, mode: str, warnings: list[str]) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if mode == "hardlink":
        try:
            os.link(src, dst)
            return
        except OSError as exc:
            warnings.append(f"hardlink failed, fallback copy: {src} -> {dst}: {exc}")
    shutil.copy2(src, dst)


def build_unity_ready(
    date_root: Path,
    overwrite: bool = False,
    copy_mode: str = "copy",
    include_empty_types: bool = False,
    scene_unity_category: str = "角色动画",
    missing_smooth_is_error: bool = True,
) -> dict[str, Any]:
    if copy_mode not in {"copy", "hardlink"}:
        raise ValueError("--copy-mode must be copy or hardlink")
    manifest = load_source_manifest(date_root)
    ready_root = date_root / "unity_ready"
    if ready_root.exists():
        if not overwrite:
            raise FileExistsError(f"{ready_root} already exists. Pass --overwrite to rebuild.")
        shutil.rmtree(ready_root)

    package_tasks: dict[str, dict[str, dict[str, Any]]] = {kind: {} for kind in ASSET_KINDS}
    warnings: list[str] = []
    for record in manifest.get("records") or []:
        if record.get("skipped"):
            continue
        asset_kind = str(record.get("assetKind") or "")
        if asset_kind not in ASSET_KINDS:
            warnings.append(f"unknown asset kind skipped: {record.get('recordId')}")
            continue
        types = list(record.get("types") or [])
        if not types and include_empty_types:
            types = [scene_unity_category if asset_kind == "scene" else "剧情"]
        if not types:
            continue
        key = str(record.get("taskKey") or task_key(record.get("character", ""), record.get("animation", "")))
        variant = str(record.get("processVariant") or "default")
        source_dir = routed_stage_dir(date_root, asset_kind, variant, "smooth", key)
        if not source_dir.is_dir():
            matte_dir = routed_stage_dir(date_root, asset_kind, variant, "matte", key)
            if not missing_smooth_is_error and matte_dir.is_dir():
                warnings.append(f"missing smooth dir, fallback matte: {asset_kind}/{key}")
                source_dir = matte_dir
        if not source_dir.is_dir():
            message = f"missing source image dir for {asset_kind}/{key}: {source_dir}"
            if missing_smooth_is_error:
                raise FileNotFoundError(message)
            warnings.append(message)
            continue
        pngs, seq_warnings = detect_frame_sequence(list(source_dir.iterdir()))
        for warning in seq_warnings:
            warnings.append(f"{key} {warning}")
        if not pngs:
            raise FileNotFoundError(f"source image dir has no png for {asset_kind}/{key}: {source_dir}")
        existing = package_tasks[asset_kind].get(key)
        if existing:
            if Path(existing["sourceImageDir"]).resolve() != source_dir.resolve():
                raise ValueError(
                    f"同一个 unity_ready/{asset_kind} 里出现重复任务 {key}：\n"
                    f"- {existing['sourceImageDir']}\n"
                    f"- {source_dir}\n"
                    "请检查飞书表格是否重复，或手动确认使用哪一条。"
                )
            merged_types = list(existing["types"])
            for value in types:
                if value not in merged_types:
                    merged_types.append(value)
            existing["types"] = merged_types
            continue
        package_tasks[asset_kind][key] = {
            "character": str(record.get("character") or ""),
            "animation": str(record.get("animation") or ""),
            "displayName": str(record.get("displayName") or ""),
            "types": sorted(set(types), key=types.index),
            "processVariant": variant,
            "sourceImageDir": str(source_dir),
            "sourceSmoothDir": str(source_dir),
            "frameCount": len(pngs),
            "pngs": pngs,
        }

    ready_root.mkdir(parents=True, exist_ok=True)
    packages: dict[str, Any] = {}
    for asset_kind in ASSET_KINDS:
        pkg_root = ready_root / asset_kind
        frames_root = pkg_root / "frames"
        items: dict[str, dict[str, dict[str, Any]]] = {}
        tasks: list[dict[str, Any]] = []
        for key, item in sorted(package_tasks[asset_kind].items()):
            ready_dir = frames_root / key
            for index, src in enumerate(item["pngs"]):
                match = re.search(r"(\d+)(?=\.png$)", src.name, flags=re.IGNORECASE)
                if match:
                    dst_name = f"{int(match.group(1)):04d}.png"
                else:
                    dst_name = f"{index:04d}.png"
                    warnings.append(f"{key} frame name has no number, normalized {src.name} -> {dst_name}")
                copy_or_link(src, ready_dir / dst_name, copy_mode, warnings)
            character = item["character"]
            animation = item["animation"]
            items.setdefault(character, {})[animation] = {
                "name": item["displayName"],
                "types": item["types"],
            }
            tasks.append(
                {
                    "character": character,
                    "animation": animation,
                    "displayName": item["displayName"],
                    "unityCategories": item["types"],
                    "processVariant": item["processVariant"],
                    "sourceImageDir": os.path.relpath(item["sourceImageDir"], ready_root).replace("\\", "/"),
                    "sourceSmoothDir": os.path.relpath(item["sourceImageDir"], ready_root).replace("\\", "/"),
                    "readyDir": f"{asset_kind}/frames/{key}",
                    "frameCount": item["frameCount"],
                }
            )
        pkg_root.mkdir(parents=True, exist_ok=True)
        (pkg_root / "animation_resource_manifest.json").write_text(
            json.dumps({"items": items}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        packages[asset_kind] = {
            "json": f"{asset_kind}/animation_resource_manifest.json",
            "framesRoot": f"{asset_kind}/frames",
            "tasks": tasks,
        }

    report = {
        "generatedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "dateRoot": str(date_root),
        "sourceManifest": "../source_manifest.json",
        "packages": packages,
        "warnings": warnings,
    }
    (ready_root / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def format_unity_ready_summary(date_root: Path, report: dict[str, Any]) -> str:
    ready = date_root / "unity_ready"
    lines = ["【Unity Ready 已生成】", ""]
    for label, key in (("Scene", "scene"), ("Emoji", "emoji")):
        package = report["packages"][key]
        lines.extend(
            [
                f"{label}:",
                f"JSON: {ready / package['json']}",
                f"Frames: {ready / package['framesRoot']}",
                f"Tasks: {len(package['tasks'])}",
                "",
            ]
        )
    warnings = report.get("warnings") or []
    if warnings:
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in warnings)
        lines.append("")
    lines.extend(["下一步：", "在 Unity 插件中分别选择对应 JSON 和 Frames 源根目录导入。", "导入完成后，再运行 P4 shelve-only 流程。"])
    return "\n".join(lines)
