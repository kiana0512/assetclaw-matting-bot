"""MCP Compatibility Layer for AssetClaw Win3090 Skill Node.

Exposes skills as MCP-compatible tools so Claude, OpenAI Agents, Cursor,
and other MCP-aware clients can discover and call them.

Implementation: HTTP-based tool API at /mcp/*
All tool logic delegates to skills.registry — single source of truth.

See docs/MCP_COMPATIBILITY.md for full integration guide.
"""
