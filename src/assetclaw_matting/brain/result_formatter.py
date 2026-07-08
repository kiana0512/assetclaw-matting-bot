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
        elif skill == "agent.current_work":
            lines.extend(_format_agent_current_work(payload))
        elif skill == "agent.diagnose":
            lines.extend(_format_agent_diagnose(payload))
        elif skill == "sticker.info":
            lines.extend(_format_sticker_info(payload))
        elif skill == "sticker.send_random":
            lines.append("给你贴一个，先别让这些流程把你榨干。" if payload.get("sent") else "我想贴一个，但现在没选到可用表情包。")
        elif skill == "emotion.respond":
            lines.append(str(payload.get("text") or "我听到了。"))
        elif skill == "life.location":
            if payload.get("location"):
                source = "我记住的" if payload.get("source") == "memory" else "默认配置"
                lines.append(f"我现在按{source}位置理解：{payload.get('location')}。")
            else:
                lines.append(str(payload.get("error") or "我还不知道你的位置。"))
        elif skill == "life.set_location":
            lines.append(f"记住了，你现在在 {payload.get('location')}。后面问天气和外卖我会先按这里来。")
        elif skill == "life.weather":
            lines.extend(_format_life_weather(payload))
        elif skill == "life.food_suggest":
            lines.extend(_format_life_food(payload))
        elif skill == "web.fetch_url":
            lines.extend(_format_web_fetch(payload))
        elif skill == "web.search":
            lines.extend(_format_web_search(payload, max_items))
        elif skill == "web.research":
            lines.extend(_format_web_research(payload, max_items))
        elif skill.startswith("p4."):
            lines.extend(_format_p4(payload, skill, max_items))
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
        elif skill == "text.process":
            lines.append(str(payload.get("display_text") or payload.get("result") or "处理完成。"))
        elif skill in {"translate.text", "translate.image_text"}:
            lines.append(str(payload.get("display_text") or payload.get("translation") or "翻译完成。"))
        elif skill == "speech.transcribe":
            lines.append("语音识别：" + str(payload.get("text") or "").strip())
        elif skill == "speech.synthesize":
            lines.append(f"已生成语音：{payload.get('output_path')}")
        elif skill == "speech.send_tts":
            lines.append(f"已发送语音：{payload.get('file_name')}")
        elif skill in {"image.ocr", "image.describe"}:
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
        elif skill.startswith("matting_pipeline."):
            lines.extend(_format_matting_pipeline(skill, payload, max_items))
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
            if payload.get("pipeline_notice"):
                lines.append(str(payload.get("pipeline_notice")))
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
        elif skill == "cherry.info":
            lines.append("Cherry 帧序列工具：")
            lines.append(f"路径：{payload.get('source_path')}")
            lines.append(f"状态：{'可用' if payload.get('exists') else '未找到'}")
            lines.append("处理：" + "、".join(payload.get("steps") or []))
        elif skill == "cherry.run_preview":
            lines.extend(_format_cherry_run_preview(payload, max_items))
        elif skill == "cherry.run_start":
            lines.append(f"Cherry 任务已启动：{payload.get('run_id')}")
            lines.append(f"输入：{payload.get('input_dir')}")
            lines.append(f"输出：{payload.get('output_dir')}")
            lines.append(f"总数：{payload.get('total')} 张，序列：{payload.get('sequence_count')} 组")
        elif skill == "cherry.run_status":
            lines.extend(_format_cherry_run_status(payload))
        elif skill == "cherry.run_list":
            lines.extend(_format_cherry_run_list(payload, max_items))
        elif skill in {"cherry.run_cancel", "cherry.run_delete"}:
            lines.append(f"{payload.get('run_id')}：{payload.get('status')}")
        elif skill == "frame.info":
            lines.append("飞书抽帧工具：")
            lines.append(f"路径：{payload.get('tool_dir')}")
            lines.append(f"状态：{'可用' if payload.get('exists') else '未找到'}")
            lines.append(f"fps：{payload.get('fps')}，最多帧数：{payload.get('max_frames') or '不限'}，相似阈值：{payload.get('diff_threshold')}")
            lines.append(f"下载：{payload.get('download_dir')}")
            lines.append(f"导出：{payload.get('export_dir')}")
            lines.append("范围：所有带动画视频附件的记录")
        elif skill == "frame.run_preview":
            lines.append("抽帧任务预览：")
            if payload.get("workspace_root"):
                lines.append(f"工作区：{payload.get('workspace_root')}")
            lines.append(f"下载目录：{payload.get('download_dir')}")
            lines.append(f"抽帧输出：{payload.get('export_dir')}")
            lines.append(
                f"fps：{payload.get('fps')}，最多帧数：{payload.get('max_frames') or '不限'}，"
                f"剔除关键帧：{'开' if payload.get('dedup_enabled') else '关'}，相似阈值：{payload.get('diff_threshold')}"
            )
            if payload.get("selection_root") or payload.get("selection_emotions"):
                lines.append(f"筛选：{payload.get('selection_root') or '全部'}/{'、'.join(payload.get('selection_emotions') or ['全部'])}")
            lines.append("范围：所有带动画视频附件的记录")
        elif skill == "frame.run_start":
            lines.append(f"抽帧任务已启动：{payload.get('run_id')}")
            lines.append(f"下载：{payload.get('download_dir')}")
            lines.append(f"导出：{payload.get('export_dir')}")
        elif skill == "frame.run_status":
            lines.extend(_format_frame_status(payload))
        elif skill == "frame.run_list":
            lines.extend(_format_frame_list(payload, max_items))
        elif skill == "frame.run_cancel":
            lines.append(f"{payload.get('run_id')}：{payload.get('status')}")
        elif skill == "pipeline.run_preview":
            lines.extend(_format_pipeline_preview(payload))
        elif skill == "pipeline.run_start":
            lines.append(f"动画自动化流程已启动：{payload.get('run_id')}")
            lines.append("步骤：抽帧 -> ComfyUI 抠图 -> Cherry 平滑")
            if payload.get("workspace_root"):
                lines.append(f"工作区：{payload.get('workspace_root')}")
            lines.append(f"最终输出：{payload.get('smooth_output_dir')}")
        elif skill == "pipeline.run_status":
            lines.extend(_format_pipeline_status(payload))
        elif skill == "pipeline.run_list":
            lines.extend(_format_pipeline_list(payload, max_items))
        elif skill == "pipeline.run_cancel":
            lines.append(f"{payload.get('run_id')}：{payload.get('status')}")
        elif skill.startswith("unity_import."):
            lines.extend(_format_unity_import(payload))
        elif skill.startswith("unity_tools."):
            lines.extend(_format_unity_tools(skill, payload))
        elif skill == "animation_flow.preview":
            lines.extend(_format_animation_flow(payload, preview=True))
        elif skill == "animation_flow.start":
            lines.extend(_format_animation_flow(payload))
        elif skill == "animation_flow.status":
            lines.extend(_format_animation_flow(payload))
        elif skill == "animation_flow.resume":
            lines.extend(_format_animation_flow(payload))
            if payload.get("message"):
                lines.append(str(payload.get("message")))
        elif skill == "animation_flow.list":
            items = payload.get("items") or []
            lines.append(f"完整动画流程任务：{payload.get('count', len(items))} 个")
            for entry in items[:max_items]:
                lines.append(f"- {entry.get('run_id')}：{entry.get('status')} / {entry.get('current_stage')}")
        elif skill == "animation_flow.cancel":
            lines.append(f"{payload.get('run_id')}：{payload.get('status')}")
        elif skill.startswith("direct_video."):
            lines.extend(_format_direct_video(skill, payload, max_items))
        elif skill.startswith("direct_image."):
            lines.extend(_format_direct_image(skill, payload, max_items))
        elif skill == "animation.status":
            lines.extend(_format_animation_status(payload))
        elif skill == "animation.manual_smooth_current":
            cherry = payload.get("cherry") or {}
            lines.append(f"动画平滑已启动：{cherry.get('run_id')}")
            lines.append(f"输入：{payload.get('input_dir')}")
            lines.append(f"输出：{payload.get('output_dir')}")
            lines.append(f"总数：{cherry.get('total', 0)} 张，序列：{cherry.get('sequence_count', 0)} 组")
        elif skill == "animation.rerun_from_videos":
            lines.append("动画全量重跑已在后台启动。")
            lines.append(f"工作区：{payload.get('root')}")
            lines.append(f"PID：{payload.get('pid')}")
            lines.append(f"日志：{payload.get('log_path')}")
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
    detail = payload.get("last_completed_detail") or {}
    if detail:
        lines.append(f"刚完成明细：{detail.get('role')}/{detail.get('emotion')}/{detail.get('frame')}")
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


def _format_cherry_run_preview(payload: dict[str, Any], max_items: int) -> list[str]:
    options = payload.get("options") or {}
    steps = []
    if options.get("use_denoise"):
        steps.append(f"去噪 阈值{options.get('denoise_threshold')} 半径{options.get('denoise_radius')}")
    if options.get("use_blur"):
        steps.append(f"模糊白叠加 半径{options.get('blur_radius')} 强度{options.get('blur_sigma')}")
    if options.get("use_resize1"):
        steps.append(f"缩小① {options.get('resize1_width')}x{options.get('resize1_height')}")
    if options.get("use_sharp1"):
        steps.append(f"锐化① 强度{options.get('sharp1_amount')}")
    if options.get("use_resize2"):
        steps.append(f"缩小② {options.get('resize2_width')}x{options.get('resize2_height')}")
    if options.get("use_sharp2"):
        steps.append(f"锐化② 强度{options.get('sharp2_amount')}")
    if options.get("use_smooth"):
        steps.append(f"时序平滑 窗口{options.get('smooth_window')} 强度{options.get('smooth_sigma')}")
    lines = [
        "Cherry 任务预览：",
        f"输入：{payload.get('input_dir')}",
        f"输出：{payload.get('output_dir')}",
        f"图片：{payload.get('total', 0)} 张，序列：{payload.get('sequence_count', 0)} 组",
        "处理：" + ("、".join(steps) if steps else "无"),
    ]
    samples = payload.get("sample_inputs") or []
    if samples:
        lines.append("示例：")
        for item in samples[:max_items]:
            lines.append(f"- {item}")
    return lines


def _format_cherry_run_status(payload: dict[str, Any]) -> list[str]:
    lines = [
        f"Cherry 任务：{payload.get('run_id')}",
        f"状态：{payload.get('status')}",
        f"进度：{payload.get('completed', 0)}/{payload.get('total', 0)} ({payload.get('progress_percent', 0)}%)",
        f"失败：{payload.get('failed', 0)}，等待/运行：{payload.get('running_or_pending', 0)}",
        f"输入：{payload.get('input_dir')}",
        f"输出：{payload.get('output_dir')}",
    ]
    if payload.get("last_completed"):
        lines.append(f"刚完成：{payload.get('last_completed')}")
    detail = payload.get("last_completed_detail") or {}
    if detail:
        lines.append(f"刚完成明细：{detail.get('role')}/{detail.get('emotion')}/{detail.get('frame')}")
    eta = payload.get("eta_seconds")
    lines.append(f"预计剩余：{_format_duration(eta) if isinstance(eta, int) else '暂无法估算'}")
    if payload.get("error"):
        lines.append(f"错误：{payload.get('error')}")
    gpu = payload.get("gpu") or {}
    if gpu:
        lines.extend(_format_gpu_status(gpu))
    return lines


def _format_cherry_run_list(payload: dict[str, Any], max_items: int) -> list[str]:
    items = payload.get("items") or []
    if not items:
        return ["当前没有 Cherry 任务。"]
    lines = [f"Cherry 任务：{payload.get('count', len(items))} 个"]
    for item in items[:max_items]:
        lines.append(
            f"- {item.get('run_id')} {item.get('status')} "
            f"{item.get('completed', 0)}/{item.get('total', 0)}"
        )
        lines.append(f"  输入：{item.get('input_dir')}")
        lines.append(f"  输出：{item.get('output_dir')}")
    return lines


def _format_frame_status(payload: dict[str, Any]) -> list[str]:
    lines = [
        f"抽帧任务：{payload.get('run_id')}",
        f"状态：{payload.get('status')}",
        f"记录：{payload.get('processed_records', 0)}/{payload.get('total_records', 0)} ({payload.get('progress_percent', 0)}%)",
        f"下载：{payload.get('download_dir')}",
        f"导出：{payload.get('export_dir')}",
    ]
    if payload.get("error"):
        lines.append(f"错误：{payload.get('error')}")
    current = payload.get("current_item") or {}
    if current:
        lines.append(f"当前记录：{current.get('role')}/{current.get('emotion')}")
    if payload.get("last_log"):
        lines.append(f"最近日志：{payload.get('last_log')}")
    if payload.get("manifest_count"):
        lines.append(f"已登记视频：{payload.get('manifest_count')} 条")
    return lines


def _format_frame_list(payload: dict[str, Any], max_items: int) -> list[str]:
    items = payload.get("items") or []
    if not items:
        return ["当前没有抽帧任务。"]
    lines = [f"抽帧任务：{payload.get('count', len(items))} 个"]
    for item in items[:max_items]:
        lines.append(f"- {item.get('run_id')} {item.get('status')} {item.get('processed_records', 0)}/{item.get('total_records', 0)}")
        lines.append(f"  导出：{item.get('export_dir')}")
    return lines


def _format_pipeline_preview(payload: dict[str, Any]) -> list[str]:
    lines = [
        "动画自动化流程预览：",
        "步骤：抽帧 -> ComfyUI 抠图 -> Cherry 平滑",
    ]
    if payload.get("workspace_root"):
        lines.append(f"工作区：{payload.get('workspace_root')}")
    lines.extend([
        f"视频下载：{payload.get('input_dir')}",
        f"抽帧输出：{payload.get('frame_output_dir')}",
        f"抠图输出：{payload.get('matte_output_dir')}",
        f"平滑输出：{payload.get('smooth_output_dir')}",
    ])
    frame = payload.get("frame") or {}
    if frame:
        lines.append(
            f"抽帧：{frame.get('fps')}fps，最多帧数 {frame.get('max_frames') or '不限'}，"
            f"剔除关键帧 {'开' if frame.get('dedup_enabled') else '关'}"
        )
        if frame.get("selection_root") or frame.get("selection_emotions"):
            lines.append(f"筛选：{frame.get('selection_root') or '全部'}/{'、'.join(frame.get('selection_emotions') or ['全部'])}")
    cherry = payload.get("cherry") or {}
    if cherry:
        lines.append(
            f"后处理：{cherry.get('resize_width')}x{cherry.get('resize_height')}，"
            f"时序平滑 {'开' if cherry.get('use_smooth') else '关'}"
        )
    return lines


def _format_pipeline_status(payload: dict[str, Any]) -> list[str]:
    lines = [
        f"动画自动化流程：{payload.get('run_id')}",
        f"状态：{payload.get('status')}，当前步骤：{payload.get('current_step')}",
        f"工作区：{payload.get('workspace_root')}" if payload.get("workspace_root") else "",
        f"抽帧：{payload.get('frame_run_id') or '未开始'}",
        f"抠图：{payload.get('comfyui_run_id') or '未开始'}",
        f"平滑：{payload.get('cherry_run_id') or '未开始'}",
    ]
    frame = payload.get("frame") or {}
    if frame:
        lines.append(f"抽帧进度：{frame.get('processed_records', 0)}/{frame.get('total_records', 0)} {frame.get('status')}")
        current = frame.get("current_item") or {}
        if current:
            lines.append(f"抽帧当前：{current.get('role')}/{current.get('emotion')}")
    comfy = payload.get("comfyui") or {}
    if comfy:
        lines.append(f"抠图进度：{comfy.get('completed', 0)}/{comfy.get('total', 0)} {comfy.get('status')}")
        detail = comfy.get("last_completed_detail") or {}
        if detail:
            lines.append(f"抠图刚完成：{detail.get('role')}/{detail.get('emotion')}/{detail.get('frame')}")
    cherry = payload.get("cherry") or {}
    if cherry:
        lines.append(f"平滑进度：{cherry.get('completed', 0)}/{cherry.get('total', 0)} {cherry.get('status')}")
        detail = cherry.get("last_completed_detail") or {}
        if detail:
            lines.append(f"平滑刚完成：{detail.get('role')}/{detail.get('emotion')}/{detail.get('frame')}")
    for line in payload.get("detail_lines") or []:
        lines.append(str(line))
    if payload.get("error"):
        lines.append(f"错误：{payload.get('error')}")
    return lines


def _format_pipeline_list(payload: dict[str, Any], max_items: int) -> list[str]:
    items = payload.get("items") or []
    if not items:
        return ["当前没有动画自动化流程任务。"]
    lines = [f"动画自动化流程：{payload.get('count', len(items))} 个"]
    for item in items[:max_items]:
        lines.append(f"- {item.get('run_id')} {item.get('status')} 当前：{item.get('current_step')}")
        lines.append(f"  输出：{item.get('smooth_output_dir')}")
    return lines


def _format_unity_import(payload: dict[str, Any]) -> list[str]:
    lines = ["Unity 动画贴图导入："]
    mode = payload.get("mode") or payload.get("import_mode")
    if mode:
        lines.append(f"模式：{'资源迭代/替换原贴图' if mode == 'iteration' else '新导入'}")
    if payload.get("unity_ready"):
        lines.append(f"unity_ready：{payload.get('unity_ready')}")
    if payload.get("unity_project"):
        lines.append(f"Unity Project：{payload.get('unity_project')}")
    api = payload.get("api") or {}
    if api:
        lines.append(f"MCP：{'可用' if api.get('available') else '不可用'} {api.get('url') or ''}".rstrip())
    for package in payload.get("packages") or []:
        lines.append(
            f"- {package.get('package')}：任务 {package.get('task_count', 0)}，"
            f"图片 {package.get('frame_count', 0)}"
        )
    if payload.get("error"):
        lines.append(f"暂停原因：{payload.get('error')}")
    if payload.get("message"):
        lines.append(str(payload.get("message")))
    return lines


def _format_unity_tools(skill: str, payload: dict[str, Any]) -> list[str]:
    if skill == "unity_tools.atlas_status":
        lines = ["Unity 图集大小报告："]
        lines.append(f"Unity Project：{payload.get('unity_project')}")
        lines.append(f"报告：{payload.get('report_path')}")
        lines.append(f"存在：{'是' if payload.get('report_exists') else '否'}")
        report = payload.get("report") or {}
        if report:
            lines.extend(_format_atlas_report_summary(report))
        if payload.get("error"):
            lines.append(f"错误：{payload.get('error')}")
        return lines
    if skill == "unity_tools.atlas_report":
        result = payload.get("result") or {}
        lines = ["Unity 图集大小检查完成：" if payload.get("ok") else "Unity 图集大小检查失败："]
        lines.append(f"Unity Project：{payload.get('unity_project')}")
        if result.get("reportPath"):
            lines.append(f"报告：{result.get('reportPath')}")
        report = result.get("atlasReport") or {}
        if report:
            lines.extend(_format_atlas_report_summary(report))
        if payload.get("error") or result.get("error"):
            lines.append(f"错误：{payload.get('error') or result.get('error')}")
        return lines
    if skill in {"unity_tools.rename_preview", "unity_tools.rename_run"}:
        result = payload.get("result") or {}
        applied = bool(result.get("apply"))
        title = "Unity 动画贴图命名整理完成：" if applied else "Unity 动画贴图命名整理预览："
        if not payload.get("ok"):
            title = "Unity 动画贴图命名整理失败："
        lines = [title]
        lines.append(f"贴图目录：{result.get('textureFolder') or payload.get('texture_folder') or ''}")
        lines.append(f"动画目录：{result.get('animationFolder') or payload.get('animation_folder') or ''}")
        if result.get("errorCount") is not None:
            lines.append(f"错误项：{result.get('errorCount')}")
        if applied:
            lines.append(f"占位回退：{result.get('displacementDone', result.get('displacementCount', 0))}")
            lines.append(f"动画引用重命名：{result.get('animationRenameDone', result.get('animationRenameCount', 0))}")
            if result.get("manifestPath"):
                lines.append(f"Manifest：{result.get('manifestPath')}")
        else:
            lines.append(f"占位回退候选：{result.get('displacementCount', 0)}")
            lines.append(f"动画引用重命名候选：{result.get('animationRenameCount', 0)}")
        preview = result.get("preview") or []
        if preview:
            lines.append("预览前几项：")
            for item in preview[:5]:
                lines.append(f"- {item.get('oldPath')} -> {item.get('newPath')}")
        errors = result.get("errors") or []
        if errors:
            lines.append(f"首条错误：{errors[0]}")
        if payload.get("error") or result.get("error"):
            lines.append(f"错误：{payload.get('error') or result.get('error')}")
        return lines
    return ["Unity 工具执行完成。"]


def _format_atlas_report_summary(report: dict[str, Any]) -> list[str]:
    lines = [
        f"生成时间：{report.get('generatedAt') or '-'}",
        f"压缩：{report.get('compressionFormat') or '-'}",
        f"总量：{report.get('totalEstimatedSizeMB', 0)} MB，图集 {report.get('totalAtlases', 0)}，精灵 {report.get('totalSprites', 0)}",
    ]
    summary = report.get("categorySummary") or {}
    for key, label in (("character", "角色"), ("chat", "剧情"), ("order", "订单")):
        item = summary.get(key) or {}
        if item:
            lines.append(
                f"{label}：{item.get('estimatedSizeMB', 0)} MB，"
                f"图集 {item.get('atlasCount', 0)}，精灵 {item.get('spriteCount', 0)}"
            )
    return lines


def _format_animation_flow(payload: dict[str, Any], preview: bool = False) -> list[str]:
    title = "完整动画自动化流程预览：" if preview else f"完整动画自动化流程：{payload.get('run_id') or ''}".rstrip()
    lines = [title]
    if payload.get("status"):
        lines.append(f"状态：{payload.get('status')}，当前：{payload.get('current_stage')}")
    if payload.get("pipeline_notice"):
        lines.append(str(payload.get("pipeline_notice")))
    lines.append(f"工作区：{payload.get('date_root')}")
    lines.append(f"unity_ready：{payload.get('unity_ready')}")
    policy = payload.get("feishu_progress_policy") or {}
    if policy:
        included = "、".join(policy.get("include") or [])
        if included:
            lines.append(f"飞书状态：仅处理 {included}；其他状态跳过")
        else:
            skipped = "、".join(policy.get("skip") or [])
            lines.append(f"飞书状态：跳过 {skipped or '无'}；其他状态重新下载并抽帧")
    lines.append("步骤：")
    for stage in payload.get("stages") or []:
        lines.append(f"- {stage.get('status')} {stage.get('label')}")
    children = payload.get("children") or {}
    if children.get("pipeline_run_id"):
        lines.append(f"1-3 子流程：{children.get('pipeline_run_id')}")
    unity = children.get("unity_import") or {}
    if unity:
        mode = unity.get("mode") or payload.get("unity_import_mode")
        mode_text = "资源迭代" if mode == "iteration" else "新导入"
        lines.append(f"Unity {mode_text}：{'完成' if unity.get('ok') else unity.get('error')}")
    p4 = payload.get("p4") or {}
    if p4.get("next_steps"):
        lines.append("P4 下一步需要分步确认：")
        for step in p4.get("next_steps", [])[:6]:
            lines.append(f"- {step.get('skill')} {step.get('arguments')}")
    if payload.get("error"):
        lines.append(f"暂停/错误：{payload.get('error')}")
    lines.append("P4 submit：disabled")
    return lines


def _format_animation_status(payload: dict[str, Any]) -> list[str]:
    counts = payload.get("counts") or {}
    sequences = payload.get("sequences") or {}
    lines = [
        "动画工作区状态：",
        f"根目录：{payload.get('root')}",
        (
            f"数量：videos {counts.get('videos', 0)}，frames {counts.get('frames', 0)}，"
            f"matte {counts.get('matte', 0)}，smooth {counts.get('smooth', 0)}"
        ),
        (
            f"序列：videos {sequences.get('videos', 0)}，frames {sequences.get('frames', 0)}，"
            f"matte {sequences.get('matte', 0)}，smooth {sequences.get('smooth', 0)}"
        ),
        f"matte 对齐 frames：{'是' if payload.get('matte_matches_frames') else '否'}",
        f"smooth 对齐 matte：{'是' if payload.get('smooth_matches_matte') else '否'}",
    ]
    if payload.get("latest_backup"):
        lines.append(f"最近备份：{payload.get('latest_backup')}")
    if payload.get("latest_rerun_report"):
        lines.append(f"最近重跑报告：{payload.get('latest_rerun_report')}")
    runs = payload.get("runs") or {}
    for label, data in runs.items():
        if isinstance(data, dict) and data.get("items"):
            item = data["items"][0]
            lines.append(f"{label} 当前任务：{item.get('run_id')} {item.get('status')}")
    return lines


def _format_agent_current_work(payload: dict[str, Any]) -> list[str]:
    lines = ["当前执行现场："]
    active = payload.get("active") or []
    if active:
        for item in active[:6]:
            lines.append(f"- {_run_kind(item)} {item.get('run_id')}：{item.get('status')} {_run_progress(item)}".rstrip())
            if item.get("input_dir"):
                lines.append(f"  输入：{item.get('input_dir')}")
            if item.get("output_dir"):
                lines.append(f"  输出：{item.get('output_dir')}")
    else:
        lines.append("当前没有检测到运行中的 ComfyUI / Cherry / 抽帧 / 全流程任务。")

    confirmations = payload.get("pending_confirmations") or []
    if confirmations:
        latest = confirmations[0]
        lines.append(f"待确认：{len(confirmations)} 个，最新 {latest.get('skill')} / {latest.get('id')}")

    gpu = payload.get("gpu") or {}
    if gpu:
        lines.extend(_format_gpu_status(gpu))
    return lines


def _format_agent_diagnose(payload: dict[str, Any]) -> list[str]:
    lines = _format_agent_current_work(payload)
    findings = payload.get("findings") or []
    if findings:
        lines.append("判断：")
        for item in findings[:6]:
            run_id = f"（{item.get('run_id')}）" if item.get("run_id") else ""
            lines.append(f"- {item.get('level', 'info')} {item.get('topic', 'finding')}{run_id}：{item.get('message')}")
    actions = payload.get("next_actions") or []
    if actions:
        lines.append("建议下一步：")
        for action in actions[:4]:
            lines.append(f"- {action.get('skill')} {action.get('arguments') or {}}")
    return lines


def _format_sticker_info(payload: dict[str, Any]) -> list[str]:
    lines = [
        "情绪表情回复：",
        f"状态：{'开启' if payload.get('enabled') else '关闭'}",
        f"目录：{payload.get('directory')}",
        f"目录存在：{'是' if payload.get('directory_exists') else '否'}",
        f"可用表情：{payload.get('count', 0)} 个",
        f"发送概率：{payload.get('probability')}",
    ]
    samples = payload.get("sample") or []
    if samples:
        lines.append("示例：" + "、".join(_path_name(item) for item in samples[:5]))
    return lines


def _format_web_fetch(payload: dict[str, Any]) -> list[str]:
    lines = [f"网页：{payload.get('url')}"]
    if payload.get("title"):
        lines.append(f"标题：{payload.get('title')}")
    text = str(payload.get("text") or "").strip()
    if text:
        lines.append(text[:3000])
    if payload.get("truncated"):
        lines.append("内容较长，已截取前半部分。")
    return lines


def _format_web_search(payload: dict[str, Any], max_items: int) -> list[str]:
    items = payload.get("items") or []
    lines = [f"搜索：{payload.get('query')}（{payload.get('count', len(items))} 条）"]
    for item in items[:max_items]:
        title = item.get("title") or item.get("url")
        domain = item.get("domain") or ""
        lines.append(f"- {title} [{domain}]")
        if item.get("url"):
            lines.append(f"  {item.get('url')}")
        if item.get("snippet"):
            lines.append(f"  {item.get('snippet')}")
    if not items:
        lines.append("没有拿到搜索结果，可以换一个更具体的关键词。")
    return lines


def _format_web_research(payload: dict[str, Any], max_items: int) -> list[str]:
    lines = [f"联网整合：{payload.get('query')}"]
    answer = str(payload.get("answer") or "").strip()
    if answer:
        lines.append(answer)
    pages = payload.get("pages") or []
    ok_pages = [page for page in pages if page.get("ok")]
    if ok_pages:
        lines.append("来源：")
        for page in ok_pages[:max_items]:
            title = page.get("title") or page.get("url")
            lines.append(f"- {title}：{page.get('url')}")
    failed = [page for page in pages if not page.get("ok")]
    if failed:
        lines.append(f"另有 {len(failed)} 个页面读取失败。")
    return lines


def _format_p4(payload: dict[str, Any], skill: str, max_items: int) -> list[str]:
    if payload.get("text"):
        return [str(payload.get("text"))]
    if payload.get("needs_confirmation"):
        return [str(payload.get("message") or f"{skill} 需要确认后执行。")]
    if payload.get("error"):
        return [f"{skill} 失败：{payload.get('error')}"]
    operation = str(payload.get("operation") or skill)
    if payload.get("report_text"):
        return [str(payload.get("report_text"))]
    lines = [f"P4：{_p4_operation_label(operation)}"]
    if operation == "list-cls":
        if payload.get("p4client"):
            lines.append(f"P4CLIENT：{payload.get('p4client')}")
        lines.append(f"Pending：{payload.get('pending_count', 0)}，Shelved：{payload.get('shelved_count', 0)}")
        items = payload.get("items") or []
        if not items:
            lines.append("当前 workspace 没有 pending/shelved CL。")
        for item in items[:max_items]:
            marks = []
            if item.get("pending"):
                marks.append("pending")
            if item.get("shelved"):
                marks.append("shelved")
            desc = str(item.get("description") or "").strip()
            lines.append(f"- CL {item.get('id')}：{'+'.join(marks) or '-'}" + (f"，{desc}" if desc else ""))
        return lines
    if operation == "cleanup-cl":
        lines.append(f"CL：{payload.get('changelist_id')}")
        lines.append(f"Shelf 删除：{'是' if payload.get('deleted_shelf') else '无/未执行'}")
        lines.append(f"Opened revert：{'是' if payload.get('reverted') else '无/未执行'}")
        lines.append(f"CL 删除：{'是' if payload.get('deleted_changelist') else '否'}")
        stats = payload.get("stats") or {}
        if stats:
            lines.append(f"涉及文件：add {stats.get('add', 0)}，edit {stats.get('edit', 0)}，delete {stats.get('delete', 0)}，move {stats.get('move', 0)}。")
        return lines
    if operation in {"status", "check", "preview", "create-cl", "reconcile", "shelve", "report"}:
        if payload.get("p4port"):
            lines.append(f"P4PORT：{payload.get('p4port')}")
        if payload.get("p4user"):
            lines.append(f"P4USER：{payload.get('p4user')}")
        if payload.get("p4client"):
            lines.append(f"P4CLIENT：{payload.get('p4client')}")
        if payload.get("root"):
            lines.append(f"Root：{payload.get('root')}")
        if payload.get("mode"):
            lines.append(f"Mode：{payload.get('mode')}")
        lines.append("Submit：disabled")
        if payload.get("logged_in") is not None:
            lines.append(f"登录：{'有效' if payload.get('logged_in') else '需要 p4 login'}")
        if payload.get("workspace_matches_config") is not None:
            lines.append(f"Workspace 匹配配置：{'是' if payload.get('workspace_matches_config') else '否'}")
        if payload.get("managed_paths"):
            lines.append("Managed paths：")
            lines.extend(f"- {path}" for path in payload.get("managed_paths", [])[:max_items])
        stats = payload.get("stats") or {}
        if stats:
            lines.append(f"文件统计：add {stats.get('add', 0)}，edit {stats.get('edit', 0)}，delete {stats.get('delete', 0)}，move {stats.get('move', 0)}。")
        files = payload.get("files") or []
        for item in files[:max_items]:
            lines.append(f"- {item.get('action', 'unknown')} {item.get('path', '')}")
        safety = payload.get("safety") or {}
        if safety:
            checks = safety.get("checks") or {}
            if checks:
                lines.append("安全检查：")
                lines.extend(f"- {name}: {value}" for name, value in checks.items())
            for warning in safety.get("warnings") or []:
                lines.append(f"WARNING：{warning}")
            for error in safety.get("errors") or []:
                lines.append(f"阻断：{error}")
        return lines
    if payload.get("client_spec") and "setup_workspace" in str(payload.get("operation")):
        lines.append(f"root：{payload.get('root')}")
        if payload.get("stream"):
            lines.append(f"stream：{payload.get('stream')}")
        lines.append("client spec 预览：")
        lines.append(str(payload.get("client_spec"))[:2000])
    summary = payload.get("summary") or {}
    if summary:
        if operation == "inventory":
            counts = summary.get("counts") or {}
            info = summary.get("info") or {}
            lines.append(f"总览：{counts.get('depots', 0)} 个 depot，{counts.get('clients_for_user', 0)} 个你的 workspace/client。")
            if info:
                lines.append(f"当前：{info.get('user name')} / {info.get('client name')}")
                lines.append(f"本地：{info.get('client root')}")
                if info.get("client stream"):
                    lines.append(f"流：{info.get('client stream')}")
            depots = summary.get("depots") or []
            if depots:
                lines.append("Depot：")
                for depot in depots[:max_items]:
                    detail = depot.get("type") or ""
                    lines.append(f"- {depot.get('name')}" + (f"（{detail}）" if detail else ""))
                if len(depots) > max_items:
                    lines.append(f"还有 {len(depots) - max_items} 个 depot 未显示。")
            clients = summary.get("clients") or []
            if clients:
                lines.append("Workspace/client：")
                for client in clients[:max_items]:
                    mark = "（当前）" if client.get("name") == payload.get("p4client") else ""
                    lines.append(f"- {client.get('name')}{mark}")
                    if client.get("name") == payload.get("p4client"):
                        lines.append(f"  root：{client.get('root')}")
            mappings = summary.get("configured_mappings") or []
            if mappings:
                lines.append("当前映射：")
                for mapping in mappings[:max_items]:
                    lines.append(f"- {mapping.get('depot')} -> {mapping.get('local')}")
        if operation == "workspace_details":
            items = summary.get("items") or []
            lines.append(f"工作区详情：{summary.get('count', len(items))} 个。")
            for item in items[:max_items]:
                mark = "（当前）" if item.get("is_current") else ""
                lines.append(f"- {item.get('name')}{mark}")
                lines.append(f"  root：{item.get('root')}")
                if item.get("stream"):
                    lines.append(f"  stream：{item.get('stream')}")
                view_lines = item.get("view_lines") or []
                if view_lines:
                    lines.append(f"  view：{view_lines[0]}")
                if item.get("update") or item.get("access"):
                    lines.append(f"  update/access：{item.get('update') or '-'} / {item.get('access') or '-'}")
        if operation == "compare_depot":
            local_status = summary.get("local_status") or {}
            sync_preview = summary.get("sync_preview") or {}
            out_of_date = summary.get("out_of_date") or []
            not_synced = summary.get("not_synced") or []
            deleted_at_head = summary.get("deleted_at_head") or []
            missing_top = summary.get("missing_top_level_items") or []
            checked_count = summary.get("active_depot_file_count", summary.get("depot_file_count", 0))
            if summary.get("clean"):
                lines.append(f"结论：本地 workspace 和 depot/head 一致（检查 {checked_count} 个当前 depot 文件）。")
            else:
                lines.append(f"结论：发现差异（检查 {checked_count} 个当前 depot 文件）。")
                if missing_top:
                    names = "、".join(str(item.get("name")) for item in missing_top[:max_items])
                    lines.append(f"本地缺少 depot 顶层项：{names}。")
                lines.append(
                    f"服务器更新：待同步 {sync_preview.get('preview_count', 0)}，"
                    f"版本落后 {summary.get('out_of_date_count', len(out_of_date))}，"
                    f"本地未同步 {summary.get('not_synced_count', len(not_synced))}，"
                    f"head 已删除 {summary.get('deleted_at_head_count', len(deleted_at_head))}。"
                )
                lines.append(
                    f"本地改动：opened {local_status.get('opened_count', 0)}，"
                    f"新增 {len(local_status.get('local_adds') or [])}，"
                    f"修改 {len(local_status.get('local_edits') or []) + len(local_status.get('diff_files') or [])}，"
                    f"删除 {len(local_status.get('local_deletes') or [])}。"
                )
                if not missing_top:
                    for item in out_of_date[:max_items]:
                        lines.append(f"- 落后：{item.get('depotFile')} have#{item.get('haveRev')} / head#{item.get('headRev')}")
                    for item in not_synced[:max_items]:
                        lines.append(f"- 未同步：{item.get('depotFile')} head#{item.get('headRev')}")
        if operation == "list_workflows":
            lines.append(f"P4 工作流：{summary.get('count', 0)} 个")
            roots = summary.get("roots") or []
            if roots:
                lines.append(f"目录：{roots[0]}")
            for item in (summary.get("items") or [])[:max_items]:
                brief = ""
                if item.get("node_count") is not None:
                    brief = f"（节点 {item.get('node_count')}，输入 {item.get('load_image_count', 0)}，输出 {item.get('save_image_count', 0)}）"
                elif item.get("size") is not None:
                    brief = f"（{_format_size(int(item.get('size') or 0))}）"
                lines.append(f"- {item.get('name')}{brief}")
            if summary.get("missing_roots"):
                lines.append("未找到目录：" + "、".join(summary.get("missing_roots") or []))
            if summary.get("truncated"):
                lines.append(f"还有更多，先显示前 {max_items} 个。")
        if "opened_count" in summary:
            opened = summary.get("opened_count", 0)
            adds = summary.get("local_adds") or []
            edits = summary.get("local_edits") or []
            deletes = summary.get("local_deletes") or []
            diffs = summary.get("diff_files") or []
            if opened == 0 and not adds and not edits and not deletes and not diffs:
                lines.append("状态：干净，没有打开文件，也没有本地待提交改动。")
            else:
                lines.append(f"状态：opened {opened}，新增 {len(adds)}，修改 {len(edits) + len(diffs)}，删除 {len(deletes)}。")
            for path in (summary.get("opened_files") or [])[:max_items]:
                lines.append(f"- opened：{path}")
            for path in adds[:max_items]:
                lines.append(f"- 新增：{path}")
            for path in edits[:max_items]:
                lines.append(f"- 修改：{path}")
            for path in deletes[:max_items]:
                lines.append(f"- 删除：{path}")
        if "preview_count" in summary:
            lines.append(f"同步预览：{summary.get('preview_count', 0)} 个文件会变化。")
        if "adds" in summary or "edits" in summary or "deletes" in summary:
            lines.append(
                f"reconcile 预览：新增 {len(summary.get('adds') or [])}，"
                f"修改 {len(summary.get('edits') or [])}，删除 {len(summary.get('deletes') or [])}。"
            )
        if summary.get("suggested_changelist_description"):
            lines.append("建议 changelist 描述：")
            lines.append(str(summary.get("suggested_changelist_description")))
    checks = payload.get("checks") or {}
    if checks:
        bad = [name for name, ok in checks.items() if not ok]
        lines.append("配置：" + ("正常" if not bad else "需要检查 " + "、".join(bad)))
    info = payload.get("info") or {}
    if info and payload.get("operation") == "inspect_workspace":
        server = info.get("server address")
        if server:
            lines.append(f"server：已连接（内部地址 {server}）")
        if info.get("client stream"):
            lines.append(f"stream：{info.get('client stream')}")
    warnings = payload.get("warnings") or []
    for warning in warnings[:max_items]:
        lines.append(f"风险提示：{warning}")
    blockers = payload.get("blockers") or []
    for blocker in blockers[:max_items]:
        lines.append(f"阻塞：{blocker}")
    return lines


def _p4_operation_label(operation: str) -> str:
    labels = {
        "inspect_workspace": "连接信息",
        "inventory": "拓扑总览",
        "workspace_details": "工作区详情",
        "compare_depot": "本地/Depot 对比",
        "list_workflows": "工作流清单",
        "get_status": "状态检查",
        "preview_sync": "同步预览",
        "do_sync": "同步完成",
        "preview_reconcile": "reconcile 预览",
        "do_reconcile": "reconcile 完成",
        "build_changelist_summary": "changelist 摘要",
        "preview_setup_workspace": "workspace 预览",
        "setup_workspace": "workspace 已配置",
        "status": "Shelve-only 状态",
        "check": "Shelve-only 安全检查",
        "preview": "UI 资源 reconcile 预览",
        "create-cl": "pending changelist 已创建",
        "reconcile": "UI 资源已 reconcile",
        "shelve": "Shelve 完成",
        "report": "飞书报告",
    }
    return labels.get(operation, operation)


def _format_life_weather(payload: dict[str, Any]) -> list[str]:
    if payload.get("fallback") and not payload.get("condition"):
        return [str(payload.get("fallback"))]
    location = payload.get("location") or "当前位置"
    condition = payload.get("condition") or "天气未知"
    temp = payload.get("temperature_c")
    feels = payload.get("feels_like_c")
    rain = payload.get("rain_chance")
    bits = [f"{location}现在：{condition}"]
    if temp:
        bits.append(f"{temp}°C")
    if feels:
        bits.append(f"体感 {feels}°C")
    if rain not in (None, ""):
        bits.append(f"降雨概率 {rain}%")
    lines = ["，".join(bits) + "。"]
    if payload.get("advice"):
        lines.append(str(payload.get("advice")))
    return lines


def _format_life_food(payload: dict[str, Any]) -> list[str]:
    lines = [str(payload.get("opener") or "给你几个选择：")]
    for item in payload.get("options", [])[:4]:
        lines.append(f"- {item.get('name')}：{item.get('reason')}")
    if payload.get("location"):
        lines.append(f"位置我先按 {payload.get('location')} 来理解。")
    return lines


def _format_direct_video(skill: str, payload: dict[str, Any], max_items: int) -> list[str]:
    if payload.get("error"):
        return [f"{skill} 失败：{payload.get('error')}"]
    run_id = payload.get("run_id") or payload.get("id") or ""
    status = payload.get("status") or ""
    stage = payload.get("stage") or ""
    videos = payload.get("videos") or []
    children = payload.get("children") if isinstance(payload.get("children"), dict) else {}
    lines: list[str] = []
    if skill == "direct_video.start":
        lines.append(f"动画处理任务已启动：{run_id}")
        lines.append(f"视频：{len(videos)} 个")
        if payload.get("pipeline_notice"):
            lines.append(str(payload.get("pipeline_notice")))
        lines.append("步骤：原视频 -> 抽帧 -> ComfyUI 抠图 -> Cherry 后处理 -> zip 回传")
        return lines
    if skill == "direct_video.list":
        items = payload.get("items") or []
        lines.append(f"直传视频处理任务：{payload.get('count', len(items))} 个")
        for item in items[:max_items]:
            lines.append(f"- {item.get('run_id')}：{item.get('status')} / {item.get('stage')} / {len(item.get('videos') or [])} 个视频")
        return lines
    if skill == "direct_video.cancel":
        return [f"{run_id}：{status}"]

    lines.append(f"动画处理进度：{run_id}")
    lines.append(f"状态：{status} / {stage}")
    if videos:
        lines.append(f"视频：{len(videos)} 个")
        for item in videos[:max_items]:
            detail = []
            if item.get("frame_count"):
                detail.append(f"{item.get('frame_count')} 帧")
            if item.get("aspect"):
                detail.append(str(item.get("aspect")))
            if item.get("cherry_profile"):
                detail.append(f"后处理 {item.get('cherry_profile')}")
            suffix = "，" + "，".join(detail) if detail else ""
            lines.append(f"- {item.get('name')}{suffix}")
    comfy = children.get("comfyui") if isinstance(children.get("comfyui"), dict) else {}
    if comfy:
        lines.append(f"抠图：{comfy.get('completed', 0)}/{comfy.get('total', 0)}，{comfy.get('status')}")
    cherry = children.get("cherry") if isinstance(children.get("cherry"), dict) else {}
    if cherry:
        lines.append(f"后处理：{cherry.get('completed', 0)}/{cherry.get('total', 0)}，{cherry.get('status')}")
    if payload.get("zip_path"):
        lines.append(f"zip：{payload.get('zip_path')}")
    if payload.get("last_log"):
        lines.append(f"最近：{payload.get('last_log')}")
    return lines


def _format_direct_image(skill: str, payload: dict[str, Any], max_items: int) -> list[str]:
    if payload.get("error") and not payload.get("run_id"):
        return [str(payload.get("error"))]
    run_id = payload.get("run_id") or payload.get("id") or ""
    status = payload.get("status") or ""
    stage = payload.get("stage") or ""
    images = payload.get("images") or []
    children = payload.get("children") or {}
    comfy = children.get("comfyui") if isinstance(children.get("comfyui"), dict) else {}
    cherry = children.get("cherry") if isinstance(children.get("cherry"), dict) else {}
    if skill == "direct_image.start":
        lines = [
            f"图片处理任务已启动：{run_id}",
            f"图片：{len(images)} 张",
        ]
        if payload.get("pipeline_notice"):
            lines.append(str(payload.get("pipeline_notice")))
        lines.append("步骤：ComfyUI 抠图 -> Cherry 后处理 -> 文件附件回传")
        return lines
    if skill == "direct_image.list":
        items = payload.get("items") or []
        lines = [f"图片处理任务：{payload.get('count', len(items))} 个"]
        for item in items[:max_items]:
            lines.append(f"- {item.get('run_id')}：{item.get('status')} / {item.get('stage')} / {len(item.get('images') or [])} 张图片")
        return lines
    if skill == "direct_image.cancel":
        return [f"{run_id}：{status}"]
    lines = [
        f"图片处理进度：{run_id}",
        f"状态：{status} / {stage}",
        f"图片：{len(images)} 张",
    ]
    if comfy:
        lines.append(f"抠图：{comfy.get('completed', 0)}/{comfy.get('total', 0)}，{comfy.get('status')}")
    if cherry:
        lines.append(f"后处理：{cherry.get('completed', 0)}/{cherry.get('total', 0)}，{cherry.get('status')}")
    sent = payload.get("sent_files") or []
    if sent:
        lines.append(f"已发回附件：{len(sent)} 个")
    if payload.get("error"):
        lines.append(f"错误：{payload.get('error')}")
    return lines


def _run_kind(item: dict[str, Any]) -> str:
    run_id = str(item.get("run_id") or "")
    if run_id.startswith("COMFY_"):
        return "ComfyUI"
    if run_id.startswith("CHERRY_"):
        return "Cherry"
    if run_id.startswith("FRAME_"):
        return "抽帧"
    if run_id.startswith("PIPE_"):
        return "全流程"
    return "任务"


def _run_progress(item: dict[str, Any]) -> str:
    if "completed" in item or "total" in item:
        return f"{item.get('completed', 0)}/{item.get('total', 0)}"
    if "processed_records" in item or "total_records" in item:
        return f"{item.get('processed_records', 0)}/{item.get('total_records', 0)}"
    if item.get("current_step"):
        return f"当前步骤 {item.get('current_step')}"
    return ""


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


def _format_matting_pipeline(skill: str, payload: dict[str, Any], max_items: int) -> list[str]:
    lines = ["抠图管线：ImageClip"]
    repo_dir = payload.get("repo_dir")
    if repo_dir:
        lines.append(f"本地仓库：{repo_dir}")
    if payload.get("repo_url"):
        lines.append(f"远程：{payload.get('repo_url')}")
    if payload.get("branch") or payload.get("commit"):
        branch = payload.get("branch") or "-"
        commit = payload.get("commit") or "-"
        lines.append(f"版本：{branch} / {commit}")
    if payload.get("commit_time"):
        lines.append(f"Git 更新时间：{payload.get('commit_time')}")
    if payload.get("commit_subject"):
        lines.append(f"提交说明：{payload.get('commit_subject')}")
    if payload.get("dirty"):
        lines.append("状态：本地仓库有未提交改动")
    if payload.get("workflow_path"):
        workflow_state = "存在" if payload.get("workflow_exists") else "缺失"
        lines.append(f"默认工作流：{payload.get('workflow_path')}（{workflow_state}）")
    if payload.get("workflow_nodes") is not None:
        lines.append(f"工作流节点：{payload.get('workflow_nodes')}")

    assets = payload.get("synced") or payload.get("assets") or []
    if assets:
        lines.append("资源同步：")
    for item in assets[:max_items]:
        name = item.get("name")
        kind = item.get("kind")
        source = item.get("source")
        target = item.get("target")
        mode = item.get("mode") or item.get("target_mode") or "-"
        if "source_exists" in item or "target_exists" in item:
            source_state = "源OK" if item.get("source_exists") else "源缺失"
            target_state = "目标OK" if item.get("target_exists") else "目标缺失"
            link_state = "已链接" if item.get("linked_to_source") else mode
            lines.append(f"- {kind} {name}：{source_state} / {target_state} / {link_state}")
        else:
            lines.append(f"- {kind} {name}：{mode}")
        if source and target:
            lines.append(f"  {source} -> {target}")

    errors = payload.get("errors") or payload.get("verify_errors") or []
    for error in errors[:max_items]:
        lines.append(f"问题：{error}")
    if skill == "matting_pipeline.update":
        if payload.get("git_output"):
            last_line = str(payload.get("git_output")).strip().splitlines()[-1:]
            if last_line:
                lines.append(f"Git：{last_line[0]}")
        lines.append("同步完成后请重启 ComfyUI 或用秋叶重载，让 custom node / workflow / lora 全部生效。")
    elif payload.get("all_ready") is True:
        lines.append("结论：当前管线资源齐全，可以使用。")
    elif payload.get("all_ready") is False:
        lines.append("结论：当前管线还没完全就绪。")
    return lines
