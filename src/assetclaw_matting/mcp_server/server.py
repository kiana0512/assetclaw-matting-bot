"""MCP HTTP API routes.

Exposed at /mcp/* — MCP-compatible HTTP endpoints for AI tools
(Claude, OpenAI Agents, Cursor, etc.)

Note: This is an HTTP-based MCP implementation.
For stdio MCP (used by Claude Desktop), see docs/MCP_COMPATIBILITY.md.
All logic delegates to skills.registry — no duplicate business logic.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse

from assetclaw_matting.mcp_server.schemas import MCPServerInfo, MCPToolCall

log = logging.getLogger(__name__)
router = APIRouter(prefix="/mcp", tags=["mcp"])


def _verify_mcp_token(x_skill_token: str = Header(default="")) -> None:
    if not x_skill_token:
        return  # Allow unauthenticated reads for manifest/info
    from assetclaw_matting.skills.auth import check_skill_token
    try:
        check_skill_token(x_skill_token)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc))


@router.get("/info")
async def mcp_info() -> JSONResponse:
    """MCP server info."""
    return JSONResponse(MCPServerInfo().model_dump())


@router.get("/tools")
async def list_tools() -> JSONResponse:
    """List available MCP tools."""
    from assetclaw_matting.mcp_server.tools import list_tools as _list
    tools = [t.model_dump() for t in _list()]
    return JSONResponse({"tools": tools})


@router.post("/tools/call", dependencies=[Depends(_verify_mcp_token)])
async def call_tool(body: MCPToolCall) -> JSONResponse:
    """Call an MCP tool. Requires X-Skill-Token header."""
    from assetclaw_matting.mcp_server.tools import call_tool as _call
    result = _call(body.name, body.arguments)
    return JSONResponse(result.model_dump())


@router.get("/resources")
async def list_resources() -> JSONResponse:
    """MCP resources endpoint (stub — no resources exposed currently)."""
    return JSONResponse({"resources": []})
