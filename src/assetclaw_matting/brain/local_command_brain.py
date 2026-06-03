from __future__ import annotations

import re

from assetclaw_matting.brain.base import BrainProvider
from assetclaw_matting.brain.conversation_recall import answer_recent_question
from assetclaw_matting.brain.file_task_planner import plan_file_task
from assetclaw_matting.brain.multimodal_planner import answer_recent_image_question, plan_multimodal_task
from assetclaw_matting.brain.result_formatter import format_skill_results
from assetclaw_matting.brain.schemas import BrainMessage, BrainResponse, ToolCall
from assetclaw_matting.brain.translation_planner import plan_translation_task


class LocalCommandBrain(BrainProvider):
    name = "local_command"

    def handle_message(self, message: BrainMessage) -> BrainResponse:
        text = message.text.strip()
        image_answer = answer_recent_image_question(message)
        if image_answer:
            response = BrainResponse(text=image_answer, provider=self.name)
            self.log_message(message, response)
            return response

        translated = plan_translation_task(message)
        if translated:
            tool_calls, planned_text = translated
            if not tool_calls:
                response = BrainResponse(text=planned_text, provider=self.name)
                self.log_message(message, response)
                return response
            results = self.execute_tool_calls(
                tool_calls,
                conversation_id=message.conversation_id,
                user_id=message.user_id,
            )
            response = BrainResponse(
                text=format_skill_results(results),
                tool_calls=tool_calls,
                raw={"deterministic_plan": planned_text, "skill_results": results},
                provider=self.name,
            )
            self.log_message(message, response)
            return response

        multimodal = plan_multimodal_task(message)
        if multimodal:
            tool_calls, planned_text = multimodal
            if not tool_calls:
                response = BrainResponse(text=planned_text, provider=self.name)
                self.log_message(message, response)
                return response
            results = self.execute_tool_calls(
                tool_calls,
                conversation_id=message.conversation_id,
                user_id=message.user_id,
            )
            response = BrainResponse(
                text=format_skill_results(results),
                tool_calls=tool_calls,
                raw={"deterministic_plan": planned_text, "skill_results": results},
                provider=self.name,
            )
            self.log_message(message, response)
            return response

        recalled = answer_recent_question(text, message.conversation_id)
        if recalled:
            response = BrainResponse(text=recalled, provider=self.name)
            self.log_message(message, response)
            return response

        planned = plan_file_task(message)
        if planned:
            tool_calls, planned_text = planned
            if not tool_calls:
                response = BrainResponse(text=planned_text, provider=self.name)
                self.log_message(message, response)
                return response
            results = self.execute_tool_calls(
                tool_calls,
                conversation_id=message.conversation_id,
                user_id=message.user_id,
            )
            response = BrainResponse(
                text=format_skill_results(results),
                tool_calls=tool_calls,
                raw={"deterministic_plan": planned_text, "skill_results": results},
                provider=self.name,
            )
            self.log_message(message, response)
            return response

        tool_calls = self._infer_tool_calls(text)
        if not tool_calls:
            response = BrainResponse(
                text="我还没理解这句。可以直接说：看看 E 盘有哪些文件，或 查看技能列表。",
                provider=self.name,
            )
            self.log_message(message, response)
            return response

        results = self.execute_tool_calls(
            tool_calls,
            conversation_id=message.conversation_id,
            user_id=message.user_id,
        )
        response = BrainResponse(
            text=format_skill_results(results),
            tool_calls=tool_calls,
            raw={"skill_results": results},
            provider=self.name,
        )
        self.log_message(message, response)
        return response

    def _infer_tool_calls(self, text: str) -> list[ToolCall]:
        lowered = text.lower()

        # bot / system queries
        if any(kw in text for kw in ("你会做什么", "帮助", "怎么用", "使用说明")) or lowered in ("help", "帮助"):
            return [ToolCall(skill="bot.help", arguments={})]
        if any(kw in text for kw in ("技能列表", "查看技能", "skill list")):
            return [ToolCall(skill="bot.skills", arguments={})]
        if any(kw in text for kw in ("权限说明", "安全边界", "查看权限", "查看安全")):
            return [ToolCall(skill="bot.permissions", arguments={})]
        if any(kw in text for kw in ("系统状态", "当前状态", "bot status")) or "查看状态" in text:
            return [ToolCall(skill="bot.status", arguments={})]
        if any(kw in text for kw in ("最近错误", "查看错误", "recent errors", "错误记录")):
            return [ToolCall(skill="bot.errors", arguments={})]
        if ("z盘" in lowered or "z 盘" in lowered) and any(kw in text for kw in ("哪些文件", "有什么", "有哪些", "列", "查看", "看看")):
            return [ToolCall(skill="file.list_allowed", arguments={"path": "Z:\\"})]
        if any(kw in text for kw in ("共享盘", "公共盘", "公共机共享")) and any(
            kw in text for kw in ("能访问", "可以访问", "能查看", "可以查看", "权限", "哪些文件", "有什么", "有哪些", "列", "查看", "看看")
        ):
            from assetclaw_matting.config import settings

            if any(kw in text for kw in ("权限", "能访问", "可以访问")) and not any(
                kw in text for kw in ("哪些文件", "有什么", "有哪些", "列")
            ):
                return [ToolCall(skill="workspace.roots", arguments={})]
            return [ToolCall(skill="file.list_allowed", arguments={"path": settings.shared_matting_root})]
        if any(kw in lowered for kw in ("nvidia-smi", "gpu")) or any(kw in text for kw in ("显卡", "显存", "gpu", "GPU")):
            return [ToolCall(skill="system.gpu_status", arguments={})]
        is_pipeline = "自动化流程" in text or "动画流程" in text or "完整流程" in text or "三步流程" in text
        if is_pipeline and any(kw in text for kw in ("哪些任务", "任务列表", "当前任务", "有哪些任务")):
            return [ToolCall(skill="pipeline.run_list", arguments={"include_finished": any(kw in text for kw in ("全部", "历史", "已结束"))})]
        if is_pipeline and any(kw in text for kw in ("终止", "取消", "停止")):
            match = re.search(r"(PIPE_[A-Fa-f0-9]{12})", text)
            return [ToolCall(skill="pipeline.run_cancel", arguments={"run_id": match.group(1) if match else None})]
        if is_pipeline and any(kw in text for kw in ("进度", "状态", "哪里了", "跑到")):
            match = re.search(r"(PIPE_[A-Fa-f0-9]{12})", text)
            return [ToolCall(skill="pipeline.run_status", arguments={"run_id": match.group(1) if match else None})]
        if is_pipeline and any(kw in text for kw in ("预览", "看看", "检查")):
            paths = re.findall(r"([A-Za-z]:\\[^\s，。]*)", text)
            args = _pipeline_args_from_paths(paths)
            return [ToolCall(skill="pipeline.run_preview", arguments=args)]
        if is_pipeline and any(kw in text for kw in ("启动", "开始", "执行", "跑")):
            paths = re.findall(r"([A-Za-z]:\\[^\s，。]*)", text)
            args = _pipeline_args_from_paths(paths)
            return [ToolCall(skill="pipeline.run_start", arguments=args)]
        is_frame = "抽帧" in text or "序列帧" in text or "飞书表格" in text and "视频" in text
        if is_frame and any(kw in text for kw in ("工具", "配置", "状态")) and not any(kw in text for kw in ("任务", "进度")):
            return [ToolCall(skill="frame.info", arguments={})]
        if is_frame and any(kw in text for kw in ("哪些任务", "任务列表", "当前任务", "有哪些任务")):
            return [ToolCall(skill="frame.run_list", arguments={"include_finished": any(kw in text for kw in ("全部", "历史", "已结束"))})]
        if is_frame and any(kw in text for kw in ("终止", "取消", "停止")):
            match = re.search(r"(FRAME_[A-Fa-f0-9]{12})", text)
            return [ToolCall(skill="frame.run_cancel", arguments={"run_id": match.group(1) if match else None})]
        if is_frame and any(kw in text for kw in ("进度", "状态", "哪里了", "跑到")):
            match = re.search(r"(FRAME_[A-Fa-f0-9]{12})", text)
            return [ToolCall(skill="frame.run_status", arguments={"run_id": match.group(1) if match else None})]
        if is_frame and any(kw in text for kw in ("预览", "检查")):
            paths = re.findall(r"([A-Za-z]:\\[^\s，。]*)", text)
            args = _frame_args_from_paths(paths, text)
            return [ToolCall(skill="frame.run_preview", arguments=args)]
        if is_frame and any(kw in text for kw in ("启动", "开始", "执行", "跑")):
            paths = re.findall(r"([A-Za-z]:\\[^\s，。]*)", text)
            args = _frame_args_from_paths(paths, text)
            return [ToolCall(skill="frame.run_start", arguments=args)]
        is_cherry = "cherry" in lowered or "帧序列" in text or "序列处理" in text or "平滑任务" in text or "平滑处理" in text
        if is_cherry and any(kw in text for kw in ("状态", "默认参数", "工具", "可用")) and not any(kw in text for kw in ("进度", "任务")):
            return [ToolCall(skill="cherry.info", arguments={})]
        if is_cherry and any(kw in text for kw in ("哪些任务", "任务列表", "当前任务", "现在有哪些任务", "有哪些任务", "还有任务")):
            include_finished = any(kw in text for kw in ("最近", "历史", "已结束", "失败", "取消", "全部"))
            return [ToolCall(skill="cherry.run_list", arguments={"include_archived": False, "include_finished": include_finished})]
        if is_cherry and any(kw in text for kw in ("终止", "取消", "停止", "别跑了")):
            match = re.search(r"(CHERRY_[A-Fa-f0-9]{12})", text)
            return [ToolCall(skill="cherry.run_cancel", arguments={"run_id": match.group(1) if match else None})]
        if is_cherry and any(kw in text for kw in ("删除", "清掉", "移除")):
            match = re.search(r"(CHERRY_[A-Fa-f0-9]{12})", text)
            return [ToolCall(skill="cherry.run_delete", arguments={"run_id": match.group(1) if match else None})]
        if is_cherry and any(kw in text for kw in ("进度", "跑到", "状态", "哪里了", "做到哪里")):
            match = re.search(r"(CHERRY_[A-Fa-f0-9]{12})", text)
            return [ToolCall(skill="cherry.run_status", arguments={"run_id": match.group(1) if match else None})]
        if is_cherry and any(kw in text for kw in ("预览", "检查", "确认一下", "看看任务")):
            paths = re.findall(r"([A-Za-z]:\\[^\s，。]*)", text)
            if paths:
                return [
                    ToolCall(
                        skill="cherry.run_preview",
                        arguments={
                            "input_dir": paths[0],
                            "output_dir": paths[1] if len(paths) >= 2 else "E:\\cherry_output",
                            "recursive": True,
                        },
                    )
                ]
        if is_cherry and any(kw in text for kw in ("启动", "开始", "跑", "执行", "处理", "平滑", "锐化", "缩放")):
            paths = re.findall(r"([A-Za-z]:\\[^\s，。]*)", text)
            if paths:
                return [
                    ToolCall(
                        skill="cherry.run_start",
                        arguments={
                            "input_dir": paths[0],
                            "output_dir": paths[1] if len(paths) >= 2 else "E:\\cherry_output",
                            "recursive": True,
                            "notify_interval_seconds": 60,
                        },
                    )
                ]
        if "comfyui" in lowered and any(kw in text for kw in ("状态", "在线", "进程", "使用情况")):
            return [ToolCall(skill="comfyui.status", arguments={})]
        if ("comfyui" in lowered or "抠图" in text or "平滑" in text) and any(
            kw in text for kw in ("哪些任务", "任务列表", "当前任务", "现在有哪些任务", "有哪些任务", "还有任务", "正在跑的任务")
        ):
            include_finished = any(kw in text for kw in ("最近", "历史", "已结束", "失败", "取消", "全部"))
            return [ToolCall(skill="comfyui.run_list", arguments={"include_archived": False, "include_finished": include_finished})]
        if ("新增" in text or "新建" in text or "创建" in text) and ("comfyui" in lowered or "任务" in text):
            return [ToolCall(skill="comfyui.workflows", arguments={})]
        if "comfyui" in lowered and any(kw in text for kw in ("队列", "在跑什么", "跑什么", "排队")):
            return [ToolCall(skill="comfyui.queue_status", arguments={})]
        if ("comfyui" in lowered or "工作流" in text or "workflow" in lowered) and any(kw in text for kw in ("工作流", "workflow", "管线")) and any(kw in text for kw in ("列", "有哪些", "当前有哪些")):
            return [ToolCall(skill="comfyui.workflows", arguments={})]
        if ("workflow" in lowered or "工作流" in text or "管线" in text) and any(kw in text for kw in ("选择", "切换", "使用", "设为")):
            match = re.search(r"([A-Za-z]:\\[^\s，。]*\.json)", text, re.IGNORECASE)
            if not match:
                match = re.search(r"([\w\u4e00-\u9fff\-]+\.json)", text, re.IGNORECASE)
            if match:
                return [ToolCall(skill="comfyui.workflow_select", arguments={"path": match.group(1)})]
        if ("workflow" in lowered or "工作流" in text or "管线" in text) and any(kw in text for kw in ("信息", "节点", "参数", "具体")):
            match = re.search(r"([A-Za-z]:\\[^\s，。]*\.json)", text, re.IGNORECASE)
            return [ToolCall(skill="comfyui.workflow_info", arguments={"path": match.group(1) if match else None})]
        if ("抠图" in text or "comfyui" in lowered or "任务" in text) and any(kw in text for kw in ("暂停", "先停", "停一下", "暂缓", "暂停后续")):
            match = re.search(r"(COMFY_[A-Fa-f0-9]{12})", text)
            return [ToolCall(skill="comfyui.run_pause", arguments={"run_id": match.group(1) if match else None})]
        if ("抠图" in text or "comfyui" in lowered or "任务" in text) and any(kw in text for kw in ("继续", "恢复", "接着跑")):
            match = re.search(r"(COMFY_[A-Fa-f0-9]{12})", text)
            return [ToolCall(skill="comfyui.run_resume", arguments={"run_id": match.group(1) if match else None})]
        if ("抠图" in text or "comfyui" in lowered or "任务" in text) and any(kw in text for kw in ("删除", "清掉", "移除")):
            match = re.search(r"(COMFY_[A-Fa-f0-9]{12})", text)
            return [ToolCall(skill="comfyui.run_delete", arguments={"run_id": match.group(1) if match else None})]
        if ("抠图" in text or "comfyui" in lowered or "任务" in text) and any(kw in text for kw in ("终止", "取消", "停止", "别跑了")):
            match = re.search(r"(COMFY_[A-Fa-f0-9]{12})", text)
            return [ToolCall(skill="comfyui.run_cancel", arguments={"run_id": match.group(1) if match else None, "interrupt_current": True})]
        if ("任务" in text or "comfyui" in lowered or "抠图" in text or "平滑" in text) and any(kw in text for kw in ("更改", "修改", "改成", "换成")):
            match = re.search(r"(COMFY_[A-Fa-f0-9]{12})", text)
            paths = re.findall(r"([A-Za-z]:\\[^\s，。]*)", text)
            workflow = next((p for p in paths if p.lower().endswith(".json")), None)
            dirs = [p for p in paths if not p.lower().endswith(".json")]
            args = {"run_id": match.group(1) if match else None}
            if workflow or "工作流" in text:
                args["workflow_path"] = workflow or text
            if dirs:
                if any(kw in text for kw in ("输出", "output")) and not any(kw in text for kw in ("输入", "input")):
                    args["output_dir"] = dirs[0]
                else:
                    args["input_dir"] = dirs[0]
                    if len(dirs) >= 2:
                        args["output_dir"] = dirs[1]
            return [ToolCall(skill="comfyui.run_update", arguments=args)]
        if ("comfyui" in lowered or "抠图" in text) and any(kw in text for kw in ("跑到多少", "进度", "还需要多久", "输入输出", "当前管线", "现在跑", "做到哪里")):
            return [ToolCall(skill="comfyui.run_status", arguments={})]
        if "comfyui" in lowered and any(kw in text for kw in ("同步输出", "下载输出", "拉取输出")):
            match = re.search(r"(COMFY_[A-Fa-f0-9]{12})", text)
            return [ToolCall(skill="comfyui.run_sync_outputs", arguments={"run_id": match.group(1) if match else ""})]
        if ("comfyui" in lowered or "批量抠图" in text or "抠图管线" in text or "抠图任务" in text) and any(kw in text for kw in ("预览", "检查", "确认一下", "看看任务")):
            paths = re.findall(r"([A-Za-z]:\\[^\s，。]*)", text)
            workflow = next((p for p in paths if p.lower().endswith(".json")), None)
            dirs = [p for p in paths if not p.lower().endswith(".json")]
            return [
                ToolCall(
                    skill="comfyui.run_preview",
                    arguments={
                        "workflow_path": workflow,
                        "input_dir": dirs[0] if dirs else "E:\\input",
                        "output_dir": dirs[1] if len(dirs) >= 2 else "E:\\output",
                        "recursive": True,
                        "preserve_structure": True,
                    },
                )
            ]
        if ("comfyui" in lowered or "批量抠图" in text or "抠图管线" in text or "抠图任务" in text) and any(kw in text for kw in ("启动", "开始", "跑", "执行")):
            paths = re.findall(r"([A-Za-z]:\\[^\s，。]*)", text)
            workflow = next((p for p in paths if p.lower().endswith(".json")), None)
            dirs = [p for p in paths if not p.lower().endswith(".json")]
            if len(dirs) >= 2:
                return [
                    ToolCall(
                        skill="comfyui.run_start",
                        arguments={
                            "workflow_path": workflow,
                            "input_dir": dirs[0],
                            "output_dir": dirs[1],
                            "recursive": True,
                            "preserve_structure": True,
                            "notify_interval_seconds": 300,
                        },
                    )
                ]
            if any(kw in text for kw in ("批量抠图", "抠图管线", "开始抠图")):
                return [
                    ToolCall(
                        skill="comfyui.run_start",
                        arguments={
                            "workflow_path": workflow,
                            "input_dir": "E:\\input",
                            "output_dir": "E:\\output",
                            "recursive": True,
                            "preserve_structure": True,
                            "notify_interval_seconds": 300,
                        },
                    )
                ]
        if any(kw in text for kw in ("共享盘", "公共盘", "公共机共享")) and any(kw in text for kw in ("跑", "抠图", "开始", "启动")):
            paths = re.findall(r"((?:[A-Za-z]:|\\\\)[^\s，。]*)", text)
            workflow = next((p for p in paths if p.lower().endswith(".json")), None)
            dirs = [p for p in paths if not p.lower().endswith(".json")]
            if len(dirs) >= 2:
                return [
                    ToolCall(
                        skill="matting.shared_start",
                        arguments={
                            "workflow_path": workflow,
                            "shared_input_dir": dirs[0],
                            "shared_output_dir": dirs[1],
                            "notify_interval_seconds": 60,
                        },
                    )
                ]
        if any(kw in text for kw in ("共享盘", "公共盘", "公共机共享")) and any(kw in text for kw in ("进度", "跑到", "状态", "哪里了")):
            match = re.search(r"(SMAT_[A-Fa-f0-9]{12})", text)
            return [ToolCall(skill="matting.shared_status", arguments={"run_id": match.group(1) if match else None})]
        if any(kw in text for kw in ("共享盘", "公共盘", "公共机共享")) and any(kw in text for kw in ("同步输出", "同步回去", "同步结果")):
            match = re.search(r"(SMAT_[A-Fa-f0-9]{12})", text)
            if match:
                return [ToolCall(skill="matting.shared_sync_outputs", arguments={"run_id": match.group(1)})]
        if any(kw in text for kw in ("进程", "进程状态")):
            return [ToolCall(skill="system.process_status", arguments={})]
        if any(kw in text for kw in ("允许访问", "哪些盘", "工作盘", "允许的磁盘")):
            return [ToolCall(skill="workspace.roots", arguments={})]
        if any(kw in text for kw in ("磁盘空间", "盘空间", "剩余空间", "可用空间")):
            return [ToolCall(skill="workspace.disk_usage", arguments={})]

        if "复制" in text or "copy" in lowered:
            tree_match = re.search(r"把\s*(.+?)\s*(?:目录|文件夹)?\s*复制到\s*(.+)$", text)
            if tree_match and any(word in text for word in ("目录", "文件夹")):
                return [
                    ToolCall(
                        skill="file.copy_tree",
                        arguments={
                            "src_path": tree_match.group(1).strip().strip('"'),
                            "dst_path": tree_match.group(2).strip().strip('"'),
                            "overwrite": False,
                        },
                    )
                ]
            same_dir_match = re.search(r"把\s*(.+?)\s*(?:复制一份|复制).*?(?:改名为|命名为|叫)\s*(\S+)", text)
            if same_dir_match:
                return [
                    ToolCall(
                        skill="file.copy_as",
                        arguments={
                            "src_path": same_dir_match.group(1).strip().strip('"'),
                            "new_name": same_dir_match.group(2).strip().strip('"'),
                            "overwrite": False,
                        },
                    )
                ]
            match = re.search(r"把\s*(.+?)\s*复制到\s*(.+)$", text)
            if not match:
                match = re.search(r"copy\s+(.+?)\s+to\s+(.+)$", text, re.IGNORECASE)
            if match:
                return [
                    ToolCall(
                        skill="file.copy",
                        arguments={
                            "src_path": match.group(1).strip().strip('"'),
                            "dst_path": match.group(2).strip().strip('"'),
                            "overwrite": False,
                        },
                    )
                ]
        if ("图片" in text or "image" in lowered) and any(kw in text for kw in ("列", "查看", "看看", "找")):
            path = "E:\\"
            match = re.search(r"([A-Za-z]:\\[^\s，。]*)", text)
            if match:
                path = match.group(1)
            return [ToolCall(skill="image.list", arguments={"path": path, "recursive": False, "max_results": 50})]
        if any(kw in text for kw in ("发给我", "发送给我", "传给我", "通过飞书发")):
            match = re.search(r"([A-Za-z]:\\[^\s，。]*)", text)
            if match:
                skill = "feishu.send_image" if any(kw in text for kw in ("图片形式", "预览", "展示", "直接显示")) else "feishu.send_file"
                return [ToolCall(skill=skill, arguments={"path": match.group(1)})]
            name_match = re.search(r"([\w.\-]+(?:\.\.\.|…)[\w.\-]+|[\w.\-]+\.(?:png|jpg|jpeg|webp|bmp|gif|zip|txt|md|csv|xlsx|psd))", text, re.IGNORECASE)
            if name_match:
                search_root = "E:\\" if "e盘" in text.lower() or "e 盘" in text.lower() else None
                skill = "feishu.send_image_by_name" if any(kw in text for kw in ("图片形式", "预览", "展示", "直接显示")) else "feishu.send_file_by_name"
                return [
                    ToolCall(
                        skill=skill,
                        arguments={"name_pattern": name_match.group(1), "search_root": search_root},
                    )
                ]
        if "移动" in text or "重命名" in text or "move" in lowered or "rename" in lowered:
            match = re.search(r"把\s*(.+?)\s*(?:移动|重命名)到\s*(.+)$", text)
            if not match:
                match = re.search(r"(?:move|rename)\s+(.+?)\s+to\s+(.+)$", text, re.IGNORECASE)
            if match:
                return [
                    ToolCall(
                        skill="file.move",
                        arguments={
                            "src_path": match.group(1).strip().strip('"'),
                            "dst_path": match.group(2).strip().strip('"'),
                            "overwrite": False,
                        },
                    )
                ]
        if "删除" in text or "delete" in lowered:
            match = re.search(r"([A-Za-z]:\\[^\s，。]*)", text)
            if match:
                return [ToolCall(skill="file.delete", arguments={"path": match.group(1), "recursive": "目录" in text or "文件夹" in text})]
        if "清空" in text and ("目录" in text or "文件夹" in text):
            match = re.search(r"([A-Za-z]:\\[^\s，。]*)", text)
            if match:
                return [ToolCall(skill="file.empty_dir", arguments={"path": match.group(1)})]
        if "sha256" in lowered or "md5" in lowered or "hash" in lowered:
            match = re.search(r"([A-Za-z]:\\[^\s，。]*)", text)
            if match:
                algorithm = "md5" if "md5" in lowered else "sha256"
                return [ToolCall(skill="file.hash", arguments={"path": match.group(1), "algorithm": algorithm})]
        if any(kw in text for kw in ("搜索文本", "搜索内容", "查找文本", "查找内容", "包含")):
            path_match = re.search(r"([A-Za-z]:\\[^\s，。]*)", text)
            query_match = re.search(r"(?:搜索|查找|包含)\s*['\"“”]?([^'\"“”，。]+)", text)
            if path_match and query_match:
                return [ToolCall(skill="file.search_text", arguments={"path": path_match.group(1), "query": query_match.group(1).strip()})]
        if any(kw in text for kw in ("统计", "数一下", "多少图片", "多少文件")):
            match = re.search(r"([A-Za-z]:\\[^\s，。]*)", text)
            if match:
                return [ToolCall(skill="file.count", arguments={"path": match.group(1), "recursive": True})]
        if any(kw in text for kw in ("导出清单", "文件清单", "manifest")):
            paths = re.findall(r"([A-Za-z]:\\[^\s，。]*)", text)
            if len(paths) >= 2:
                fmt = "csv" if paths[1].lower().endswith(".csv") or "csv" in lowered else "json"
                return [ToolCall(skill="file.manifest", arguments={"path": paths[0], "output_path": paths[1], "format": fmt})]
        if any(kw in text for kw in ("zip里面", "zip 里面", "压缩包里面", "查看压缩包")):
            match = re.search(r"([A-Za-z]:\\[^\s，。]*\.zip)", text, re.IGNORECASE)
            if match:
                return [ToolCall(skill="archive.list", arguments={"path": match.group(1)})]
        if any(kw in text for kw in ("csv", "表格")) and any(kw in text for kw in ("预览", "有哪些列", "前几行", "看一下", "查看")):
            match = re.search(r"([A-Za-z]:\\[^\s，。]*\.(?:csv|tsv))", text, re.IGNORECASE)
            if match:
                return [ToolCall(skill="csv.summary", arguments={"path": match.group(1)})]
        if "json" in lowered and any(kw in text for kw in ("查看", "读取", "查询", "内容")):
            match = re.search(r"([A-Za-z]:\\[^\s，。]*\.json)", text, re.IGNORECASE)
            if match:
                return [ToolCall(skill="json.query", arguments={"path": match.group(1)})]
        if any(kw in text for kw in ("预览", "前几行", "最后几行", "tail", "看一下")):
            match = re.search(r"([A-Za-z]:\\[^\s，。]*)", text)
            if match:
                path_text = match.group(1)
                looks_like_file = bool(re.search(r"\.[A-Za-z0-9]{1,8}$", path_text))
                preview_intent = any(kw in text for kw in ("预览", "前几行", "最后几行", "tail", "日志", "文本", "文件"))
                if looks_like_file or preview_intent:
                    return [ToolCall(skill="file.preview", arguments={"path": path_text, "tail": any(kw in text for kw in ("最后", "tail"))})]
        if any(kw in text for kw in ("读取", "查看文本", "读一下")):
            match = re.search(r"([A-Za-z]:\\[^\s，。]*)", text)
            if match:
                return [ToolCall(skill="file.read_text", arguments={"path": match.group(1), "max_chars": 8000})]
        if "创建目录" in text or "新建目录" in text or "mkdir" in lowered:
            match = re.search(r"([A-Za-z]:\\[^\s，。]*)", text)
            if match:
                return [ToolCall(skill="file.mkdir", arguments={"path": match.group(1)})]
        if "是否存在" in text or "存在吗" in text or "exists" in lowered:
            match = re.search(r"([A-Za-z]:\\[^\s，。]*)", text)
            if match:
                return [ToolCall(skill="file.exists", arguments={"path": match.group(1)})]
        if "看看" in text or "列" in text or "list" in lowered:
            path = "E:\\"
            match = re.search(r"((?:[A-Za-z]:|\\\\)[^\s，。]*)", text)
            if match:
                path = match.group(1)
            return [ToolCall(skill="file.list_allowed", arguments={"path": path})]
        return []


def _frame_args_from_paths(paths: list[str], text: str) -> dict:
    args: dict = {}
    if paths:
        args["download_dir"] = paths[0]
    if len(paths) >= 2:
        args["export_dir"] = paths[1]
    fps_match = re.search(r"fps\s*[=:：]?\s*(\d+)|(\d+)\s*帧", text, re.IGNORECASE)
    if fps_match:
        args["fps"] = int(fps_match.group(1) or fps_match.group(2))
    threshold_match = re.search(r"(?:阈值|threshold|diff)[^\d]*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if threshold_match:
        args["diff_threshold"] = float(threshold_match.group(1))
    return args


def _pipeline_args_from_paths(paths: list[str]) -> dict:
    args: dict = {}
    keys = ("input_dir", "frame_output_dir", "matte_output_dir", "smooth_output_dir")
    dirs = [path for path in paths if not path.lower().endswith(".json")]
    for key, path in zip(keys, dirs):
        args[key] = path
    workflow = next((path for path in paths if path.lower().endswith(".json")), None)
    if workflow:
        args["workflow_path"] = workflow
    return args
