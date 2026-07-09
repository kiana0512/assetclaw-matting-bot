"""Pluggable Brain Router for AssetClaw Win3090 Skill Node.

Supported providers:
  local_command  — hardcoded commands, always available
  llm_proxy      — OpenAI-compatible API (recommended default)
  arkclaw        — ArkClaw Enterprise Brain
  claude         — Anthropic Claude (stub, reserved)
  openai_agents  — OpenAI Agents (stub, reserved)
  langgraph      — LangGraph (stub, reserved)

Configure via BRAIN_PROVIDER in .env.
"""
