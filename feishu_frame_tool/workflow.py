"""Read Feishu records, download animation videos, and export local frames."""

import os
import re
import json
import shutil
import threading
from pathlib import Path
from typing import Callable, Optional

from feishu_client import FeishuClient
from extractor import LocalFrameExtractor
from dedup import dedup_folder

# 项目根目录：相对路径（如 ./downloads）一律相对它解析，
# 避免因启动方式不同（当前工作目录不同）把文件写到别处。
HERE = os.path.dirname(os.path.abspath(__file__))


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _project_path(p: str) -> str:
    """把相对路径固定为相对项目根目录解析；绝对路径原样返回。"""
    return p if os.path.isabs(p) else os.path.abspath(os.path.join(HERE, p))


def _safe_name(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", str(s)).strip("_") or "rec"


def _progress_value(raw) -> str:
    """单选字段值归一化为文本。"""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        return raw.get("text") or raw.get("name") or ""
    if isinstance(raw, list) and raw:
        return _progress_value(raw[0])
    return str(raw)


def _attachments(raw) -> list:
    """动画附件字段值归一化为附件 dict 列表。"""
    if not raw:
        return []
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [a for a in raw if isinstance(a, dict)]
    return []


def _parent_record_id(raw) -> Optional[str]:
    """从「父记录」双向关联字段中取出父记录 record_id（无则 None）。

    字段值形如：[{"record_ids": ["recXXX"], "text": "susan", ...}]
    顶层节点的 record_ids 为 None。
    """
    if isinstance(raw, list) and raw:
        ids = raw[0].get("record_ids") if isinstance(raw[0], dict) else None
        if ids:
            return ids[0]
    return None


class Workflow:
    def __init__(self, config: dict, logger: Optional[Callable[[str], None]] = None,
                 stop_event: Optional[threading.Event] = None):
        self.cfg = config
        self.log = logger or (lambda m: None)
        self.stop_event = stop_event or threading.Event()

        self.client = FeishuClient.from_feishu_config(config["feishu"], logger=self.log)

        self.f_animation = config["fields"]["animation"]
        self.f_role = config["fields"].get("role", "角色")
        self.f_animation_name = config["fields"].get("animation_name", "动画名")
        self.f_parent = config["fields"].get("parent", "父记录")
        self.rec_map: dict = {}
        self.manifest_items: list[dict] = []

        self.download_dir = _project_path(config["paths"]["download_dir"])
        self.export_dir = _project_path(config["paths"]["export_dir"])

        dd = config.get("dedup", {})
        self.dedup_enabled = bool(dd.get("enabled", False))
        self.dedup_threshold = float(dd.get("diff_threshold", 2.5))
        self.dedup_renumber = bool(dd.get("renumber", True))

        fp = config.get("framepacker", {})
        self.fps = int(fp.get("fps", 24))
        self.max_frames = int(fp.get("max_frames", 0) or 0)

    def _stopped(self) -> bool:
        return self.stop_event.is_set()

    def _clear_dir(self, path: str) -> None:
        """若目录已存在且有旧文件，先清空其内容（文件与子目录）。"""
        if not os.path.isdir(path):
            return
        removed = 0
        for name in os.listdir(path):
            p = os.path.join(path, name)
            try:
                if os.path.isfile(p) or os.path.islink(p):
                    os.remove(p)
                else:
                    shutil.rmtree(p)
                removed += 1
            except OSError as e:
                self.log(f"⚠ 清理旧文件失败 {p}: {e}")
        if removed:
            self.log(f"发现旧文件，已清空 {removed} 项: {path}")

    def run(self) -> None:
        os.makedirs(self.download_dir, exist_ok=True)
        os.makedirs(self.export_dir, exist_ok=True)

        self.log("读取飞书表格记录 ...")
        records = self.client.list_records()
        self.log(f"共 {len(records)} 条记录")
        # 建立 record_id -> 记录 映射，用于沿「父记录」回溯角色层级路径
        self.rec_map = {r.get("record_id", ""): r for r in records}
        animation_records = [r for r in records if _attachments(r.get("fields", {}).get(self.f_animation))]
        self.log(f"发现 {len(animation_records)} 条有动画附件的记录")

        extractor = LocalFrameExtractor(
            export_dir=self.export_dir, fps=self.fps, max_frames=self.max_frames, logger=self.log)

        processed = 0
        for rec in animation_records:
            if self._stopped():
                self.log("已停止。")
                break
            rid = rec.get("record_id", "")
            fields = rec.get("fields", {})
            self._process_record(rid, fields, extractor)
            processed += 1

        self.log(f"完成。处理了 {processed} 条有动画附件的记录。")

    def _role_parts(self, rid: str) -> list[str]:
        """沿「父记录」自底向上回溯，返回可读层级，如 ["gary", "idle"]。"""
        parts = []
        seen = set()
        cur = rid
        while cur and cur not in seen and cur in self.rec_map:
            seen.add(cur)
            f = self.rec_map[cur].get("fields", {})
            role = _progress_value(f.get(self.f_role))
            if role:
                parts.append(role)
            cur = _parent_record_id(f.get(self.f_parent))
        parts.reverse()
        if len(parts) > 2:
            parts = parts[-2:]
        return [_safe_name(p) for p in parts] or [_safe_name(rid)]

    def _record_identity(self, rid: str, fields: dict) -> dict:
        parts = self._role_parts(rid)
        if len(parts) < 2:
            raise ValueError(f"Cannot resolve character/emotion for record {rid}. Check parent record mapping.")
        role = parts[-2]
        emotion = parts[-1]
        animation_name = _progress_value(fields.get(self.f_animation_name))
        rel_dir = os.path.join(role, emotion)
        return {
            "record_id": rid,
            "role": role,
            "emotion": emotion,
            "animation_name": animation_name,
            "rel_dir": rel_dir,
            "label": f"{role}/{emotion}" + (f"（{animation_name}）" if animation_name else ""),
        }

    def _write_manifest(self) -> None:
        path = os.path.join(self.export_dir, "_pipeline_manifest.json")
        os.makedirs(self.export_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"items": self.manifest_items}, f, ensure_ascii=False, indent=2)

    def _process_record(self, rid: str, fields: dict, extractor: LocalFrameExtractor) -> None:
        identity = self._record_identity(rid, fields)
        role = identity["role"]
        emotion = identity["emotion"]
        rel_dir = identity["rel_dir"]
        self.log(f"——— 处理记录 {rid}（{identity['label']}）———")
        attachments = _attachments(fields.get(self.f_animation))
        videos = [a for a in attachments
                  if str(a.get("type", "")).startswith("video")
                  or str(a.get("name", "")).lower().endswith((".mp4", ".mov", ".webm", ".m4v"))]
        if not videos:
            videos = attachments  # 兜底：没有明确视频类型则全部尝试
        if not videos:
            self.log(f"记录 {rid} 没有可下载的「{self.f_animation}」附件，跳过。")
            return

        # 1) 下载视频（目录 = 角色/情绪，文件名 = 角色_情绪.ext）
        #    下载前先清空该记录的旧视频文件，确保每次都是全新内容
        rec_dir = os.path.join(self.download_dir, rel_dir)
        self._clear_dir(rec_dir)
        downloaded = []
        for index, att in enumerate(videos):
            if self._stopped():
                return
            raw_name = str(att.get("name") or "")
            suffix = Path(raw_name).suffix or ".mp4"
            stem = f"{role}_{emotion}" if len(videos) == 1 else f"{role}_{emotion}_{index + 1}"
            save_name = _safe_name(stem) + suffix
            self.log(f"下载视频：{identity['label']} -> {os.path.join(rel_dir, save_name)}")
            path = self.client.download_attachment(
                att, rec_dir, field_name=self.f_animation, record_id=rid, save_name=save_name)
            downloaded.append(path)

        # 2) 抽帧 + 导出 PNG 序列帧（导出子目录同样用角色层级路径）
        for i, video in enumerate(downloaded):
            if self._stopped():
                return
            # 同一记录有多个视频时追加序号，避免目录互相覆盖
            out_subdir = rel_dir if len(downloaded) == 1 else os.path.join(rel_dir, f"video_{i + 1}")
            # 抽帧前清空导出目录的旧序列帧，避免新旧帧混在一起干扰去重
            self._clear_dir(os.path.join(self.export_dir, out_subdir))
            self.log(f"本地抽帧：{identity['label']} / {os.path.basename(video)} (fps={self.fps})")
            dest = extractor.process_video(video, out_subdir)

            # 2.5) 本地去除相似帧
            if self.dedup_enabled:
                try:
                    dedup_folder(dest, diff_threshold=self.dedup_threshold,
                                 renumber=self.dedup_renumber, logger=self.log)
                except Exception as e:
                    self.log(f"⚠ 相似帧去重出错（已保留全部帧）: {e}")
            frames = sorted(name for name in os.listdir(dest) if name.lower().endswith(".png"))
            self.manifest_items.append({
                **identity,
                "video_path": video,
                "frame_dir": dest,
                "frame_rel_dir": out_subdir.replace("\\", "/"),
                "frame_count": len(frames),
                "frames": frames,
            })
            self._write_manifest()
            self.log(f"抽帧记录：{identity['label']} -> {len(frames)} 帧")

        self.log(f"记录完成：{identity['label']}")
