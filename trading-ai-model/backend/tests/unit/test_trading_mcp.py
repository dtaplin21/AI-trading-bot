"""Tests for trading_mcp tool handlers."""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch


def _make_registry(agents: dict):
    """Build a minimal AgentRegistry with a fake manifest."""
    from agents.registry import AgentRegistry
    from config.agent_mcp_schema import AgentManifest, AgentMcpConfig

    manifest = AgentManifest(agents={k: AgentMcpConfig(**v) for k, v in agents.items()})
    reg = AgentRegistry.__new__(AgentRegistry)
    reg.symbol = ""
    reg._manifest = manifest
    reg._cache = {}
    return reg


# ── get_risk_summary ──────────────────────────────────────────────────────────


def test_get_risk_summary_returns_expected_keys(monkeypatch):
    import risk.kill_switch_runtime as kill_switch_runtime

    monkeypatch.setenv("RISK_KILL_SWITCH", "false")
    monkeypatch.setattr(kill_switch_runtime, "_read_postgres", lambda: (None, None))
    kill_switch_runtime.reset_kill_switch_runtime()
    monkeypatch.setenv("PAPER_MODE", "true")
    monkeypatch.setenv("RISK_MAX_CONCURRENT", "3")

    with patch("live.live_position_monitor.get_position_monitor") as mock_mon:
        mock_mon.return_value.open_positions.return_value = []
        import asyncio

        from trading_mcp.tools.risk import get_risk_summary

        result = asyncio.run(get_risk_summary())

    data = json.loads(result)
    assert "kill_switch" in data
    assert "paper_mode" in data
    assert "open_positions" in data
    assert data["open_count"] == 0


def test_get_risk_summary_lists_open_positions(monkeypatch):
    import risk.kill_switch_runtime as kill_switch_runtime

    monkeypatch.setenv("RISK_KILL_SWITCH", "false")
    monkeypatch.setattr(kill_switch_runtime, "_read_postgres", lambda: (None, None))
    kill_switch_runtime.reset_kill_switch_runtime()

    mock_pos = MagicMock()
    mock_pos.trade_id = "live-MES-abc123"
    mock_pos.symbol = "MES"
    mock_pos.side = "LONG"
    mock_pos.entry_price = 5280.0
    mock_pos.target_price = 5300.0
    mock_pos.stop_price = 5265.0
    mock_pos.bars_held = 3
    mock_pos.ev_pct = 0.18

    with patch("live.live_position_monitor.get_position_monitor") as mock_mon:
        mock_mon.return_value.open_positions.return_value = [mock_pos]
        import asyncio

        from trading_mcp.tools.risk import get_risk_summary

        result = asyncio.run(get_risk_summary())

    data = json.loads(result)
    assert data["open_count"] == 1
    assert data["open_positions"][0]["symbol"] == "MES"


# ── set_kill_switch ───────────────────────────────────────────────────────────


def test_set_kill_switch_true(monkeypatch):
    import risk.kill_switch_runtime as kill_switch_runtime

    monkeypatch.setenv("RISK_KILL_SWITCH", "false")
    monkeypatch.setattr(kill_switch_runtime, "_write_postgres", lambda enabled: None)
    kill_switch_runtime.reset_kill_switch_runtime()
    import asyncio

    from trading_mcp.tools.risk import set_kill_switch

    result = asyncio.run(set_kill_switch(True))
    data = json.loads(result)
    assert data["ok"] is True
    assert data["enabled"] is True
    assert data["effective"] is True
    assert kill_switch_runtime.is_kill_switch_active() is True


def test_set_kill_switch_false(monkeypatch):
    import risk.kill_switch_runtime as kill_switch_runtime

    monkeypatch.setenv("RISK_KILL_SWITCH", "true")
    monkeypatch.setattr(kill_switch_runtime, "_write_postgres", lambda enabled: None)
    kill_switch_runtime.reset_kill_switch_runtime()
    import asyncio

    from trading_mcp.tools.risk import set_kill_switch

    result = asyncio.run(set_kill_switch(False))
    data = json.loads(result)
    assert data["ok"] is True
    assert data["enabled"] is False
    assert data["effective"] is False
    assert kill_switch_runtime.is_kill_switch_active() is False


def test_set_kill_switch_delegates_to_runtime(monkeypatch):
    import asyncio

    from trading_mcp.tools.risk import set_kill_switch

    expected = {
        "enabled": True,
        "env_default": False,
        "updated_at": None,
        "effective": True,
        "source": "memory",
    }
    with patch(
        "trading_mcp.tools.risk.set_kill_switch_enabled",
        new=AsyncMock(return_value=expected),
    ) as runtime_set:
        result = asyncio.run(set_kill_switch(True))

    runtime_set.assert_awaited_once_with(True)
    data = json.loads(result)
    assert data["ok"] is True
    assert data["enabled"] is True
    assert data["effective"] is True


# ── check_level_gate ──────────────────────────────────────────────────────────


def test_check_level_gate_passes(monkeypatch):
    monkeypatch.delenv("LEVEL_GATE_DISABLED", raising=False)

    mock_setup = MagicMock()
    mock_setup.level_price = 5280.0
    mock_setup.entry_side = "BUY"
    mock_setup.entry_price = 5280.0
    mock_setup.target_price = 5301.74
    mock_setup.stop_price = 5268.49
    mock_setup.optimal_tp_pct = 0.412
    mock_setup.optimal_sl_pct = 0.218
    mock_setup.expected_value_pct = 0.187
    mock_setup.touch_count = 14
    mock_setup.hold_rate = 0.72

    with patch("pipeline.level_entry_gate.LevelEntryGate.check", return_value=mock_setup):
        import asyncio

        from trading_mcp.tools.levels import check_level_gate

        result = asyncio.run(check_level_gate("MES", 5281.0))

    data = json.loads(result)
    assert data["passed"] is True
    assert data["entry_side"] == "BUY"
    assert data["touch_count"] == 14


def test_check_level_gate_fails(monkeypatch):
    monkeypatch.delenv("LEVEL_GATE_DISABLED", raising=False)

    with patch("pipeline.level_entry_gate.LevelEntryGate.check", return_value=None):
        import asyncio

        from trading_mcp.tools.levels import check_level_gate

        result = asyncio.run(check_level_gate("MES", 5100.0))

    data = json.loads(result)
    assert data["passed"] is False


# ── get_level_watchlist ───────────────────────────────────────────────────────


def test_get_level_watchlist_returns_error_without_db(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake:fake@fake/fake")
    monkeypatch.setenv("DATABASE_SSL_DISABLE", "true")

    import asyncio

    from trading_mcp.tools.levels import get_level_watchlist

    result = asyncio.run(get_level_watchlist("MES"))
    data = json.loads(result)
    assert "levels" in data or "error" in data


def test_get_level_watchlist_structure(monkeypatch):
    monkeypatch.setenv("DATABASE_SSL_DISABLE", "true")

    fake_rows = [
        {
            "level_price": 5280.0,
            "role": "SUPPORT",
            "entry_side": "BUY",
            "hold_rate": 0.72,
            "touch_count": 14,
            "strength_score": 0.81,
            "optimal_tp_pct": 0.412,
            "optimal_sl_pct": 0.218,
            "optimal_rr": 1.89,
            "expected_value_pct": 0.187,
        }
    ]

    mock_cur = MagicMock()
    mock_cur.fetchall.return_value = fake_rows

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    with patch("psycopg2.connect", return_value=mock_conn):
        import asyncio

        from trading_mcp.tools.levels import get_level_watchlist

        result = asyncio.run(get_level_watchlist("MES"))

    data = json.loads(result)
    assert data["symbol"] == "MES"
    assert isinstance(data["levels"], list)


# ── get_method_agents_from_registry ──────────────────────────────────────────


def test_get_all_method_agents_falls_back_on_registry_failure():
    with patch("agents.registry.get_agent_registry", side_effect=Exception("DB down")):
        from agents.method_agents import ALL_METHOD_AGENTS, get_all_method_agents_from_registry

        result = get_all_method_agents_from_registry()
        assert result is ALL_METHOD_AGENTS


def test_get_confirm_method_agents_falls_back_on_registry_failure():
    with patch("agents.registry.get_agent_registry", side_effect=Exception("DB down")):
        from agents.method_agents import get_confirm_method_agents_from_registry

        result = get_confirm_method_agents_from_registry()
        assert isinstance(result, list)


# ── MCP client / remote transport ────────────────────────────────────────────


def test_parse_mcp_server_json():
    from trading_mcp.mcp_client import parse_mcp_server

    cfg = parse_mcp_server('{"command": "python", "args": ["-m", "srv"]}')
    assert cfg["command"] == "python"
    assert cfg["args"] == ["-m", "srv"]


def test_parse_mcp_server_rejects_invalid():
    import pytest

    from trading_mcp.mcp_client import parse_mcp_server

    with pytest.raises(ValueError):
        parse_mcp_server(None)


def test_registry_instantiates_remote_proxy():
    from agents.registry import AgentRegistry
    from config.agent_mcp_schema import AgentManifest, AgentMcpConfig
    from trading_mcp.mcp_agent_proxy import RemoteAgentProxy

    manifest = AgentManifest(
        agents={
            "method_gann": AgentMcpConfig(
                enabled=True,
                transport="mcp",
                mcp_server='{"command": "python", "args": ["-m", "fake"]}',
                timeout_ms=3000,
                config={"method_name": "gann"},
            ),
        }
    )
    reg = AgentRegistry.__new__(AgentRegistry)
    reg.symbol = ""
    reg._manifest = manifest
    reg._cache = {}

    proxy = reg.get("method_gann")
    assert isinstance(proxy, RemoteAgentProxy)
    assert proxy.method_name == "gann"


def test_get_config_path_uses_env(monkeypatch, tmp_path):
    from trading_mcp.config_loader import get_config_path

    custom = tmp_path / "agents.yaml"
    custom.write_text("agents: {}\n")
    monkeypatch.setenv("AGENT_CONFIG_PATH", str(custom))
    assert get_config_path() == custom

