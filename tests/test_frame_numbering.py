from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from feishu_frame_tool.dedup import dedup_folder
from feishu_frame_tool.extractor import LocalFrameExtractor


def _image(path: Path, value: int) -> None:
    Image.new("RGBA", (4, 4), (value, value, value, 255)).save(path)


def test_dedup_renumber_starts_at_zero(tmp_path: Path) -> None:
    _image(tmp_path / "0001.png", 10)
    _image(tmp_path / "0002.png", 40)
    _image(tmp_path / "0003.png", 80)

    kept, removed = dedup_folder(str(tmp_path), diff_threshold=0.0, renumber=True)

    assert kept == 3
    assert removed == 0
    assert [path.name for path in sorted(tmp_path.glob("*.png"))] == ["0000.png", "0001.png", "0002.png"]


def test_local_frame_extractor_starts_at_zero(tmp_path: Path) -> None:
    video = tmp_path / "source.mp4"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 24, (8, 8))
    writer.write(np.full((8, 8, 3), 40, dtype=np.uint8))
    writer.write(np.full((8, 8, 3), 80, dtype=np.uint8))
    writer.release()

    out = Path(LocalFrameExtractor(str(tmp_path / "frames"), fps=24).process_video(str(video), "sample"))

    assert [path.name for path in sorted(out.glob("*.png"))] == ["0000.png", "0001.png"]
