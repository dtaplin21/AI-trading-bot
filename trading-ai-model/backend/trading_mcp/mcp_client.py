"""MCP stdio client for remote agent servers."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def parse_mcp_server(raw: str | None) -> dict[str, Any]:
    """Parse mcp_server from agents.yaml (JSON with command/args/cwd/env)."""
    if not raw or not str(raw).strip():
        raise ValueError("mcp_server is required when transport=mcp")
    text = str(raw).strip()
    if text.startswith("{"):
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("mcp_server JSON must be an object")
        if "command" not in data:
            raise ValueError("mcp_server JSON must include 'command'")
        return data
    raise ValueError("mcp_server must be a JSON object string")


class McpAgentClient:
    """Call tools on a child MCP server launched via stdio."""

    def __init__(self, server_config: dict[str, Any], timeout_ms: int = 5000) -> None:
        from mcp.client.stdio import StdioServerParameters

        self._params = StdioServerParameters(
            command=str(server_config["command"]),
            args=[str(a) for a in server_config.get("args", [])],
            env=server_config.get("env"),
            cwd=server_config.get("cwd"),
        )
        self._timeout_s = max(timeout_ms, 1000) / 1000.0

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        async with stdio_client(self._params) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=self._timeout_s)
                result = await asyncio.wait_for(
                    session.call_tool(name, arguments),
                    timeout=self._timeout_s,
                )
                parts: list[str] = []
                for block in result.content:
                    if block.type == "text":
                        parts.append(block.text)
                if not parts:
                    return json.dumps({"error": "empty MCP tool response"})
                return parts[0]

    def call_tool_sync(self, name: str, arguments: dict[str, Any]) -> str:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.call_tool(name, arguments))
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(
                lambda: asyncio.run(self.call_tool(name, arguments))
            ).result(timeout=self._timeout_s + 1.0)
