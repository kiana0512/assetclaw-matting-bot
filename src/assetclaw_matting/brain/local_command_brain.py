from __future__ import annotations

import re

from assetclaw_matting.brain.base import BrainProvider
from assetclaw_matting.brain.conversation_recall import answer_recent_question
from assetclaw_matting.brain.file_task_planner import plan_file_task
from assetclaw_matting.brain.result_formatter import format_skill_results
from assetclaw_matting.brain.schemas import BrainMessage, BrainResponse, ToolCall


class LocalCommandBrain(BrainProvider):
    name = "local_command"

    def handle_message(self, message: BrainMessage) -> BrainResponse:
        text = message.text.strip()
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
        if any(kw in lowered for kw in ("nvidia-smi", "gpu")) or any(kw in text for kw in ("显卡", "显存", "gpu", "GPU")):
            return [ToolCall(skill="system.gpu_status", arguments={})]
        if "comfyui" in lowered and any(kw in text for kw in ("状态", "在线", "进程", "使用情况")):
            return [ToolCall(skill="comfyui.status", arguments={})]
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
                return [ToolCall(skill="feishu.send_file", arguments={"path": match.group(1)})]
            name_match = re.search(r"([\w.\-]+(?:\.\.\.|…)[\w.\-]+|[\w.\-]+\.(?:png|jpg|jpeg|webp|bmp|gif|zip|txt|md|csv|xlsx|psd))", text, re.IGNORECASE)
            if name_match:
                search_root = "E:\\" if "e盘" in text.lower() or "e 盘" in text.lower() else None
                return [
                    ToolCall(
                        skill="feishu.send_file_by_name",
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
            match = re.search(r"([A-Za-z]:\\[^\s，。]*)", text)
            if match:
                path = match.group(1)
            return [ToolCall(skill="file.list_allowed", arguments={"path": path})]
        return []
