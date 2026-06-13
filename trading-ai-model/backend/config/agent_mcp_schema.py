"""Pydantic models for agents.yaml manifest."""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field


class AgentMcpConfig(BaseModel):
    """One agent entry in agents.yaml.

    transport=local — import Python class from agents.registry.AGENT_MAP.
    transport=mcp   — spawn mcp_server (JSON: command, args, cwd, env) and call mcp_tool.
    """

    enabled: bool = True
    transport: Literal["local", "mcp"] = "local"
    mcp_server: Optional[str] = None  # JSON stdio launch spec when transport=mcp
    timeout_ms: int = 5000
    config: Dict[str, Any] = Field(default_factory=dict)
    agent_id: str = ""

    model_config = {"extra": "allow"}


class AgentManifest(BaseModel):
    agents: Dict[str, AgentMcpConfig] = Field(default_factory=dict)

    def get(self, agent_id: str) -> Optional[AgentMcpConfig]:
        cfg = self.agents.get(agent_id)
        if cfg:
            cfg.agent_id = agent_id
        return cfg

    def get_enabled(self, prefix: str = "") -> Dict[str, AgentMcpConfig]:
        return {
            k: v for k, v in self.agents.items()
            if v.enabled and k.startswith(prefix)
        }
