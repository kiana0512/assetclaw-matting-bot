"""Agent Harness — local adapter layer for cloud AI integration.

IMPORTANT DESIGN NOTES:
- This is NOT a local model runner. No GPU is used here.
- The real AI inference happens on the OpenClaw cloud API.
- This module bridges local command execution with cloud agent calls.
- When AGENT_ENABLED=false, only deterministic local commands are executed.
- When AGENT_ENABLED=true, messages are forwarded to the external LLM API.
"""
from __future__ import annotations

import logging
import json
from typing import Any

from assetclaw_matting.models.agent_models import AgentContext, AgentResult, ToolCall

log = logging.getLogger(__name__)


class AgentHarness:
    """Local adapter: routes messages to local commands or external LLM."""

    def handle_user_message(
        self,
        text: str,
        context: AgentContext,
    ) -> AgentResult:
        from assetclaw_matting.config import settings

        if not settings.agent_enabled:
            return self._deterministic(text, context)
        return self._llm_agent(text, context)

    def _deterministic(self, text: str, context: AgentContext) -> AgentResult:
        # Import from command_runner, not event_handler, to avoid circular imports
        from assetclaw_matting.feishu.command_runner import execute_command
        reply = execute_command(text, context.chat_id or "")
        if not reply:
            reply = "未知命令。发送 help 查看可用命令。"
        return AgentResult(reply=reply, used_llm=False)

    def _llm_agent(self, text: str, context: AgentContext) -> AgentResult:
        """Call external LLM API (not local GPU)."""
        from assetclaw_matting.agent import memory as mem_module
        from assetclaw_matting.agent.llm_client import llm_client
        from assetclaw_matting.agent.prompts import SYSTEM_PROMPT
        from assetclaw_matting.agent.tool_registry import call_tool, get_tool_schemas
        from assetclaw_matting.config import settings

        chat_id = context.chat_id or "__global__"
        mem_module.memory.add(chat_id, "user", text)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ] + mem_module.memory.get_messages(chat_id)

        tool_calls_log: list[ToolCall] = []
        max_rounds = settings.agent_max_tool_calls

        for _ in range(max_rounds + 1):
            try:
                response = llm_client.chat(
                    messages=messages,
                    tools=get_tool_schemas(),
                )
            except NotImplementedError as exc:
                return AgentResult(reply=f"Agent 模式未配置：{exc}", used_llm=True)
            except Exception as exc:
                log.exception("LLM call failed")
                return AgentResult(reply=f"LLM 调用失败：{exc}", used_llm=True)

            choice = response.get("choices", [{}])[0]
            msg = choice.get("message", {})
            finish_reason = choice.get("finish_reason", "stop")
            messages.append(msg)

            if finish_reason == "tool_calls" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {})
                    tool_name = fn.get("name", "")
                    try:
                        args = json.loads(fn.get("arguments", "{}"))
                        result = call_tool(tool_name, args)
                        result_str = json.dumps(result, ensure_ascii=False)
                        tool_calls_log.append(ToolCall(tool=tool_name, args=args, result=result))
                    except Exception as exc:
                        result_str = json.dumps({"error": str(exc)})
                        tool_calls_log.append(ToolCall(tool=tool_name, args={}, error=str(exc)))
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": result_str,
                    })
            else:
                reply = msg.get("content", "")
                mem_module.memory.add(chat_id, "assistant", reply)
                return AgentResult(reply=reply, tool_calls=tool_calls_log, used_llm=True)

        return AgentResult(
            reply="已达到最大工具调用次数，请简化您的请求。",
            tool_calls=tool_calls_log,
            used_llm=True,
        )


harness = AgentHarness()
