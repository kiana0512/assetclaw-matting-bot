"""Tool registry: maps tool names to implementations and schemas."""
from __future__ import annotations

from assetclaw_matting.agent.tool_schemas import TOOL_SCHEMAS
from assetclaw_matting.agent.tools import TOOL_REGISTRY


def get_tool_schemas() -> list[dict]:
    return TOOL_SCHEMAS


def call_tool(name: str, args: dict) -> object:
    """Call a registered tool by name. Raises KeyError if not found."""
    fn = TOOL_REGISTRY.get(name)
    if fn is None:
        raise KeyError(f"Tool '{name}' is not registered. Allowed: {list(TOOL_REGISTRY)}")
    return fn(**args)


def is_registered(name: str) -> bool:
    return name in TOOL_REGISTRY
