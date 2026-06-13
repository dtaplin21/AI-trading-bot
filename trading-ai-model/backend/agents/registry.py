"""
agents/registry.py

Central agent registry — loads agents.yaml and returns agent instances.
Only used by MCP tools today. Pipeline wiring comes in Phase 2.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Maps agent_id → dotted import path matching your actual class names
AGENT_MAP: Dict[str, str] = {
    # Method agents
    "method_level_369": "agents.method_agents.level_369_agent.Level369Agent",
    "method_fibonacci": "agents.method_agents.fibonacci_agent.FibonacciAgent",
    "method_ancient_number": "agents.method_agents.ancient_number_agent.AncientNumberAgent",
    "method_gann": "agents.method_agents.gann_agent.GannAgent",
    "method_elliott": "agents.method_agents.elliott_wave_agent.ElliottWaveAgent",
    "method_harmonic": "agents.method_agents.harmonic_agent.HarmonicAgent",
    "method_candlestick": "agents.method_agents.candlestick_agent.CandlestickAgent",
    "method_fractal": "agents.method_agents.fractal_agent.FractalAgent",
    "method_balance_line": "agents.method_agents.balance_line_agent.BalanceLineAgent",
    "method_momentum": "agents.method_agents.momentum_agent.MomentumAgent",
    "method_markov": "agents.method_agents.markov_agent.MarkovAgent",
    "method_monte_carlo": "agents.method_agents.monte_carlo_agent.MonteCarloMethodAgent",
    "method_strategy_math": "agents.method_agents.strategy_math_agent.StrategyMathAgent",
    # Pipeline agents
    "market_data": "agents.market_data_agent.MarketDataAgent",
    "chart_reading": "agents.chart_reading_agent.ChartReadingAgent",
    "confluence": "agents.confluence_agent.ConfluenceAgentRunner",
    "feature_fusion": "agents.feature_fusion_agent.FeatureFusionAgent",
    "prediction": "agents.prediction_agent.PredictionAgent",
    "trade_planning": "agents.trade_planning_agent.TradePlanningAgent",
    "risk": "agents.risk_agent.RiskAgent",
    "execution": "agents.execution_agent.ExecutionAgent",
    "learning": "agents.learning_agent.LearningAgent",
    "audit": "agents.audit_agent.AuditAgent",
    "news": "agents.news_runtime.get_news_agent",
    "position_monitor": "live.live_position_monitor.get_position_monitor",
}

METHOD_AGENT_IDS = [k for k in AGENT_MAP if k.startswith("method_")]

CONFIRM_METHOD_IDS: tuple[str, ...] = (
    "method_candlestick",
    "method_momentum",
    "method_markov",
    "method_monte_carlo",
    "method_harmonic",
    "method_elliott",
    "method_fractal",
)


def _import_class(dotpath: str):
    module_path, name = dotpath.rsplit(".", 1)
    mod = importlib.import_module(module_path)
    return getattr(mod, name)


class AgentRegistry:
    def __init__(self, symbol: str = "", config_path: str | None = None) -> None:
        self.symbol = symbol
        from trading_mcp.config_loader import load_manifest

        self._manifest = load_manifest(config_path)
        self._cache: Dict[str, Any] = {}

    def get(self, agent_id: str) -> Optional[Any]:
        cfg = self._manifest.get(agent_id)
        if cfg is None:
            logger.debug("Agent '%s' not in manifest", agent_id)
            return None
        if not cfg.enabled:
            logger.debug("Agent '%s' disabled", agent_id)
            return None
        if agent_id not in self._cache:
            self._cache[agent_id] = self._instantiate(agent_id)
        return self._cache[agent_id]

    def get_method_agents(self) -> List[Any]:
        return [a for aid in METHOD_AGENT_IDS if (a := self.get(aid)) is not None]

    def get_confirm_method_agents(self) -> List[Any]:
        return [a for aid in CONFIRM_METHOD_IDS if (a := self.get(aid)) is not None]

    def get_enabled(self, prefix: str = "") -> Dict[str, Any]:
        return {
            aid: inst
            for aid in self._manifest.get_enabled(prefix)
            if (inst := self.get(aid)) is not None
        }

    def is_enabled(self, agent_id: str) -> bool:
        cfg = self._manifest.get(agent_id)
        return cfg is not None and cfg.enabled

    def catalog(self) -> List[dict]:
        rows = []
        for aid, cfg in self._manifest.agents.items():
            row = {
                "id": aid,
                "enabled": cfg.enabled,
                "transport": cfg.transport,
                "timeout_ms": cfg.timeout_ms,
                "config": cfg.config,
            }
            if cfg.mcp_server:
                row["mcp_server"] = cfg.mcp_server
            rows.append(row)
        return rows

    def reload(self) -> None:
        from trading_mcp.config_loader import reload_manifest

        self._manifest = reload_manifest()
        self._cache.clear()

    def _instantiate(self, agent_id: str) -> Optional[Any]:
        cfg = self._manifest.get(agent_id)
        if cfg is not None and cfg.transport == "mcp":
            if not cfg.mcp_server:
                logger.warning("Agent '%s' has transport=mcp but no mcp_server", agent_id)
                return None
            try:
                from trading_mcp.mcp_agent_proxy import RemoteAgentProxy

                return RemoteAgentProxy(agent_id, cfg)
            except Exception as e:
                logger.error("Remote agent '%s' setup failed: %s", agent_id, e)
                return None

        dotpath = AGENT_MAP.get(agent_id)
        if not dotpath:
            logger.warning("No import path for '%s'", agent_id)
            return None
        try:
            cls = _import_class(dotpath)
            if callable(cls) and not isinstance(cls, type):
                return cls()
            return cls()
        except ImportError as e:
            logger.warning("Agent '%s' import failed: %s", agent_id, e)
            return None
        except Exception as e:
            logger.error("Agent '%s' instantiation failed: %s", agent_id, e)
            return None


_registries: Dict[str, AgentRegistry] = {}


def get_agent_registry(symbol: str = "", config_path: str | None = None) -> AgentRegistry:
    key = symbol or "__global__"
    if key not in _registries:
        _registries[key] = AgentRegistry(symbol=symbol, config_path=config_path)
    return _registries[key]


def reset_registries() -> None:
    _registries.clear()
