from __future__ import annotations

import re

from assetclaw_matting.brain.base import BrainProvider
from assetclaw_matting.brain.emotion_planner import plan_emotional_reply
from assetclaw_matting.brain.pre_llm_router import handle_pre_llm_message
from assetclaw_matting.brain.result_formatter import format_skill_results
from assetclaw_matting.brain.schemas import BrainMessage, BrainResponse, ToolCall
from tools.p4_assistant.nl_intent import parse_intent
from tools.p4_assistant.workspace_registry import WorkspaceRegistry


class LocalCommandBrain(BrainProvider):
    name = "local_command"

    def handle_message(self, message: BrainMessage) -> BrainResponse:
        text = message.text.strip()
        pre_llm_response = handle_pre_llm_message(self, message)
        if pre_llm_response:
            return pre_llm_response

        tool_calls = self._infer_tool_calls(text)
        if not tool_calls:
            emotional_reply = plan_emotional_reply(text)
            if emotional_reply:
                response = BrainResponse(text=emotional_reply, provider=self.name)
                self.log_message(message, response)
                return response
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
        if any(
            kw in text
            for kw in (
                "你会做什么",
                "你会做啥",
                "你会啥",
                "你能做什么",
                "你能做啥",
                "你可以做什么",
                "你可以做啥",
                "你会干啥",
                "你能干嘛",
                "你能干啥",
                "你可以干嘛",
                "你可以干啥",
                "会做啥",
                "你有什么用",
                "你能帮我什么",
                "你能陪我做什么",
                "帮助",
                "怎么用",
                "使用说明",
            )
        ) or lowered in ("help", "帮助", "what can you do"):
            return [ToolCall(skill="bot.help", arguments={})]
        if any(kw in text for kw in ("技能列表", "查看技能", "skill list")):
            return [ToolCall(skill="bot.skills", arguments={})]
        if any(kw in text for kw in ("权限说明", "安全边界", "查看权限", "查看安全")):
            return [ToolCall(skill="bot.permissions", arguments={})]
        if any(kw in text for kw in ("系统状态", "当前状态", "bot status")) or "查看状态" in text:
            return [ToolCall(skill="bot.status", arguments={})]
        if any(kw in text for kw in ("最近错误", "查看错误", "recent errors", "错误记录")):
            return [ToolCall(skill="bot.errors", arguments={})]
        p4_calls = _p4_tool_calls_from_text(text)
        if p4_calls:
            return p4_calls
        if any(
            kw in text
            for kw in (
                "诊断",
                "卡在哪里",
                "为什么没开始",
                "为什么这个没开始",
                "哪里卡住",
                "自己判断",
                "帮我判断",
                "你看看现在什么情况",
                "现在什么情况",
            )
        ):
            return [ToolCall(skill="agent.diagnose", arguments={})]
        wants_gpu = any(kw in lowered for kw in ("nvidia-smi", "gpu")) or any(kw in text for kw in ("显卡", "显存", "GPU"))
        wants_current_work = any(kw in text for kw in ("现在机器在跑什么", "当前所有任务", "当前执行现场", "现在有哪些活", "现在在跑什么", "当前任务"))
        if wants_gpu and wants_current_work:
            return [ToolCall(skill="system.gpu_status", arguments={}), ToolCall(skill="agent.current_work", arguments={"include_gpu": False})]
        if any(kw in text for kw in ("现在机器在跑什么", "当前所有任务", "当前执行现场", "现在有哪些活", "现在在跑什么")):
            return [ToolCall(skill="agent.current_work", arguments={})]
        if any(kw in text for kw in ("动画处理进度", "视频处理进度", "直传视频进度", "这个视频处理到哪", "这个动画处理到哪")):
            match = re.search(r"(VID_[A-Fa-f0-9]{12})", text)
            return [ToolCall(skill="direct_video.status", arguments={"run_id": match.group(1) if match else None})]
        if any(kw in text for kw in ("视频处理任务", "直传视频任务")) and any(kw in text for kw in ("有哪些", "列表", "列出", "当前任务")):
            return [ToolCall(skill="direct_video.list", arguments={"include_finished": any(kw in text for kw in ("全部", "历史", "已结束"))})]
        if any(kw in text for kw in ("取消视频处理", "停止视频处理", "取消直传视频", "停止直传视频")):
            match = re.search(r"(VID_[A-Fa-f0-9]{12})", text)
            return [ToolCall(skill="direct_video.cancel", arguments={"run_id": match.group(1) if match else None})]
        if any(kw in text for kw in ("表情包状态", "情绪回复配置", "表情包池", "sticker status")):
            return [ToolCall(skill="sticker.info", arguments={})]
        if any(kw in text for kw in ("随机发个表情包", "发个表情包", "来个表情包", "发个 sticker", "发个sticker")):
            return [ToolCall(skill="sticker.send_random", arguments={})]
        url_match = re.search(r"https?://[^\s，。]+", text)
        if url_match and any(kw in text for kw in ("网页", "网站", "url", "URL", "链接", "读取", "看一下", "总结", "内容")):
            return [ToolCall(skill="web.fetch_url", arguments={"url": url_match.group(0)})]
        web_query = _web_query_from_text(text)
        if web_query:
            skill = "web.research" if _wants_web_research(text) else "web.search"
            return [ToolCall(skill=skill, arguments={"query": web_query})]
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
        if (
            "atlassizereport" in lowered
            or "spriteatlas" in lowered
            or "预打图集" in text
            or ("图集" in text and any(kw in text for kw in ("大小", "统计", "报告", "检查", "生成", "资源大小")))
        ):
            args: dict = {}
            paths = re.findall(r"([A-Za-z]:\\[^\s，。]*)", text)
            if paths:
                args["unity_project"] = paths[0]
            if any(kw in text for kw in ("状态", "查看", "看看", "读", "显示")) and not any(kw in text for kw in ("生成", "跑", "执行", "检查", "统计")):
                return [ToolCall(skill="unity_tools.atlas_status", arguments=args)]
            return [ToolCall(skill="unity_tools.atlas_report", arguments=args)]
        if (
            "animtexturebatchrename" in lowered
            or "动画贴图批量重命名" in text
            or "贴图命名整理" in text
            or ("重命名" in text and "动画贴图" in text)
            or ("命名整理" in text and "贴图" in text)
        ):
            paths = _unity_tool_paths_from_text(text)
            args: dict = {}
            if len(paths) >= 1:
                args["texture_folder"] = paths[0]
            if len(paths) >= 2:
                args["animation_folder"] = paths[1]
            if any(kw in text for kw in ("预览", "看看", "检查", "扫描")):
                return [ToolCall(skill="unity_tools.rename_preview", arguments=args)]
            return [ToolCall(skill="unity_tools.rename_run", arguments=args)]
        unity_import_single_step = (
            ("unity" in lowered or "unity_ready" in lowered or "插件导入" in text or "进引擎" in text)
            and any(kw in text for kw in ("导入", "迭代", "替换", "高清化", "进引擎", "状态", "预览", "检查"))
            and not any(kw in text for kw in ("流程", "自动化", "6步", "六步", "7步", "七步"))
        )
        if unity_import_single_step:
            paths = re.findall(r"([A-Za-z]:\\[^\s，。]*)", text)
            args: dict = {}
            if paths:
                args["unity_ready"] = paths[0]
            mode = _unity_import_mode_from_text(text)
            if mode:
                args["mode"] = mode
            if any(kw in text for kw in ("状态", "进度")):
                return [ToolCall(skill="unity_import.status", arguments=args)]
            if any(kw in text for kw in ("预览", "看看", "检查")):
                return [ToolCall(skill="unity_import.preview", arguments=args)]
            return [ToolCall(skill="unity_import.run", arguments=args)]
        animation_flow_paths = re.findall(r"([A-Za-z]:\\[^\s，。]*)", text)
        animation_flow_date_root = _animation_flow_date_root_from_text(text)
        is_full_animation_flow = (
            "动画自动化流程" in text
            or "动画流程自动机" in text
            or "启动动画流程" in text
            or "开始动画流程" in text
            or "动画自动化" in text and _animation_flow_date_from_text(text)
            or "自动化流程" in text
            or "完整动画流程" in text
            or "完整动画自动化" in text
            or "6步动画" in text
            or "六步动画" in text
            or "7步动画" in text
            or "七步动画" in text
            or re.search(r"AFLOW_[A-Fa-f0-9]{12}", text) is not None
            or "unity_ready" in lowered
            or ("unity" in lowered and "p4" in lowered)
        )
        if is_full_animation_flow and any(kw in text for kw in ("哪些任务", "任务列表", "当前任务", "有哪些任务")):
            return [ToolCall(skill="animation_flow.list", arguments={"include_finished": any(kw in text for kw in ("全部", "历史", "已结束"))})]
        if is_full_animation_flow and any(kw in text for kw in ("终止", "取消", "停止")):
            match = re.search(r"(AFLOW_[A-Fa-f0-9]{12})", text)
            return [ToolCall(skill="animation_flow.cancel", arguments={"run_id": match.group(1) if match else None})]
        if is_full_animation_flow and any(kw in text for kw in ("进度", "状态", "哪里了", "跑到")):
            match = re.search(r"(AFLOW_[A-Fa-f0-9]{12})", text)
            return [ToolCall(skill="animation_flow.status", arguments={"run_id": match.group(1) if match else None})]
        if is_full_animation_flow and any(kw in text for kw in ("继续", "恢复", "接着跑", "继续跑", "从p4继续", "从 P4 继续", "继续p4", "继续 P4")):
            match = re.search(r"(AFLOW_[A-Fa-f0-9]{12})", text)
            return [ToolCall(skill="animation_flow.resume", arguments={"run_id": match.group(1) if match else None})]
        if is_full_animation_flow and any(kw in text for kw in ("预览", "看看", "检查", "计划")):
            paths = animation_flow_paths
            args = {"date_root": paths[0] if paths else animation_flow_date_root} if paths or animation_flow_date_root else {}
            mode = _unity_import_mode_from_text(text)
            if mode:
                args["unity_import_mode"] = mode
            priority_characters = _priority_characters_from_text(text)
            if priority_characters:
                args["priority_characters"] = priority_characters
            if _fake_matting_from_text(text):
                args["fake_matting_from_frames"] = True
            return [ToolCall(skill="animation_flow.preview", arguments=args)]
        if is_full_animation_flow and any(kw in text for kw in ("启动", "开始", "执行", "跑")):
            paths = animation_flow_paths
            args = {"date_root": paths[0] if paths else animation_flow_date_root} if paths or animation_flow_date_root else {}
            mode = _unity_import_mode_from_text(text)
            if mode:
                args["unity_import_mode"] = mode
            priority_characters = _priority_characters_from_text(text)
            if priority_characters:
                args["priority_characters"] = priority_characters
            if _fake_matting_from_text(text):
                args["fake_matting_from_frames"] = True
            return [ToolCall(skill="animation_flow.start", arguments=args)]
        if is_full_animation_flow and (_unity_import_mode_from_text(text) or animation_flow_date_root):
            paths = animation_flow_paths
            args = {"date_root": paths[0] if paths else animation_flow_date_root} if paths or animation_flow_date_root else {}
            mode = _unity_import_mode_from_text(text)
            if mode:
                args["unity_import_mode"] = mode
            priority_characters = _priority_characters_from_text(text)
            if priority_characters:
                args["priority_characters"] = priority_characters
            if _fake_matting_from_text(text):
                args["fake_matting_from_frames"] = True
            return [ToolCall(skill="animation_flow.start", arguments=args)]
        animation_root = _animation_root_from_text(text)
        is_animation_ops = (
            "animation_automation" in lowered
            or "动画自动化" in text
            or "动画流程" in text
            or "帧的问题" in text
            or ("matte" in lowered and "smooth" in lowered)
        )
        if is_animation_ops and any(kw in text for kw in ("状态", "数量", "多少帧", "多少张", "对齐", "检查", "盘点", "进度")):
            return [ToolCall(skill="animation.status", arguments={"root": animation_root})]
        if is_animation_ops and any(kw in text for kw in ("全部重做", "全量重做", "重新抽", "重抽", "重跑", "从 videos", "从videos")):
            return [ToolCall(skill="animation.rerun_from_videos", arguments={"root": animation_root, "fps": _fps_from_text(text) or 24})]
        if (
            (is_animation_ops or "平滑" in text)
            and any(kw in text for kw in ("再做一次平滑", "手动做一下平滑", "重新平滑", "再平滑", "当前 matte", "基于当前", "最新的平滑"))
        ):
            paths = re.findall(r"([A-Za-z]:\\[^\s，。]*)", text)
            args: dict = {"root": animation_root, "skip_existing": any(kw in text for kw in ("跳过已有", "不覆盖", "补跑"))}
            if len(paths) >= 2:
                args["input_dir"] = paths[0]
                args["output_dir"] = paths[1]
            elif paths and paths[0].lower().rstrip("\\").endswith("\\matte"):
                args["input_dir"] = paths[0]
                args["output_dir"] = str(paths[0].rstrip("\\")[:-5] + "smooth")
            return [ToolCall(skill="animation.manual_smooth_current", arguments=args)]
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
                skip_existing = any(kw in text for kw in ("跳过已有", "跳过已存在", "不要覆盖", "不覆盖", "只处理没做的", "只处理新的", "补跑"))
                return [
                    ToolCall(
                        skill="cherry.run_start",
                        arguments={
                            "input_dir": paths[0],
                            "output_dir": paths[1] if len(paths) >= 2 else "E:\\cherry_output",
                            "recursive": True,
                            "skip_existing": skip_existing,
                            "notify_interval_seconds": 60,
                            "use_smooth": (
                                any(kw in text for kw in ("开启时序", "启用时序", "做时序", "时序平滑", "temporal smooth"))
                                and not any(kw in text for kw in ("不做时序", "关闭时序", "不要时序", "不做这个时序", "without temporal", "no temporal"))
                            ),
                        },
                    )
                ]
        comfy_match = re.search(r"(COMFY_[A-Fa-f0-9]{12})", text)
        if comfy_match and any(kw in text for kw in ("终止", "取消", "停止", "别跑了")):
            return [ToolCall(skill="comfyui.run_cancel", arguments={"run_id": comfy_match.group(1), "interrupt_current": True})]
        if any(kw in text for kw in ("为什么没开始", "没开始", "开始这个任务", "继续这个任务", "继续跑", "恢复", "接着跑", "拉起")) and (
            comfy_match or "comfyui" in lowered or "抠图" in text or "任务" in text or "这个" in text
        ):
            return [ToolCall(skill="comfyui.run_resume", arguments={"run_id": comfy_match.group(1) if comfy_match else None})]
        if any(kw in text for kw in ("开始抠图", "启动抠图", "继续抠图")) and not re.findall(r"([A-Za-z]:\\[^\s，。]*)", text):
            return [ToolCall(skill="comfyui.run_resume", arguments={})]
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
            if any(kw in text for kw in ("开始抠图", "启动抠图", "继续抠图")):
                return [ToolCall(skill="comfyui.run_resume", arguments={})]
            if any(kw in text for kw in ("批量抠图", "抠图管线")):
                return [ToolCall(skill="comfyui.workflows", arguments={})]
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


def _unity_tool_paths_from_text(text: str) -> list[str]:
    paths = re.findall(r"([A-Za-z]:\\[^\s，。]*)", text)
    asset_paths = re.findall(r"(Assets/[^\s，。]+)", text)
    combined: list[str] = []
    for path in paths + asset_paths:
        clean = path.rstrip("，。,.；;")
        if clean not in combined:
            combined.append(clean)
    return combined


def _unity_import_mode_from_text(text: str) -> str | None:
    lowered = text.lower()
    if any(kw in text for kw in ("迭代", "资源迭代", "替换", "贴图迭代", "高清化", "直接替换")) or any(
        kw in lowered for kw in ("iteration", "iterate", "replace", "update")
    ):
        return "iteration"
    if any(kw in text for kw in ("新导入", "批量导入", "导入")) or any(kw in lowered for kw in ("import", "new")):
        return "import"
    return None


def _fake_matting_from_text(text: str) -> bool:
    lowered = text.lower()
    return any(kw in lowered for kw in ("fake", "faker", "mock")) or any(
        kw in text for kw in ("模拟抠图", "假抠图", "跳过抠图", "抽帧当抠图", "抽帧直接当抠图", "抽帧作为抠图")
    )


def _priority_characters_from_text(text: str) -> list[str]:
    match = re.search(r"(?:优先|先跑|先扣|先抠|priority)\s*[:：]?\s*([A-Za-z0-9_\-\u4e00-\u9fff]+)", text, re.IGNORECASE)
    if not match:
        return []
    raw = match.group(1).strip(" ，。；;,.")
    if not raw:
        return []
    return [raw]


def _animation_flow_date_from_text(text: str) -> str | None:
    match = re.search(r"(?<!\d)(20\d{2})[-_/年.]?(\d{1,2})[-_/月.]?(\d{1,2})(?:日)?(?!\d)", text)
    if not match:
        return None
    year, month, day = match.groups()
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def _animation_flow_date_root_from_text(text: str) -> str | None:
    day = _animation_flow_date_from_text(text)
    return f"E:\\animation_automation\\{day}" if day else None


def _web_query_from_text(text: str) -> str | None:
    if re.search(r"https?://", text):
        return None
    compact = text.strip()
    if any(kw in compact for kw in ("搜索文本", "搜索内容", "查找文本", "查找内容")):
        return None
    if not any(kw in compact for kw in ("搜索", "搜一下", "搜搜", "查一下", "查查", "网上查", "联网查", "调研")):
        return None
    query = re.sub(r"^(帮我|你帮我|麻烦你|可以帮我|能不能帮我)?\s*", "", compact)
    query = re.sub(r"^(搜索一下|搜索|搜一下|搜搜|查一下|查查|网上查一下|网上查|联网查一下|联网查|调研一下|调研)\s*", "", query)
    query = re.sub(r"^(并)?(整理|整合|总结|归纳|对比)(一下|给我)?\s*", "", query)
    query = re.sub(r"(并)?(整理|整合|总结|归纳|对比)(一下|给我)?$", "", query).strip(" ：:，。")
    return query or None


def _wants_web_research(text: str) -> bool:
    return any(kw in text for kw in ("整合", "整理", "总结", "归纳", "对比", "调研", "资料", "来源", "结论"))


def _p4_tool_calls_from_text(text: str) -> list[ToolCall]:
    lowered = text.lower()
    if not any(kw in lowered or kw in text for kw in ("p4", "perforce", "depot", "changelist", "cl", "reconcile", "workspace", "工作区", "服务器", "差异", "拉最新", "改了什么", "shelve", "搁置", "飞书报告", "变更")):
        return []
    if any(kw in lowered or kw in text for kw in ("submit", "提交", "merge", "合流", "copy up", "stream 创建", "创建 stream", "sync", "拉最新", "同步")):
        return [ToolCall(skill="p4.help", arguments={})]
    if (any(kw in text for kw in ("功能", "会做什么", "帮助", "怎么用")) or "help" in lowered) and ("p4" in lowered or "perforce" in lowered):
        return [ToolCall(skill="p4.help", arguments={})]
    changelist = re.search(r"(?:changelist|cl|CL|变更)\s*#?\s*(\d+)|\b(\d{4,})\b", text, re.IGNORECASE)
    if any(kw in lowered or kw in text for kw in ("删除", "清理", "取消", "删掉", "删了", "作废", "不要了", "delete", "cleanup", "remove")) and changelist:
        return [ToolCall(skill="p4.cleanup_cl", arguments={"cl": changelist.group(1) or changelist.group(2)})]
    if any(kw in lowered or kw in text for kw in ("哪些 cl", "哪些cl", "有哪些 cl", "有哪些cl", "cl 的id", "cl id", "cl 列表", "cl列表", "changelist 列表", "pending cl", "shelved cl", "当前 cl", "当前cl")) or (
        "cl" in lowered and any(kw in text for kw in ("有哪些", "哪些", "列一下", "查看", "看看"))
    ):
        return [ToolCall(skill="p4.list_cls", arguments={})]
    if ("p4" in lowered or "perforce" in lowered) and any(kw in lowered or kw in text for kw in ("info", "connection", "verify", "status", "opened", "changes", "changed", "状态")):
        return [ToolCall(skill="p4.status", arguments={})]
    if any(kw in lowered or kw in text for kw in ("check", "检查", "安全检查")):
        return [ToolCall(skill="p4.check", arguments={})]
    if any(kw in lowered or kw in text for kw in ("preview", "预览", "reconcile -n")):
        return [ToolCall(skill="p4.preview", arguments={})]
    registry = WorkspaceRegistry()
    intent = parse_intent(text, registry)
    if intent.need_clarification:
        return [ToolCall(skill="p4.help", arguments={})]
    args: dict = {}
    if intent.workflow:
        args["workflow"] = intent.workflow
    if intent.workspace:
        args["workspace"] = intent.workspace
    if intent.paths:
        args["paths"] = list(intent.paths)
    mapping = {
        "status": "p4.status",
        "check": "p4.check",
        "preview": "p4.preview",
        "create_cl": "p4.create_cl",
        "reconcile": "p4.reconcile",
        "shelve": "p4.shelve",
        "report": "p4.report",
    }
    skill = mapping.get(intent.intent)
    if not skill:
        return []
    if skill in {"p4.reconcile", "p4.shelve", "p4.report"} and changelist:
        args["cl"] = changelist.group(1) or changelist.group(2)
    return [ToolCall(skill=skill, arguments=args)]


def _animation_root_from_text(text: str) -> str:
    paths = re.findall(r"([A-Za-z]:\\[^\s，。]*)", text)
    for raw in paths:
        normalized = raw.rstrip("\\/")
        parts = re.split(r"[\\/]+", normalized)
        lowered_parts = [part.lower() for part in parts]
        if "animation_automation" in lowered_parts:
            idx = lowered_parts.index("animation_automation")
            if len(parts) > idx + 1:
                return "\\".join(parts[: idx + 2])
        if parts and parts[-1].lower() in {"videos", "frames", "frames_missing_patch", "matte", "smooth"}:
            return "\\".join(parts[:-1])
    return r"E:\animation_automation\2026-06-02"


def _fps_from_text(text: str) -> int | None:
    match = re.search(r"(?:fps|帧率|每秒)\s*[=:：]?\s*(\d+)|(\d+)\s*fps|每秒\s*(\d+)\s*帧", text, re.IGNORECASE)
    if not match:
        return None
    value = next((group for group in match.groups() if group), None)
    if not value:
        return None
    return int(value)
