from __future__ import annotations

import uuid
from typing import Optional


class ErrorEnvelope:
    """Structured error carrier for the full feishu->brain->skill pipeline."""

    __slots__ = (
        "ok", "trace_id", "phase", "error_type", "error_message",
        "user_message", "suggestion", "detail_for_log", "skill_name", "raw_exception",
    )

    def __init__(
        self,
        phase: str,
        error_type: str,
        error_message: str,
        user_message: str = "",
        suggestion: str = "",
        detail_for_log: str = "",
        skill_name: Optional[str] = None,
        raw_exception: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> None:
        self.ok = False
        self.trace_id = trace_id or uuid.uuid4().hex[:12]
        self.phase = phase
        self.error_type = error_type
        self.error_message = error_message
        self.user_message = user_message or error_message
        self.suggestion = suggestion
        self.detail_for_log = detail_for_log or error_message
        self.skill_name = skill_name
        self.raw_exception = raw_exception

    def to_feishu_text(self) -> str:
        lines = ["执行失败", ""]
        lines.append(f"阶段：{self.phase}")
        if self.skill_name:
            lines.append(f"动作：{self.skill_name}")
        lines.append(f"错误类型：{self.error_type}")
        lines.append(f"说明：{self.user_message}")
        lines.append(f"Trace ID：{self.trace_id}")
        if self.suggestion:
            lines.append("")
            lines.append("建议：")
            for i, item in enumerate(self.suggestion.strip().split("\n"), 1):
                if item.strip():
                    lines.append(f"{i}. {item.strip()}")
        return "\n".join(lines)

    def to_log_dict(self) -> dict:
        return {
            "ok": self.ok,
            "trace_id": self.trace_id,
            "phase": self.phase,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "user_message": self.user_message,
            "skill_name": self.skill_name,
            "detail_for_log": self.detail_for_log,
        }


def classify_exception(exc: Exception, phase: str, trace_id: str, skill_name: Optional[str] = None) -> ErrorEnvelope:
    from assetclaw_matting.skills.security import redact_secrets

    raw_msg = redact_secrets(str(exc))
    exc_type = type(exc).__name__

    if isinstance(exc, PermissionError):
        return ErrorEnvelope(
            phase=phase,
            error_type="PermissionDenied",
            error_message=raw_msg,
            user_message="这个路径不在允许访问范围内，或者命中了安全规则。",
            suggestion=(
                "检查路径是否在 E:\\ 允许工作区内\n"
                "不要访问 .env、.ssh、Windows、Program Files 等敏感目录\n"
                "可以发送「查看权限说明」了解当前机器人权限"
            ),
            detail_for_log=raw_msg,
            skill_name=skill_name,
            raw_exception=raw_msg,
            trace_id=trace_id,
        )

    if isinstance(exc, FileNotFoundError):
        return ErrorEnvelope(
            phase=phase,
            error_type="FileNotFound",
            error_message=raw_msg,
            user_message="指定的文件或目录不存在。",
            suggestion=(
                "检查路径拼写是否正确\n"
                "可以先发送「看看 E 盘文件」确认路径"
            ),
            detail_for_log=raw_msg,
            skill_name=skill_name,
            raw_exception=raw_msg,
            trace_id=trace_id,
        )

    if isinstance(exc, FileExistsError):
        return ErrorEnvelope(
            phase=phase,
            error_type="FileAlreadyExists",
            error_message=raw_msg,
            user_message="目标文件已存在。如需覆盖，请在命令中说明「允许覆盖」。",
            suggestion="命令中加入「覆盖」或 overwrite",
            detail_for_log=raw_msg,
            skill_name=skill_name,
            raw_exception=raw_msg,
            trace_id=trace_id,
        )

    if "401" in raw_msg or "unauthorized" in raw_msg.lower():
        return ErrorEnvelope(
            phase=phase,
            error_type="AuthenticationFailed",
            error_message=raw_msg,
            user_message="LLM Proxy 鉴权失败（401），请检查 API Key 是否正确。",
            suggestion=(
                "检查 .env 中 LLM_PROXY_API_KEY 是否有效\n"
                "确认 LLM_PROXY_BASE_URL 正确"
            ),
            detail_for_log=raw_msg,
            skill_name=skill_name,
            raw_exception=raw_msg,
            trace_id=trace_id,
        )

    return ErrorEnvelope(
        phase=phase,
        error_type=exc_type,
        error_message=raw_msg,
        user_message=f"内部错误（{exc_type}）：{raw_msg[:120]}",
        suggestion="可以发送「查看最近错误」或联系管理员",
        detail_for_log=raw_msg,
        skill_name=skill_name,
        raw_exception=raw_msg,
        trace_id=trace_id,
    )