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
        elif skill == "file.search_text":
            lines.append(f"找到 {payload.get('count', 0)} 处匹配：")
            for entry in payload.get("items", [])[:max_items]:
                lines.append(f"- {_path_name(entry.get('path'))}:{entry.get('line')} {entry.get('snippet')}")
        elif skill == "file.preview":
            if payload.get("kind") == "text":
                lines.append(str(payload.get("preview") or ""))
            else:
                lines.append(f"{_path_name(payload.get('path'))}：二进制文件，大小 {payload.get('size')} bytes")
                lines.append(str(payload.get("hex") or ""))
        elif skill == "file.count":
            lines.append(
                f"{payload.get('path')}：文件 {payload.get('files', 0)}，目录 {payload.get('dirs', 0)}，"
                f"图片 {payload.get('images', 0)}，视频 {payload.get('videos', 0)}，表格 {payload.get('tables', 0)}，"
                f"压缩包 {payload.get('archives', 0)}"
            )
        elif skill == "file.manifest":
            lines.append(f"已导出文件清单：{payload.get('output_path')}（{payload.get('count', 0)} 项）")
        elif skill == "archive.list":
            lines.append(f"{_path_name(payload.get('path'))}：{payload.get('total', 0)} 项")
            for entry in payload.get("items", [])[:max_items]:
                suffix = "\\" if entry.get("is_dir") else ""
                lines.append(f"- {entry.get('name')}{suffix}")
        elif skill == "json.query":
            value = payload.get("value")
            text = value if isinstance(value, str) else __import__("json").dumps(value, ensure_ascii=False, indent=2)
            lines.append(text[:4000])
        elif skill == "csv.summary":
            lines.append("列：" + "、".join(payload.get("columns") or []))
            for row in payload.get("sample_rows", [])[:max_items]:
                lines.append(str(row))
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
        elif skill == "feishu.zip_and_send":
            lines.append(f"已打包并发送：{payload.get('file_name')}（{payload.get('count', 0)} 个文件）")
        elif skill in {"feishu.send_file", "feishu.send_file_by_name"}:
            lines.append(f"已发送文件：{payload.get('file_name')}")
        elif skill in {"feishu.send_image", "feishu.send_image_by_name"}:
            lines.append(f"已发送图片：{payload.get('file_name')}")
        elif skill in {"translate.text", "translate.image_text"}:
            lines.append(str(payload.get("translation") or "翻译完成。"))
        elif skill == "image.ocr":
            lines.append(str(payload.get("text") or "没有识别到文字。"))
        elif skill.startswith("matting."):
            if skill.startswith("matting.shared_"):
                lines.extend(_format_shared_matting(payload))
            else:
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
        elif skill == "comfyui.workflows":
            lines.append(f"ComfyUI 工作流：{payload.get('count', 0)} 个")
            for item in payload.get("items", [])[:max_items]:
                brief = ""
                if item.get("load_image_count") is not None:
                    brief = f"（输入 {item.get('load_image_count')}，输出 {item.get('save_image_count')}）"
                lines.append(f"- {item.get('name')}{brief}")
            if payload.get("items"):
                lines.append("下一步可以说：选择其中一个工作流，然后告诉我输入路径。")
        elif skill in {"comfyui.workflow_info", "comfyui.workflow_select"}:
            lines.extend(_format_workflow_info(payload, max_items))
        elif skill == "comfyui.run_preview":
            lines.extend(_format_comfyui_run_preview(payload, max_items))
        elif skill == "comfyui.queue_status":
            lines.append(f"ComfyUI 队列：运行 {len(payload.get('running') or [])}，等待 {len(payload.get('pending') or [])}")
        elif skill == "comfyui.run_start":
            lines.append(f"ComfyUI 批量任务已启动：{payload.get('run_id')}")
            lines.append(f"输入：{payload.get('input_dir')}")
            lines.append(f"输出：{payload.get('output_dir')}")
            lines.append(f"总数：{payload.get('total')} 张，同结构输出：{'是' if payload.get('preserve_structure') else '否'}")
        elif skill == "comfyui.run_status":
            lines.extend(_format_comfyui_run_status(payload))
        elif skill == "comfyui.run_list":
            lines.extend(_format_comfyui_run_list(payload, max_items))
        elif skill == "comfyui.run_update":
            lines.append(f"已更新任务：{payload.get('run_id')}")
            lines.append(f"工作流：{_path_name(payload.get('workflow_path'))}")
            lines.append(f"输入：{payload.get('input_dir')}")
            lines.append(f"输出：{payload.get('output_dir')}")
            lines.append(f"图片：{payload.get('total')} 张")
        elif skill in {"comfyui.run_pause", "comfyui.run_resume", "comfyui.run_cancel", "comfyui.run_delete"}:
            lines.append(f"{payload.get('run_id')}：{payload.get('status')}")
            if payload.get("message"):
                lines.append(str(payload.get("message")))
        elif skill == "comfyui.run_sync_outputs":
            lines.append(f"已同步输出：{payload.get('count', 0)} 个文件")
            lines.append(str(payload.get("output_dir") or ""))
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
        f"Aki：{'存在' if payload.get('aki_root_exists') else '不存在'} ({payload.get('aki_root')})",
        f"ComfyUI：{'存在' if payload.get('comfyui_dir_exists') else '不存在'} ({payload.get('comfyui_dir')})",
        f"版本：{payload.get('comfyui_version') or '未读到'}",
        f"工作流：{'存在' if payload.get('workflow_exists') else '不存在'} ({payload.get('workflow_path')})",
    ]
    if payload.get("fake_mode"):
        return lines
    if payload.get("reachable"):
        lines.append("连接：正常")
    else:
        lines.append(f"连接：失败（{payload.get('error', '未知错误')}）")
    processes = payload.get("processes") or {}
    if isinstance(processes, dict) and processes.get("count") is not None:
        lines.append(f"相关进程：{processes.get('count')} 个")
    return lines


def _format_workflow_info(payload: dict[str, Any], max_items: int) -> list[str]:
    lines = [
        "工作流：",
        f"路径：{payload.get('path')}",
        f"节点：{payload.get('node_count', 0)}",
        f"LoadImage：{len(payload.get('load_image_nodes') or [])} 个",
        f"SaveImage：{len(payload.get('save_image_nodes') or [])} 个",
    ]
    load_nodes = payload.get("load_image_nodes") or []
    if load_nodes:
        lines.append("输入节点：")
        for node in load_nodes[:max_items]:
            inputs = ",".join(node.get("inputs") or [])
            lines.append(f"- {node.get('id')} {node.get('class_type')} ({inputs})")
    save_nodes = payload.get("save_image_nodes") or []
    if save_nodes:
        lines.append("输出节点：")
        for node in save_nodes[:max_items]:
            inputs = ",".join(node.get("inputs") or [])
            lines.append(f"- {node.get('id')} {node.get('class_type')} ({inputs})")
    counts = payload.get("class_counts") or {}
    if counts:
        top = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:5]
        lines.append("主要节点：" + "、".join(f"{name}x{count}" for name, count in top))
    return lines


def _format_comfyui_run_preview(payload: dict[str, Any], max_items: int) -> list[str]:
    lines = [
        "抠图任务预览：",
        f"工作流：{payload.get('workflow_name') or payload.get('workflow_path')}",
        f"输入：{payload.get('input_dir')}",
        f"输出：{payload.get('output_dir')}",
        f"图片：{payload.get('total', 0)} 张",
        f"节点：{payload.get('node_count', 0)} 个，LoadImage {len(payload.get('load_image_nodes') or [])} 个，SaveImage {len(payload.get('save_image_nodes') or [])} 个",
    ]
    samples = payload.get("sample_inputs") or []
    if samples:
        lines.append("示例：")
        for item in samples[:max_items]:
            lines.append(f"- {item}")
    return lines


def _format_comfyui_run_status(payload: dict[str, Any]) -> list[str]:
    lines = [
        f"ComfyUI 管线：{payload.get('run_id')}",
        f"状态：{payload.get('status')}",
        f"进度：{payload.get('completed', 0)}/{payload.get('total', 0)} ({payload.get('progress_percent', 0)}%)",
        f"失败：{payload.get('failed', 0)}，等待/运行：{payload.get('running_or_pending', 0)}",
        f"输入：{payload.get('input_dir')}",
        f"输出：{payload.get('output_dir')}",
    ]
    if payload.get("last_completed"):
        lines.append(f"刚完成：{payload.get('last_completed')}")
    eta = payload.get("eta_seconds")
    lines.append(f"预计剩余：{_format_duration(eta) if isinstance(eta, int) else '暂无法估算'}")
    lines.append(f"ComfyUI 队列：运行 {payload.get('queue_running', 0)}，等待 {payload.get('queue_pending', 0)}")
    gpu = payload.get("gpu") or {}
    if gpu:
        lines.extend(_format_gpu_status(gpu))
    return lines


def _format_comfyui_run_list(payload: dict[str, Any], max_items: int) -> list[str]:
    items = payload.get("items") or []
    if not items:
        return ["当前没有 ComfyUI 任务。"]
    lines = [f"ComfyUI 任务：{payload.get('count', len(items))} 个"]
    for item in items[:max_items]:
        lines.append(
            f"- {item.get('run_id')} {item.get('status')} "
            f"{item.get('completed', 0)}/{item.get('total', 0)} "
            f"{item.get('workflow_name')}"
        )
        lines.append(f"  输入：{item.get('input_dir')}")
        lines.append(f"  输出：{item.get('output_dir')}")
    return lines


def _format_shared_matting(payload: dict[str, Any]) -> list[str]:
    if payload.get("error") and not payload.get("run_id"):
        return [f"共享盘抠图失败：{payload.get('error')}"]
    lines = [
        f"共享盘抠图：{payload.get('run_id')}",
        f"状态：{payload.get('status')}",
        f"共享输入：{payload.get('shared_input_dir')}",
        f"共享输出：{payload.get('shared_output_dir')}",
    ]
    if payload.get("local_input_dir"):
        lines.append(f"本地工作区：{payload.get('local_input_dir')}")
    if payload.get("total") is not None:
        lines.append(f"总数：{payload.get('total')} 张")
    if payload.get("synced_out") is not None:
        lines.append(f"已同步输出：{payload.get('synced_out')} 个")
    comfy = payload.get("comfyui") or {}
    if comfy:
        lines.append(f"ComfyUI：{comfy.get('completed', 0)}/{comfy.get('total', 0)} ({comfy.get('progress_percent', 0)}%)")
        eta = comfy.get("eta_seconds")
        lines.append(f"预计剩余：{_format_duration(eta) if isinstance(eta, int) else '暂无法估算'}")
        gpu = comfy.get("gpu") or {}
        if gpu:
            lines.extend(_format_gpu_status(gpu))
    if payload.get("error"):
        lines.append(f"错误：{payload.get('error')}")
    return lines


def _format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


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
