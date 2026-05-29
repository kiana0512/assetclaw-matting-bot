"""MCP tool definitions derived from the Skill Registry.

Tools and skills share the same implementation — no duplicate logic.
"""
from __future__ import annotations

from typing import Any

from assetclaw_matting.mcp_server.schemas import MCPToolDefinition, MCPToolResult

# Subset of skills exposed as MCP tools
_MCP_SKILL_NAMES = [
    "batch.create",
    "batch.start",
    "batch.status",
    "batch.list",
    "queue.status",
    "comfyui.status",
    "worker.status",
    "file.list_allowed",
    "log.tail",
]

# Minimal JSON schema for each exposed tool
_INPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "batch.create": {
        "type": "object",
        "properties": {
            "input_dir":   {"type": "string", "description": "Absolute path to input image directory"},
            "output_dir":  {"type": "string", "description": "Absolute path to output directory"},
            "workflow_type": {"type": "string", "default": "matting_v1"},
            "notify_chat_id": {"type": "string"},
            "note": {"type": "string"},
        },
        "required": ["input_dir", "output_dir"],
    },
    "batch.start":  {"type": "object", "properties": {"batch_id": {"type": "string"}}, "required": ["batch_id"]},
    "batch.status": {"type": "object", "properties": {"batch_id": {"type": "string"}}, "required": ["batch_id"]},
    "batch.list":   {"type": "object", "properties": {"limit": {"type": "integer", "default": 10}}},
    "queue.status":    {"type": "object", "properties": {}},
    "comfyui.status":  {"type": "object", "properties": {}},
    "worker.status":   {"type": "object", "properties": {}},
    "file.list_allowed": {
        "type": "object",
        "properties": {
            "path":      {"type": "string"},
            "max_items": {"type": "integer", "default": 100},
        },
        "required": ["path"],
    },
    "log.tail": {
        "type": "object",
        "properties": {
            "log_name": {"type": "string", "enum": ["gateway", "worker", "app"], "default": "gateway"},
            "lines":    {"type": "integer", "default": 50},
        },
    },
}


def list_tools() -> list[MCPToolDefinition]:
    """Return MCP tool definitions for all exposed skills."""
    from assetclaw_matting.skills.registry import _SKILL_MAP
    tools = []
    for name in _MCP_SKILL_NAMES:
        skill = _SKILL_MAP.get(name)
        if skill:
            tools.append(MCPToolDefinition(
                name=name.replace(".", "_"),  # MCP uses underscore names
                description=skill["description"],
                inputSchema=_INPUT_SCHEMAS.get(name, {"type": "object", "properties": {}}),
            ))
    return tools


def call_tool(tool_name: str, arguments: dict[str, Any]) -> MCPToolResult:
    """Call a skill and return MCP-format result."""
    # Convert underscore name back to dot name
    skill_name = tool_name.replace("_", ".", 1) if "_" in tool_name else tool_name

    from assetclaw_matting.skills.registry import call_skill
    result = call_skill(skill_name, arguments, requested_by="mcp")

    if result.get("ok"):
        content = [{"type": "text", "text": str(result.get("result", result.get("message", "ok")))}]
        return MCPToolResult(content=content, isError=False)
    else:
        content = [{"type": "text", "text": f"Error: {result.get('error', 'unknown')}"}]
        return MCPToolResult(content=content, isError=True)
