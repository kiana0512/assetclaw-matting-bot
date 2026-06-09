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
from tools.animation_automation.core import (
    SKIP_PROGRESS,
    classify_asset_kind,
    classify_process_variant,
    ensure_split_dirs,
    first_text,
    routed_stage_dir,
    safe_name,
    task_key,
    unity_types,
    video_attachments,
    write_source_manifest,
)

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
    return safe_name(s)


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


def _field_texts(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        return [text] if text else []
    if isinstance(raw, dict):
        text = str(raw.get("text") or raw.get("name") or raw.get("value") or "").strip()
        return [text] if text else []
    if isinstance(raw, list):
        values = []
        for item in raw:
            values.extend(_field_texts(item))
        return values
    return [str(raw)]


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
        self.f_type = config["fields"].get("type", "类型")
        self.f_progress = config["fields"].get("progress", "进度")
        self.f_process_option = config["fields"].get("process_option", "处理选项")
        selection = config.get("selection", {})
        self.selection_root = str(selection.get("root") or "").strip()
        self.selection_emotions = {
            _safe_name(item)
            for item in selection.get("emotions", [])
            if str(item or "").strip()
        }
        self.selection_types = {str(item).strip() for item in selection.get("types", []) if str(item or "").strip()}
        self.progress_include = {str(item).strip() for item in selection.get("progress_include", []) if str(item or "").strip()}
        self.progress_exclude = {str(item).strip() for item in selection.get("progress_exclude", []) if str(item or "").strip()}
        self.rec_map: dict = {}
        self.manifest_items: list[dict] = []

        self.download_dir = _project_path(config["paths"]["download_dir"])
        self.export_dir = _project_path(config["paths"]["export_dir"])
        routing = config.get("routing", {})
        self.routing_enabled = bool(routing.get("enabled", True))
        self.date_root = Path(routing.get("date_root") or self._infer_date_root()).resolve()
        self.source_manifest: dict = {
            "date": self.date_root.name,
            "feishu": {
                "baseUrl": config.get("feishu", {}).get("table_url") or config.get("feishu", {}).get("base_domain", ""),
                "tableId": config.get("feishu", {}).get("table_id", ""),
                "viewId": config.get("feishu", {}).get("view_id", ""),
            },
            "records": [],
            "skipped": [],
        }

        dd = config.get("dedup", {})
        self.dedup_enabled = bool(dd.get("enabled", False))
        self.dedup_threshold = float(dd.get("diff_threshold", 2.5))
        self.dedup_renumber = bool(dd.get("renumber", True))

        fp = config.get("framepacker", {})
        self.fps = int(fp.get("fps", 24))
        self.max_frames = int(fp.get("max_frames", 0) or 0)

    def _stopped(self) -> bool:
        return self.stop_event.is_set()

    def _infer_date_root(self) -> str:
        download = Path(self.download_dir)
        export = Path(self.export_dir)
        if download.name.lower() == "videos":
            return str(download.parent)
        if export.name.lower() == "frames":
            return str(export.parent)
        return str(download.parent)

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
        if self.routing_enabled:
            ensure_split_dirs(self.date_root)
        else:
            os.makedirs(self.download_dir, exist_ok=True)
            os.makedirs(self.export_dir, exist_ok=True)

        self.log("读取飞书表格记录 ...")
        records = self.client.list_records()
        self.log(f"共 {len(records)} 条记录")
        # 建立 record_id -> 记录 映射，用于沿「父记录」回溯角色层级路径
        self.rec_map = {r.get("record_id", ""): r for r in records}
        animation_records = [
            r for r in records
            if _attachments(r.get("fields", {}).get(self.f_animation))
        ]
        self.log(f"发现 {len(animation_records)} 条有动画附件的记录")

        processed = 0
        for rec in animation_records:
            if self._stopped():
                self.log("已停止。")
                break
            rid = rec.get("record_id", "")
            fields = rec.get("fields", {})
            progress = first_text(fields.get(self.f_progress))
            if progress in SKIP_PROGRESS:
                self._append_skipped(rid, fields, f"progress is {progress}")
                continue
            if not self._record_selected(rec):
                continue
            self._process_record(rid, fields)
            processed += 1

        self._write_source_manifest()
        self.log(f"完成。处理了 {processed} 条有动画附件的记录。")

    def _role_parts(self, rid: str) -> list[str]:
        """沿「父记录」自底向上回溯，返回可读层级，如 ["gary", "idle"]。"""
        parts = self._hierarchy_parts(rid)
        if len(parts) > 2:
            parts = parts[-2:]
        return [_safe_name(p) for p in parts] or [_safe_name(rid)]

    def _hierarchy_parts(self, rid: str) -> list[str]:
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
        return [_safe_name(p) for p in parts]

    def _record_selected(self, record: dict) -> bool:
        rid = record.get("record_id", "")
        fields = record.get("fields", {})
        parts = self._hierarchy_parts(rid)
        if self.selection_root and (not parts or parts[0] != _safe_name(self.selection_root)):
            return False
        if self.selection_emotions and (not parts or parts[-1] not in self.selection_emotions):
            return False
        types = set(_field_texts(fields.get(self.f_type)))
        if self.selection_types and not (types & self.selection_types):
            return False
        progress = set(_field_texts(fields.get(self.f_progress)))
        if self.progress_include and not (progress & self.progress_include):
            return False
        if self.progress_exclude and (progress & self.progress_exclude):
            return False
        return True

    def _record_identity(self, rid: str, fields: dict) -> dict:
        hierarchy = self._hierarchy_parts(rid)
        parts = hierarchy[-2:]
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
            "root": hierarchy[0] if hierarchy else "",
            "hierarchy": hierarchy,
            "rel_dir": rel_dir,
            "label": f"{role}/{emotion}" + (f"（{animation_name}）" if animation_name else ""),
        }

    def _write_manifest(self) -> None:
        path = os.path.join(self.export_dir, "_pipeline_manifest.json")
        os.makedirs(self.export_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"items": self.manifest_items}, f, ensure_ascii=False, indent=2)

    def _write_source_manifest(self) -> None:
        write_source_manifest(self.date_root, self.source_manifest)

    def _append_skipped(self, rid: str, fields: dict, reason: str) -> None:
        try:
            identity = self._record_identity(rid, fields)
            character = identity["role"]
            animation = identity["emotion"]
        except Exception:
            character = first_text(fields.get(self.f_role))
            animation = first_text(fields.get(self.f_animation_name))
        item = {
            "recordId": rid,
            "character": character,
            "animation": animation,
            "reason": reason,
        }
        self.source_manifest["skipped"].append(item)
        self.source_manifest["records"].append({**item, "skipped": True, "skipReason": reason})
        self._write_source_manifest()

    def _process_record(self, rid: str, fields: dict) -> None:
        identity = self._record_identity(rid, fields)
        role = identity["role"]
        emotion = identity["emotion"]
        rel_dir = identity["rel_dir"]
        asset_kind = classify_asset_kind(fields, fallback_texts=identity.get("hierarchy", []))
        process_variant = classify_process_variant({**fields, "处理选项": fields.get(self.f_process_option)})
        key = task_key(role, emotion)
        types = unity_types(fields, asset_kind)
        self.log(f"——— 处理记录 {rid}（{identity['label']}）———")
        videos = video_attachments(fields.get(self.f_animation))
        if not videos:
            self.log(f"记录 {rid} 没有可下载的「{self.f_animation}」附件，跳过。")
            return

        # 1) 下载视频（目录 = 角色/情绪，文件名 = 角色_情绪.ext）
        #    下载前先清空该记录的旧视频文件，确保每次都是全新内容
        if self.routing_enabled:
            rec_dir_path = routed_stage_dir(self.date_root, asset_kind, process_variant, "videos", key)
            frame_root = routed_stage_dir(self.date_root, asset_kind, process_variant, "frames")
            out_rel_dir = key
        else:
            rec_dir_path = Path(self.download_dir) / rel_dir
            frame_root = Path(self.export_dir)
            out_rel_dir = rel_dir
        rec_dir = str(rec_dir_path)
        self._clear_dir(rec_dir)
        downloaded = []
        manifest_attachments = []
        for index, att in enumerate(videos):
            if self._stopped():
                return
            raw_name = str(att.get("name") or "")
            suffix = Path(raw_name).suffix or ".mp4"
            stem = "source" if index == 0 else f"source_{index + 1:02d}"
            save_name = stem + suffix
            self.log(f"下载视频：{identity['label']} -> {os.path.join(asset_kind, process_variant, 'videos', key, save_name)}")
            path = self.client.download_attachment(
                att, rec_dir, field_name=self.f_animation, record_id=rid, save_name=save_name)
            downloaded.append(path)
            manifest_attachments.append(
                {
                    "name": raw_name,
                    "localPath": str(Path(path).relative_to(self.date_root)).replace("\\", "/") if self.routing_enabled else path,
                }
            )

        source_record = {
            "recordId": rid,
            "character": role,
            "animation": emotion,
            "displayName": identity["animation_name"],
            "assetKind": asset_kind,
            "unityCategory": types[0] if types else "",
            "progress": first_text(fields.get(self.f_progress)),
            "processOption": first_text(fields.get(self.f_process_option)),
            "processVariant": process_variant,
            "types": types,
            "attachments": manifest_attachments,
            "taskKey": key,
            "skipped": False,
            "skipReason": "",
        }
        self.source_manifest["records"].append(source_record)
        self._write_source_manifest()

        # 2) 抽帧 + 导出 PNG 序列帧（导出子目录同样用角色层级路径）
        extractor = LocalFrameExtractor(
            export_dir=str(frame_root), fps=self.fps, max_frames=self.max_frames, logger=self.log)
        for i, video in enumerate(downloaded):
            if self._stopped():
                return
            # 同一记录有多个视频时追加序号，避免目录互相覆盖
            out_subdir = out_rel_dir if len(downloaded) == 1 else os.path.join(out_rel_dir, f"video_{i + 1}")
            # 抽帧前清空导出目录的旧序列帧，避免新旧帧混在一起干扰去重
            self._clear_dir(str(frame_root / out_subdir))
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
                "asset_kind": asset_kind,
                "process_variant": process_variant,
                "task_key": key,
                "video_path": video,
                "frame_dir": dest,
                "frame_rel_dir": out_subdir.replace("\\", "/"),
                "frame_count": len(frames),
                "frames": frames,
            })
            if self.routing_enabled:
                pipeline_manifest = frame_root / "_pipeline_manifest.json"
                pipeline_manifest.write_text(json.dumps({"items": self.manifest_items}, ensure_ascii=False, indent=2), encoding="utf-8")
            else:
                self._write_manifest()
            self.log(f"抽帧记录：{identity['label']} -> {len(frames)} 帧")

        self.log(f"记录完成：{identity['label']}")
