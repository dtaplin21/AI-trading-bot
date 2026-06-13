"""Proxy that runs an agent via a remote MCP server."""
from __future__ import annotations

import json
import logging
from typing import Any

from agents.pipeline_context import PipelineContext
from agents.schemas import MethodOutput
from config.agent_mcp_schema import AgentMcpConfig
from trading_mcp.mcp_client import McpAgentClient, parse_mcp_server

logger = logging.getLogger(__name__)


class RemoteAgentProxy:
    """
    Local stand-in for an agent whose implementation lives on another MCP server.

    Remote servers must expose a tool (default ``run_method``) accepting:
      symbol, timeframe, historical_sample_size, agent_id
    and returning JSON compatible with MethodOutput.
    """

    def __init__(self, agent_id: str, cfg: AgentMcpConfig) -> None:
        self.agent_id = agent_id
        self.method_name = str(cfg.config.get("method_name", agent_id.replace("method_", "")))
        self._tool_name = str(cfg.config.get("mcp_tool", "run_method"))
        server_cfg = parse_mcp_server(cfg.mcp_server)
        self._client = McpAgentClient(server_cfg, timeout_ms=cfg.timeout_ms)

    def run(self, ctx: PipelineContext) -> PipelineContext:
        payload = {
            "agent_id": self.agent_id,
            "symbol": ctx.symbol,
            "timeframe": ctx.timeframe,
            "historical_sample_size": ctx.historical_sample_size or 0,
        }
        try:
            raw = self._client.call_tool_sync(self._tool_name, payload)
            data: dict[str, Any] = json.loads(raw)
            if "method" not in data:
                data["method"] = self.method_name
            output = MethodOutput(**data)
            ctx.method_outputs.append(output)
        except Exception as exc:
            logger.warning("Remote agent %s failed: %s", self.agent_id, exc)
            ctx.method_outputs.append(
                MethodOutput(
                    method=self.method_name,
                    confidence=0.0,
                    skipped=True,
                    skip_reason=f"mcp_error:{exc}",
                )
            )
        return ctx
