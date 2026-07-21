from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_matte_identity(
    source_path: str | Path,
    matte_path: str | Path,
    *,
    max_weighted_mae: float = 0.08,
) -> dict[str, Any]:
    source = Path(source_path)
    matte = Path(matte_path)
    with Image.open(source) as image:
        source_image = ImageOps.exif_transpose(image).convert("RGB")
        source_size = source_image.size
        source_small = np.asarray(
            source_image.resize((96, 128), Image.Resampling.BILINEAR), dtype=np.float32
        ) / 255.0
    with Image.open(matte) as image:
        matte_image = ImageOps.exif_transpose(image).convert("RGBA")
        matte_size = matte_image.size
        matte_small = np.asarray(
            matte_image.resize((96, 128), Image.Resampling.BILINEAR), dtype=np.float32
        ) / 255.0

    if source_size != matte_size:
        raise RuntimeError(
            f"frame identity check failed: size changed for {source.name}: "
            f"source={source_size[0]}x{source_size[1]} matte={matte_size[0]}x{matte_size[1]}"
        )
    alpha = matte_small[..., 3]
    weight = np.clip((alpha - 0.25) / 0.75, 0.0, 1.0)[..., None]
    coverage = float((alpha >= 0.5).mean())
    if coverage < 0.002:
        raise RuntimeError(f"frame identity check failed: foreground alpha is empty for {matte.name}")
    denominator = max(float(weight.sum()) * 3.0, 1.0)
    weighted_mae = float((np.abs(source_small - matte_small[..., :3]) * weight).sum() / denominator)
    if weighted_mae > max_weighted_mae:
        raise RuntimeError(
            f"frame identity check failed: {source.name} does not match {matte.name}; "
            f"weighted_mae={weighted_mae:.4f} limit={max_weighted_mae:.4f}"
        )
    return {
        "source": str(source),
        "output": str(matte),
        "source_sha256": sha256_file(source),
        "weighted_mae": round(weighted_mae, 6),
        "foreground_coverage": round(coverage, 6),
    }


def validate_sequence_names(source_dir: str | Path, output_dir: str | Path, *, label: str) -> dict[str, Any]:
    source = Path(source_dir)
    output = Path(output_dir)
    source_names = sorted(path.name for path in source.glob("*.png") if path.is_file())
    output_names = sorted(path.name for path in output.glob("*.png") if path.is_file())
    missing = sorted(set(source_names) - set(output_names))
    extra = sorted(set(output_names) - set(source_names))
    if not source_names:
        raise RuntimeError(f"sequence integrity check failed: source sequence is empty: {source}")
    if source_names != output_names:
        raise RuntimeError(
            f"sequence integrity check failed ({label}): expected={len(source_names)} actual={len(output_names)} "
            f"missing={missing[:8]} extra={extra[:8]}"
        )
    return {
        "label": label,
        "count": len(source_names),
        "first": source_names[0],
        "last": source_names[-1],
        "names_match": True,
    }


def validate_matte_sequence(source_dir: str | Path, matte_dir: str | Path) -> dict[str, Any]:
    report = validate_sequence_names(source_dir, matte_dir, label="frames_to_matte")
    source = Path(source_dir)
    matte = Path(matte_dir)
    checks = [
        validate_matte_identity(source / name, matte / name)
        for name in sorted(path.name for path in source.glob("*.png"))
    ]
    scores = [float(item["weighted_mae"]) for item in checks]
    report.update(
        {
            "identity_verified": len(checks),
            "weighted_mae_mean": round(sum(scores) / len(scores), 6),
            "weighted_mae_max": round(max(scores), 6),
        }
    )
    return report
