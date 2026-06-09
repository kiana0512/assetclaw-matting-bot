"""本地相似帧去重。

对一个目录内的 PNG 序列帧，按文件名自然排序后逐帧与「上一张保留帧」比对，
差异低于阈值（太相似）的帧将被删除；可选删除后重新连续编号。

比对方式：缩略到小图、按 RGBA 计算平均像素差异，归一化为 0~100 的百分比。
不依赖网页，纯本地，结果可复现、阈值可调。
"""

import os
import re
from typing import Callable, List, Optional, Tuple

import numpy as np
from PIL import Image

_THUMB = (64, 64)  # 比对用缩略图尺寸，越小越快、对位移越不敏感


def _natural_key(name: str):
    """按文件名中的数字自然排序（001.png < 002.png < 010.png）。"""
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", name)]


def _list_pngs(folder: str) -> List[str]:
    names = [n for n in os.listdir(folder) if n.lower().endswith(".png")]
    names.sort(key=_natural_key)
    return names


def _fingerprint(path: str) -> np.ndarray:
    """读取图片 -> 缩略 RGBA -> float 数组（用于差异比对）。"""
    with Image.open(path) as im:
        im = im.convert("RGBA").resize(_THUMB, Image.BILINEAR)
        return np.asarray(im, dtype=np.float32)


def _diff_percent(a: np.ndarray, b: np.ndarray) -> float:
    """两张指纹图的平均像素差异，归一化为 0~100 的百分比。"""
    return float(np.abs(a - b).mean() / 255.0 * 100.0)


def dedup_folder(folder: str, diff_threshold: float = 2.5,
                 renumber: bool = True,
                 logger: Optional[Callable[[str], None]] = None) -> Tuple[int, int]:
    """对 folder 内 PNG 序列帧去重。

    返回 (保留数, 删除数)。差异 < diff_threshold 的帧视为相似帧并删除。
    """
    log = logger or (lambda m: None)
    names = _list_pngs(folder)
    if len(names) <= 1:
        return len(names), 0

    kept: List[str] = []
    removed = 0
    ref: Optional[np.ndarray] = None

    for name in names:
        path = os.path.join(folder, name)
        try:
            fp = _fingerprint(path)
        except Exception as e:
            log(f"⚠ 读取失败，保留该帧 {name}: {e}")
            kept.append(name)
            ref = None  # 读取失败则不更新参考，避免误删后续
            continue

        if ref is None:
            kept.append(name)
            ref = fp
            continue

        d = _diff_percent(fp, ref)
        if d < diff_threshold:
            try:
                os.remove(path)
                removed += 1
            except OSError as e:
                log(f"⚠ 删除失败，保留 {name}: {e}")
                kept.append(name)
                ref = fp
        else:
            kept.append(name)
            ref = fp  # 保留帧成为新的参考

    log(f"相似帧去重: 原 {len(names)} 帧 -> 保留 {len(kept)} 帧，删除 {removed} 帧 "
        f"(阈值 {diff_threshold}%)")

    if renumber and kept:
        _renumber(folder, kept, log)

    return len(kept), removed


def _renumber(folder: str, kept: List[str], log: Callable[[str], None]) -> None:
    """把保留的帧重新连续编号为 0000.png、0001.png ...（两阶段重命名防冲突）。"""
    width = max(4, len(str(len(kept))))
    # 阶段一：先改成临时名，避免与现有文件名冲突
    tmp_names = []
    for i, name in enumerate(kept):
        tmp = f"__dedup_tmp_{i:0{width}d}.png"
        os.rename(os.path.join(folder, name), os.path.join(folder, tmp))
        tmp_names.append(tmp)
    # 阶段二：临时名 -> 最终连续编号
    for i, tmp in enumerate(tmp_names):
        final = f"{i:0{width}d}.png"
        os.rename(os.path.join(folder, tmp), os.path.join(folder, final))
    log(f"已重新编号为 {0:0{width}d}.png ~ {len(kept) - 1:0{width}d}.png")
