from __future__ import annotations

from typing import Any


def format_skill_results(results: list[dict[str, Any]], max_items: int = 8) -> str:
    if not results:
        return "没有执行操作。"

    lines: list[str] = []
    for item in results:
        skill = item.get("skill", "skill")
        if item.get("needs_confirmation"):
            lines.append(item.get("message") or f"{skill} 需要确认后执行。")
            continue
        if not item.get("ok"):
            lines.append(f"{skill} 失败：{item.get('error', '未知错误')}")
            continue

        payload = item.get("result") or {}
        if skill.startswith("bot."):
            lines.append(str(payload.get("text") or "完成。"))
        elif skill in {"file.list_allowed", "image.list", "file.list_by_type"}:
            lines.extend(_format_listing(payload, max_items=max_items))
        elif skill == "file.exists":
            status = "存在" if payload.get("exists") else "不存在"
            lines.append(f"{payload.get('path')}：{status}")
        elif skill in {"file.copy", "file.copy_as", "file.duplicate_same_dir"}:
            lines.append(f"已复制：{payload.get('dst_path')}")
        elif skill == "file.copy_many":
            lines.append(f"已批量复制 {payload.get('count', 0)} 个文件。")
        elif skill == "file.move":
            lines.append(f"已移动：{payload.get('dst_path')}")
        elif skill == "file.move_many":
            lines.append(f"已批量移动 {payload.get('count', 0)} 个文件。")
        elif skill == "file.mkdir":
            lines.append(f"已创建目录：{payload.get('path')}")
        elif skill == "file.mkdir_many":
            lines.append(f"已创建 {payload.get('count', 0)} 个目录。")
        elif skill == "workspace.roots":
            roots = payload.get("roots", [])
            lines.append("允许访问：" + "、".join(item.get("path", "") for item in roots))
        elif skill == "workspace.disk_usage":
            lines.append("磁盘空间：")
            for item in payload.get("items", [])[:10]:
                if not item.get("exists"):
                    lines.append(f"{item.get('path')}：不存在")
                    continue
                free_gb = (item.get("free", 0) or 0) / (1024 ** 3)
                total_gb = (item.get("total", 0) or 0) / (1024 ** 3)
                lines.append(f"{item.get('path')}：可用 {free_gb:.1f} GB / 共 {total_gb:.1f} GB")
        elif skill == "file.info":
            size = payload.get("size")
            size_text = f"，{size} bytes" if size is not None else ""
            lines.append(f"{payload.get('path')}：{'文件夹' if payload.get('is_dir') else '文件'}{size_text}")
        elif skill == "file.read_text":
            lines.append(f"{payload.get('path')}：")
            lines.append(str(payload.get("text", "")))
        elif skill == "file.write_text":
            lines.append(f"已写入：{payload.get('path')}")
        elif skill == "file.append_text":
            lines.append(f"已追加：{payload.get('path')}")
        elif skill == "file.hash":
            lines.append(f"{payload.get('algorithm')}：{payload.get('hash')}")
        elif skill == "file.batch_info":
            lines.append(f"已检查 {payload.get('count', 0)} 个路径。")
        elif skill == "file.copy_tree":
            lines.append(f"已复制目录：{payload.get('dst_path')}（{payload.get('files', 0)} 个文件）")
        elif skill == "file.rename_many":
            lines.append(f"已批量重命名 {payload.get('count', 0)} 个文件。")
        elif skill == "file.rename_sequence":
            lines.append(f"已按顺序重命名 {payload.get('count', 0)} 个文件：")
            for item in payload.get("items", [])[:max_items]:
                lines.append(f"{_path_name(item.get('src_path'))} -> {_path_name(item.get('dst_path'))}")
        elif skill == "file.unzip":
            lines.append(f"已解压 {payload.get('count', 0)} 个文件到：{payload.get('dst_dir')}")
        elif skill == "file.delete":
            lines.append(f"已删除：{payload.get('path')}")
        elif skill == "file.empty_dir":
            lines.append(f"已清空：{payload.get('path')}（{payload.get('removed', 0)} 项）")
        elif skill == "image.info":
            lines.append(
                f"{payload.get('name')}：{payload.get('width')}x{payload.get('height')}，"
                f"{payload.get('format') or payload.get('suffix')}"
            )
        elif skill == "image.batch_info":
            lines.append(f"已读取 {payload.get('count', 0)} 张图片信息。")
            for item in payload.get("items", [])[:max_items]:
                lines.append(f"{item.get('name')}：{item.get('width')}x{item.get('height')}")
        elif skill == "image.convert_format":
            lines.append(f"已转换图片：{payload.get('dst_path')}")
        elif skill == "image.resize":
            lines.append(f"已缩放图片：{payload.get('dst_path')} ({payload.get('width')}x{payload.get('height')})")
        elif skill == "file.zip_paths":
            lines.append(f"已打包 {payload.get('count', 0)} 个文件：{payload.get('zip_path')}")
        elif skill in {"feishu.send_file", "feishu.send_file_by_name"}:
            lines.append(f"已发送文件：{payload.get('file_name')}")
        elif skill.startswith("matting."):
            batch_id = payload.get("batch_id")
            status = payload.get("status")
            if batch_id and status:
                lines.append(f"批次 {batch_id}：{status}")
            elif batch_id:
                lines.append(f"已创建批次：{batch_id}")
            else:
                lines.append(f"{skill} 完成。")
        elif skill == "comfyui.status":
            lines.extend(_format_comfyui_status(payload))
        elif skill == "system.gpu_status":
            lines.extend(_format_gpu_status(payload))
        elif skill == "system.process_status":
            lines.append(f"匹配到 {payload.get('count', 0)} 个进程：")
            for proc in payload.get("items", [])[:max_items]:
                lines.append(
                    f"PID {proc.get('pid')} {proc.get('name')} "
                    f"{proc.get('status')} {proc.get('memory_mb')} MB"
                )
        else:
            lines.append(f"{skill} 完成。")

    return "\n".join(line for line in lines if line).strip() or "完成。"


def _format_listing(payload: dict[str, Any], max_items: int) -> list[str]:
    items = payload.get("items") or []
    root = payload.get("path") or payload.get("root") or "目录"
    count = payload.get("count", len(items))
    total = count if count is not None else len(items)
    lines = [f"{root}：{total} 项"]
    for entry in items[:max_items]:
        name = entry.get("name") or entry.get("path")
        if not name:
            continue
        if entry.get("is_dir"):
            lines.append(f"- {name}\\")
        else:
            size = entry.get("size")
            size_text = f" ({_format_size(size)})" if isinstance(size, int) else ""
            lines.append(f"- {name}{size_text}")
    if payload.get("truncated") or len(items) > max_items:
        shown = min(max_items, len(items))
        lines.append(f"还有更多，先显示前 {shown} 项。")
    return lines


def _path_name(path: str | None) -> str:
    if not path:
        return ""
    return path.replace("/", "\\").rstrip("\\").split("\\")[-1]


def _format_size(size: int) -> str:
    units = ("B", "KB", "MB", "GB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def _format_comfyui_status(payload: dict[str, Any]) -> list[str]:
    lines = [
        "ComfyUI 状态：",
        f"模式：{'fake mode（不使用 GPU）' if payload.get('fake_mode') else 'real mode'}",
        f"URL：{payload.get('url')}",
        f"工作流：{'存在' if payload.get('workflow_exists') else '不存在'} ({payload.get('workflow_path')})",
    ]
    if payload.get("fake_mode"):
        return lines
    if payload.get("reachable"):
        lines.append("连接：正常")
    else:
        lines.append(f"连接：失败（{payload.get('error', '未知错误')}）")
    return lines


def _format_gpu_status(payload: dict[str, Any]) -> list[str]:
    if not payload.get("available"):
        return [f"GPU 状态：不可用（{payload.get('error', '没有检测到 GPU')}）"]
    lines = ["GPU 状态："]
    for gpu in payload.get("gpus", []):
        used = gpu.get("memory_used_mb")
        total = gpu.get("memory_total_mb")
        util = gpu.get("utilization_gpu_percent")
        temp = gpu.get("temperature_c")
        power = gpu.get("power_draw_w")
        lines.append(
            f"GPU {gpu.get('index')} {gpu.get('name')}：显存 {used}/{total} MB，"
            f"利用率 {util}%，温度 {temp}°C，功耗 {power} W"
        )
    return lines
