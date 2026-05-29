"""MCP-compatible schema types."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class MCPToolDefinition(BaseModel):
    name: str
    description: str
    inputSchema: dict[str, Any]


class MCPToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = {}


class MCPToolResult(BaseModel):
    content: list[dict[str, Any]]
    isError: bool = False


class MCPServerInfo(BaseModel):
    name: str = "assetclaw-win3090-skill-node"
    version: str = "0.3.0"
    protocolVersion: str = "2024-11-05"
    description: str = "AssetClaw Win3090 Skill Node — MCP tool server"
