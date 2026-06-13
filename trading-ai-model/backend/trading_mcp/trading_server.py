"""
trading_mcp/trading_server.py - MCP server entry point
"""
import asyncio
import json
import logging
import os

logger = logging.getLogger(__name__)


def _save_manifest(manifest) -> None:
    """Write current manifest state back to agents.yaml."""
    import yaml

    config_path = os.getenv(
        "AGENT_CONFIG_PATH",
        "/Users/dtaplin21/AI-trading-bot/trading-ai-model/backend/config/agents.yaml",
    )
    data = {"agents": {}}
    for agent_id, cfg in manifest.agents.items():
        data["agents"][agent_id] = {
            "enabled": cfg.enabled,
            "transport": cfg.transport,
            "timeout_ms": cfg.timeout_ms,
            "config": cfg.config,
        }
        if cfg.mcp_server:
            data["agents"][agent_id]["mcp_server"] = cfg.mcp_server
    with open(config_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


async def main():
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    import mcp.types as types

    server = Server("trading-agents")

    @server.list_tools()
    async def list_tools():
        return [
            # ── Agent admin ───────────────────────────────────────────────
            types.Tool(
                name="list_agents",
                description="List all agents in the registry with enabled state and config.",
                inputSchema={"type": "object", "properties": {}},
            ),
            types.Tool(
                name="get_pipeline_status",
                description="Pipeline status: kill switch, paper mode, open positions.",
                inputSchema={"type": "object", "properties": {}},
            ),
            types.Tool(
                name="set_agent_config",
                description="Enable or disable an agent by ID. Persists to agents.yaml by default.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "agent_id": {"type": "string"},
                        "enabled": {"type": "boolean"},
                        "persist": {
                            "type": "boolean",
                            "description": "Write to agents.yaml (default true)",
                        },
                    },
                    "required": ["agent_id"],
                },
            ),
            types.Tool(
                name="reload_config",
                description="Reload agents.yaml from disk and clear registry cache.",
                inputSchema={"type": "object", "properties": {}},
            ),
            # ── Level intelligence ────────────────────────────────────────
            types.Tool(
                name="get_level_watchlist",
                description="Actionable DB levels for a symbol: price, role, TP/SL, EV, R:R.",
                inputSchema={
                    "type": "object",
                    "properties": {"symbol": {"type": "string"}},
                    "required": ["symbol"],
                },
            ),
            types.Tool(
                name="check_level_gate",
                description="Would this price pass the level entry gate for this symbol?",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string"},
                        "price": {"type": "number"},
                    },
                    "required": ["symbol", "price"],
                },
            ),
            types.Tool(
                name="get_recent_touches",
                description="Last N level touches for a symbol from level_touches table.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["symbol"],
                },
            ),
            # ── Risk / ops ────────────────────────────────────────────────
            types.Tool(
                name="get_risk_summary",
                description="Kill switch state, daily loss caps, open positions, size limits.",
                inputSchema={"type": "object", "properties": {}},
            ),
            types.Tool(
                name="set_kill_switch",
                description="Enable or disable the kill switch for this MCP process.",
                inputSchema={
                    "type": "object",
                    "properties": {"enabled": {"type": "boolean"}},
                    "required": ["enabled"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        # ── Agent admin ───────────────────────────────────────────────────
        if name == "list_agents":
            try:
                from agents.registry import get_agent_registry

                reg = get_agent_registry()
                return [types.TextContent(type="text", text=json.dumps(reg.catalog(), indent=2))]
            except Exception as e:
                return [types.TextContent(type="text", text=json.dumps({"error": str(e)}))]

        elif name == "get_pipeline_status":
            from trading_mcp.tools.risk import get_risk_summary

            return [types.TextContent(type="text", text=await get_risk_summary())]

        elif name == "set_agent_config":
            agent_id = arguments.get("agent_id", "")
            enabled = arguments.get("enabled")
            persist = arguments.get("persist", True)
            try:
                from agents.registry import get_agent_registry

                reg = get_agent_registry()
                cfg = reg._manifest.get(agent_id)
                if cfg is None:
                    return [types.TextContent(type="text", text=f"Agent '{agent_id}' not found")]
                if enabled is not None:
                    cfg.enabled = enabled
                reg._cache.pop(agent_id, None)

                if persist:
                    _save_manifest(reg._manifest)
                    note = "Saved to agents.yaml."
                else:
                    note = "In-memory only (persist=false)."

                return [
                    types.TextContent(
                        type="text",
                        text=f"Updated {agent_id}: enabled={cfg.enabled}. {note}",
                    )
                ]
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

        # ── Level intelligence ────────────────────────────────────────────
        elif name == "get_level_watchlist":
            from trading_mcp.tools.levels import get_level_watchlist

            return [
                types.TextContent(
                    type="text",
                    text=await get_level_watchlist(arguments["symbol"]),
                )
            ]

        elif name == "check_level_gate":
            from trading_mcp.tools.levels import check_level_gate

            return [
                types.TextContent(
                    type="text",
                    text=await check_level_gate(arguments["symbol"], float(arguments["price"])),
                )
            ]

        elif name == "get_recent_touches":
            from trading_mcp.tools.levels import get_recent_touches

            return [
                types.TextContent(
                    type="text",
                    text=await get_recent_touches(
                        arguments["symbol"],
                        int(arguments.get("limit", 20)),
                    ),
                )
            ]

        # ── Risk / ops ────────────────────────────────────────────────────
        elif name == "get_risk_summary":
            from trading_mcp.tools.risk import get_risk_summary

            return [types.TextContent(type="text", text=await get_risk_summary())]

        elif name == "set_kill_switch":
            from trading_mcp.tools.risk import set_kill_switch

            return [
                types.TextContent(
                    type="text",
                    text=await set_kill_switch(bool(arguments["enabled"])),
                )
            ]

        return [types.TextContent(type="text", text=f"Unknown tool: {name}")]

    async with stdio_server() as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
