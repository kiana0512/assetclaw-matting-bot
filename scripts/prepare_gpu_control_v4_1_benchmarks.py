from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from assetclaw_matting.services.gpu_control_batch import build_input_batch


DEFAULT_PRIMARY = Path("storage/direct_video_runs/VID_717F2D6BC22C/frames/video_01")
DEFAULT_SUPPLEMENTS = (
    Path("storage/direct_video_runs/VID_BD18EE2C8C8F/frames/video_01"),
    Path("storage/direct_video_runs/VID_C4FCD2951DC0/frames/video_01"),
)
DEFAULT_COUNTS = (1, 6, 30, 64, 97, 300)
ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    partial.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(partial, path)


def _source_files(root: Path) -> list[Path]:
    if not root.is_dir():
        raise FileNotFoundError(f"benchmark source root does not exist: {root}")
    return sorted(
        (path.resolve() for path in root.rglob("*") if path.is_file() and path.suffix.lower() in ALLOWED_SUFFIXES),
        key=lambda path: path.relative_to(PROJECT_ROOT).as_posix(),
    )


def _unique_pool(primary: Path, supplements: list[Path]) -> tuple[list[Path], int]:
    primary_files = _source_files(primary)
    files: list[Path] = []
    seen: set[str] = set()
    for source_root in [primary, *supplements]:
        for path in _source_files(source_root):
            key = os.path.normcase(str(path))
            if key in seen:
                continue
            seen.add(key)
            files.append(path)
    return files, len(primary_files)


def _frame_fact(path: Path, ordinal: int) -> dict[str, Any]:
    with Image.open(path) as image:
        width, height = image.size
        mode = image.mode
        image_format = image.format or ""
    return {
        "ordinal": ordinal,
        "source_relative_path": path.relative_to(PROJECT_ROOT).as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256_file(path),
        "width": width,
        "height": height,
        "input_pixels": width * height,
        "mode": mode,
        "format": image_format,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Freeze B1/B6/B30/B64/B97/B300 GPU Control V4.1 benchmark bundles without network calls."
    )
    parser.add_argument("--session", default="v4_1-20260730-r1")
    parser.add_argument("--output-root", type=Path, default=Path("storage/gpu_control_v4_1_acceptance/frozen_inputs"))
    parser.add_argument("--primary-root", type=Path, default=DEFAULT_PRIMARY)
    parser.add_argument("--supplement-root", type=Path, action="append", default=[])
    parser.add_argument("--counts", type=int, nargs="+", default=list(DEFAULT_COUNTS))
    args = parser.parse_args()

    counts = sorted(set(args.counts))
    if not counts or any(count < 1 or count > 5000 for count in counts):
        parser.error("--counts must contain unique values in the range 1-5000")
    primary = args.primary_root.resolve()
    supplements = [path.resolve() for path in (args.supplement_root or list(DEFAULT_SUPPLEMENTS))]
    pool, primary_count = _unique_pool(primary, supplements)
    if primary_count < 97:
        raise RuntimeError(f"primary B97 source must contain at least 97 frames, found {primary_count}: {primary}")
    if len(pool) < max(counts):
        raise RuntimeError(f"benchmark source pool has {len(pool)} frames, needs {max(counts)}")

    session_root = args.output_root.resolve() / args.session
    index_path = session_root / "bundle_index.json"
    generated_at = datetime.now(timezone.utc).isoformat()
    if index_path.is_file():
        try:
            existing_index = json.loads(index_path.read_text(encoding="utf-8"))
            generated_at = str(existing_index.get("generated_at") or generated_at)
        except (OSError, ValueError):
            raise RuntimeError(f"existing bundle index is unreadable: {index_path}") from None
    bundle_index: dict[str, Any] = {
        "schema_version": "1.0",
        "acceptance_session_id": args.session,
        "generated_at": generated_at,
        "network_calls": False,
        "source_files_modified": False,
        "primary_b97_source": str(primary),
        "supplement_sources": [str(path) for path in supplements],
        "selection_policy": "B1-B97 are prefixes of primary source; B300 extends the same prefix from ordered supplements",
        "bundles": {},
    }

    for count in counts:
        name = f"B{count}"
        files = pool[:count]
        if count <= 97 and any(path.parent != primary for path in files):
            raise RuntimeError(f"{name} must be sourced only from the frozen primary B97 sample")
        bundle_root = session_root / name
        external_batch_id = f"assetclaw-benchmark:v4_1:{args.session}:{name}:g1"
        prepared = build_input_batch(
            f"BENCH_{name}_{args.session}",
            PROJECT_ROOT,
            files,
            bundle_root,
            preserve_structure=True,
            external_batch_id=external_batch_id,
            parameters={},
        )
        archive_path = Path(prepared["archive_path"])
        manifest_path = Path(prepared["manifest_path"])
        frame_facts = [_frame_fact(path, ordinal) for ordinal, path in enumerate(files)]
        metadata = {
            "schema_version": "1.0",
            "bundle": name,
            "frame_count": count,
            "input_pixels_total": sum(item["input_pixels"] for item in frame_facts),
            "input_source_bytes_total": sum(item["size_bytes"] for item in frame_facts),
            "input_archive_bytes": archive_path.stat().st_size,
            "input_archive_sha256": _sha256_file(archive_path),
            "input_manifest_sha256": prepared["manifest_sha256"],
            "input_manifest_file_sha256": _sha256_file(manifest_path),
            "external_batch_id_template": external_batch_id,
            "workflow_identity": {
                "workflow_key": "imageclip-rgba",
                "workflow_version": "2026.07.30-691770c-r1",
                "pipeline_commit": "691770cd6a59fd7c51391456fe900dc57a313233",
                "pipeline_sha256": "00e7104762f0a1fdf3a4c20e043bec2b9f088132452d5a5ce4302ba268edac0b",
                "output_node": "SaveImage #25",
            },
            "frames": frame_facts,
        }
        metadata["metadata_sha256"] = _canonical_sha256(metadata)
        metadata_path = bundle_root / "benchmark_metadata.json"
        _write_json_atomic(metadata_path, metadata)
        bundle_index["bundles"][name] = {
            "frame_count": count,
            "input_pixels_total": metadata["input_pixels_total"],
            "input_archive": str(archive_path),
            "input_archive_bytes": metadata["input_archive_bytes"],
            "input_archive_sha256": metadata["input_archive_sha256"],
            "input_manifest": str(manifest_path),
            "input_manifest_sha256": metadata["input_manifest_sha256"],
            "benchmark_metadata": str(metadata_path),
            "metadata_sha256": metadata["metadata_sha256"],
        }
        print(
            f"FROZEN {name} frames={count} pixels={metadata['input_pixels_total']} "
            f"archive_sha256={metadata['input_archive_sha256']}",
            flush=True,
        )

    index_without_hash = dict(bundle_index)
    bundle_index["bundle_index_sha256"] = _canonical_sha256(index_without_hash)
    _write_json_atomic(index_path, bundle_index)
    print(f"INDEX {index_path}", flush=True)
    print(f"INDEX_SHA256 {bundle_index['bundle_index_sha256']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
