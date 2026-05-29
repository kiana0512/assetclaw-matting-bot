"""Local Command Brain — deterministic hardcoded commands.

This is the always-available fallback when no external brain is configured.
"""
from __future__ import annotations

from assetclaw_matting.brain.base import BrainProvider
from assetclaw_matting.brain.schemas import BrainContext, BrainMessage, BrainResponse


_HELP_TEXT = (
    "AssetClaw Win3090 Skill Node — 本地命令：\n"
    "  help                      查看帮助\n"
    "  queue                     队列状态\n"
    "  batch list                最近批次\n"
    "  batch status <id>         批次详情\n"
    "  batch cancel <id>         取消批次\n"
    "  task status <id>          任务详情\n"
    "  comfyui status            ComfyUI 在线状态\n"
    "  worker status             Worker 状态\n"
    "\n"
    "批量抠图：先创建批次再启动：\n"
    "  python -m assetclaw_matting.cli.main batch-create ...\n"
    "  python -m assetclaw_matting.cli.main batch-start --batch-id BATCH_XXX\n"
    "\n"
    "如需 AI 助手，配置 BRAIN_PROVIDER=llm_proxy 并填入 LLM_PROXY_API_KEY。"
)


class LocalCommandBrain(BrainProvider):
    name = "local_command"

    def is_available(self) -> bool:
        return True

    def handle_message(
        self, message: BrainMessage, context: BrainContext
    ) -> BrainResponse:
        from assetclaw_matting.feishu.command_runner import execute_command, is_known_command

        text = message.text.strip()

        if not text:
            return BrainResponse(text="请输入命令。发送 help 查看可用命令。", provider=self.name)

        if is_known_command(text):
            reply = execute_command(text, message.conversation_id)
            if not reply:
                reply = "命令执行完成。"
        else:
            reply = (
                f"未识别命令：{text!r}\n\n"
                "当前使用本地命令模式（BRAIN_PROVIDER=local_command）。\n"
                "发送 help 查看可用命令，或配置 BRAIN_PROVIDER=llm_proxy 启用 AI 助手。"
            )

        response = BrainResponse(text=reply, provider=self.name)
        self._log_message(message, response)
        return response
