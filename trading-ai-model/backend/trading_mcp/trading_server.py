"""
trading_mcp/trading_server.py - MCP server entry point
"""
import asyncio
import json
import logging
import os

logger = logging.getLogger(__name__)


async def main():
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    import mcp.types as types

    server = Server("trading-agents")

    @server.list_tools()
    async def list_tools():
        return [
            types.Tool(
                name="list_agents",
                description="List all agents in the registry with their enabled state.",
                inputSchema={"type": "object", "properties": {}},
            ),
            types.Tool(
                name="get_pipeline_status",
                description="Current pipeline status: kill switch, open positions.",
                inputSchema={"type": "object", "properties": {}},
            ),
            types.Tool(
                name="set_agent_config",
                description="Enable or disable an agent by ID.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "agent_id": {"type": "string"},
                        "enabled":  {"type": "boolean"},
                    },
                    "required": ["agent_id"],
                },
            ),
            types.Tool(
                name="reload_config",
                description="Reload agents.yaml from disk.",
                inputSchema={"type": "object", "properties": {}},
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        if name == "list_agents":
            try:
                from agents.registry import get_agent_registry
                reg = get_agent_registry()
                return [types.TextContent(type="text", text=json.dumps(reg.catalog(), indent=2))]
            except Exception as e:
                return [types.TextContent(type="text", text=json.dumps({"error": str(e)}))]

        elif name == "get_pipeline_status":
            kill = os.getenv("RISK_KILL_SWITCH", "false")
            open_positions = []
            try:
                from live.live_position_monitor import get_position_monitor
                open_positions = [
                    {"trade_id": p.trade_id, "symbol": p.symbol, "side": p.side}
                    for p in get_position_monitor().open_positions()
                ]
            except Exception:
                pass
            return [types.TextContent(type="text", text=json.dumps({
                "kill_switch": kill,
                "paper_mode": os.getenv("PAPER_MODE", "true"),
                "open_positions": open_positions,
            }, indent=2))]

        elif name == "set_agent_config":
            agent_id = arguments.get("agent_id", "")
            enabled  = arguments.get("enabled")
            try:
                from agents.registry import get_agent_registry
                reg = get_agent_registry()
                cfg = reg._manifest.get(agent_id)
                if cfg is None:
                    return [types.TextContent(type="text", text=f"Agent '{agent_id}' not found")]
                if enabled is not None:
                    cfg.enabled = enabled
                reg._cache.pop(agent_id, None)
                return [types.TextContent(type="text", text=f"Updated {agent_id}: enabled={cfg.enabled}")]
            except Exception as e:
                return [types.TextContent(type="text", text=f"Error: {e}")]

        elif name == "reload_config":
            try:
                from agents.registry import reset_registries
                from trading_mcp.config_loader import reload_manifest
                reload_manifest()
                reset_registries()
                return [types.TextContent(type="text", text="Reloaded.")]
            except Exception as e:
                return [types.TextContent(type="text", text=f"Error: {e}")]

        return [types.TextContent(type="text", text=f"Unknown tool: {name}")]

    async with stdio_server() as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
