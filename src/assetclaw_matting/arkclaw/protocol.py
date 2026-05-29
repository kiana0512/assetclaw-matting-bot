"""ArkClaw API protocol constants.

Centralise all endpoint paths here so they can be updated without
touching business logic.
"""
from __future__ import annotations

# REST endpoint paths (relative to ARKCLAW_BASE_URL)
PATH_CHAT = "/api/v1/chat"
PATH_SKILL_RESULT = "/api/v1/skill_results"
PATH_EVENT = "/api/v1/events"
PATH_NODE_REGISTER = "/api/v1/nodes/register"
PATH_NODE_HEARTBEAT = "/api/v1/nodes/heartbeat"

# Security policy description sent to ArkClaw with every request
# so it knows the constraints this node operates under.
SECURITY_POLICY_SUMMARY = (
    "This Win3090 Skill Node operates under strict security constraints: "
    "(1) Only whitelisted skills can be called via /skills/v1/call. "
    "(2) All file operations are restricted to ALLOWED_ROOTS. "
    "(3) Shell execution is forbidden. "
    "(4) File deletion is forbidden. "
    "(5) Sensitive paths (.env, .ssh, Windows, AppData) are blocked. "
    "(6) All skill calls are logged to skill_calls audit table. "
    "(7) GPU is reserved exclusively for ComfyUI — no local LLM inference."
)

# Node type identifier sent to ArkClaw
NODE_TYPE = "win3090_skill_node"
NODE_VERSION = "0.2.0"
