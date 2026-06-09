"""本地视频抽帧（OpenCV）。

从下载的视频按目标帧率重采样，导出原始画面的 PNG 序列帧。
不依赖任何网页/外部服务；速度快、稳定、可离线。

说明：
- 保留视频原始画面（不做抠图/透明背景），按原始尺寸导出。
- 目标帧率 < 源帧率时做重采样（抽稀）；否则导出每一帧。
- 兼容中文路径：读取视频时复制到临时 ASCII 路径，写 PNG 用 imencode+tofile。
"""

import os
import shutil
import tempfile
from typing import Callable, Optional

import cv2


class ExtractError(RuntimeError):
    pass


def _save_png(path: str, frame) -> None:
    """写 PNG，兼容中文路径（cv2.imwrite 在 Windows 不支持非 ASCII 路径）。"""
    ok, buf = cv2.imencode(".png", frame)
    if not ok:
        raise ExtractError("PNG 编码失败")
    buf.tofile(path)


class LocalFrameExtractor:
    """本地抽帧器。对外接口与旧的 framepacker 自动化保持一致：process_video()。"""

    def __init__(self, export_dir: str, fps: int = 24, max_frames: int = 0,
                 logger: Optional[Callable[[str], None]] = None):
        self.export_dir = export_dir
        self.fps = int(fps) if fps else 0
        self.max_frames = int(max_frames) if max_frames else 0
        self.log = logger or (lambda m: None)

    def process_video(self, video_path: str, out_subdir: str) -> str:
        """对单个视频抽帧并导出到 export_dir/out_subdir，返回该输出目录。"""
        dest = os.path.join(self.export_dir, out_subdir)
        os.makedirs(dest, exist_ok=True)

        # OpenCV 在 Windows 对中文路径支持不稳，先复制到临时 ASCII 路径再读
        tmp = None
        read_path = video_path
        if not video_path.isascii():
            suffix = os.path.splitext(video_path)[1] or ".mp4"
            fd, tmp = tempfile.mkstemp(suffix=suffix)
            os.close(fd)
            shutil.copyfile(video_path, tmp)
            read_path = tmp

        try:
            cap = cv2.VideoCapture(read_path)
            if not cap.isOpened():
                raise ExtractError(f"无法打开视频: {video_path}")
            src_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            target = self.fps
            resample = bool(target and src_fps and target < src_fps)
            self.log(f"抽帧: 源 {src_fps:.2f}fps / 约 {total} 帧 -> "
                     f"目标 {target or '全部'}fps"
                     + ("（重采样）" if resample else "（逐帧）")
                     + (f"，最多 {self.max_frames} 帧" if self.max_frames else ""))

            emit_interval = 1.0 / target if target else 0.0
            next_emit = 0.0
            frame_idx = 0
            out_idx = 0
            try:
                while True:
                    ret, frame = cap.read()
                    if not ret:
                        break
                    keep = True
                    if resample:
                        cur_time = frame_idx / src_fps
                        if cur_time + 1e-9 >= next_emit:
                            next_emit += emit_interval
                        else:
                            keep = False
                    if keep:
                        _save_png(os.path.join(dest, f"{out_idx:04d}.png"), frame)
                        out_idx += 1
                        if self.max_frames and out_idx >= self.max_frames:
                            break
                    frame_idx += 1
            finally:
                cap.release()

            if out_idx == 0:
                raise ExtractError(f"未能从视频中抽取任何帧: {video_path}")
            self.log(f"抽帧完成: 导出 {out_idx} 帧 -> {dest}")
            return dest
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
