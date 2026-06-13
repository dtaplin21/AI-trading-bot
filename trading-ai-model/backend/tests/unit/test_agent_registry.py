"""Tests for AgentRegistry and agents.yaml config loading."""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import yaml


def _write_yaml(agents: dict) -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w")
    yaml.dump({"agents": agents}, tmp)
    tmp.close()
    return Path(tmp.name)


def _minimal_agent(**overrides) -> dict:
    base = {"enabled": True, "transport": "local", "timeout_ms": 2000, "config": {}}
    base.update(overrides)
    return base


# ── AgentManifest ─────────────────────────────────────────────────────────────


def test_manifest_loads_from_yaml():
    from trading_mcp.config_loader import load_manifest

    path = _write_yaml({"chart_reading": _minimal_agent()})
    manifest = load_manifest(path)
    assert "chart_reading" in manifest.agents
    assert manifest.agents["chart_reading"].enabled is True


def test_manifest_get_returns_none_for_missing():
    from trading_mcp.config_loader import load_manifest

    path = _write_yaml({"chart_reading": _minimal_agent()})
    manifest = load_manifest(path)
    assert manifest.get("nonexistent") is None


def test_manifest_get_sets_agent_id():
    from trading_mcp.config_loader import load_manifest

    path = _write_yaml({"risk": _minimal_agent()})
    manifest = load_manifest(path)
    cfg = manifest.get("risk")
    assert cfg is not None
    assert cfg.agent_id == "risk"


def test_manifest_get_enabled_filters_by_prefix():
    from trading_mcp.config_loader import load_manifest

    path = _write_yaml(
        {
            "method_fibonacci": _minimal_agent(enabled=True),
            "method_gann": _minimal_agent(enabled=False),
            "chart_reading": _minimal_agent(enabled=True),
        }
    )
    manifest = load_manifest(path)
    enabled = manifest.get_enabled("method_")
    assert "method_fibonacci" in enabled
    assert "method_gann" not in enabled
    assert "chart_reading" not in enabled


def test_manifest_get_enabled_no_prefix_returns_all_enabled():
    from trading_mcp.config_loader import load_manifest

    path = _write_yaml(
        {
            "risk": _minimal_agent(enabled=True),
            "audit": _minimal_agent(enabled=False),
            "learning": _minimal_agent(enabled=True),
        }
    )
    manifest = load_manifest(path)
    enabled = manifest.get_enabled()
    assert "risk" in enabled
    assert "learning" in enabled
    assert "audit" not in enabled


# ── Env overrides ─────────────────────────────────────────────────────────────


def test_env_override_disables_agent(monkeypatch):
    monkeypatch.setenv("AGENT_METHOD_GANN_ENABLED", "false")
    from trading_mcp.config_loader import load_manifest

    path = _write_yaml({"method_gann": _minimal_agent(enabled=True)})
    manifest = load_manifest(path)
    assert manifest.agents["method_gann"].enabled is False


def test_env_override_enables_agent(monkeypatch):
    monkeypatch.setenv("AGENT_AUDIT_ENABLED", "true")
    from trading_mcp.config_loader import load_manifest

    path = _write_yaml({"audit": _minimal_agent(enabled=False)})
    manifest = load_manifest(path)
    assert manifest.agents["audit"].enabled is True


def test_env_override_timeout(monkeypatch):
    monkeypatch.setenv("AGENT_RISK_TIMEOUT_MS", "9999")
    from trading_mcp.config_loader import load_manifest

    path = _write_yaml({"risk": _minimal_agent(timeout_ms=2000)})
    manifest = load_manifest(path)
    assert manifest.agents["risk"].timeout_ms == 9999


# ── AgentRegistry ─────────────────────────────────────────────────────────────


def test_registry_catalog_structure():
    from agents.registry import AgentRegistry
    from config.agent_mcp_schema import AgentManifest, AgentMcpConfig

    manifest = AgentManifest(
        agents={
            "news": AgentMcpConfig(
                enabled=True,
                transport="local",
                timeout_ms=3000,
                config={"max_age_minutes": 120},
            ),
        }
    )
    reg = AgentRegistry.__new__(AgentRegistry)
    reg.symbol = ""
    reg._manifest = manifest
    reg._cache = {}

    catalog = reg.catalog()
    assert len(catalog) == 1
    assert catalog[0]["id"] == "news"
    assert catalog[0]["enabled"] is True
    assert catalog[0]["config"]["max_age_minutes"] == 120


def test_registry_get_returns_none_for_disabled():
    from agents.registry import AgentRegistry
    from config.agent_mcp_schema import AgentManifest, AgentMcpConfig

    manifest = AgentManifest(
        agents={
            "audit": AgentMcpConfig(enabled=False, transport="local", timeout_ms=1000),
        }
    )
    reg = AgentRegistry.__new__(AgentRegistry)
    reg.symbol = ""
    reg._manifest = manifest
    reg._cache = {}

    assert reg.get("audit") is None


def test_registry_get_returns_none_for_unknown():
    from agents.registry import AgentRegistry
    from config.agent_mcp_schema import AgentManifest

    reg = AgentRegistry.__new__(AgentRegistry)
    reg.symbol = ""
    reg._manifest = AgentManifest(agents={})
    reg._cache = {}

    assert reg.get("does_not_exist") is None


def test_registry_is_enabled():
    from agents.registry import AgentRegistry
    from config.agent_mcp_schema import AgentManifest, AgentMcpConfig

    manifest = AgentManifest(
        agents={
            "learning": AgentMcpConfig(enabled=True, transport="local", timeout_ms=3000),
            "audit": AgentMcpConfig(enabled=False, transport="local", timeout_ms=1000),
        }
    )
    reg = AgentRegistry.__new__(AgentRegistry)
    reg.symbol = ""
    reg._manifest = manifest
    reg._cache = {}

    assert reg.is_enabled("learning") is True
    assert reg.is_enabled("audit") is False
    assert reg.is_enabled("nonexistent") is False


def test_registry_reload_clears_cache():
    from agents.registry import AgentRegistry, reset_registries
    from config.agent_mcp_schema import AgentManifest, AgentMcpConfig

    reset_registries()
    manifest = AgentManifest(
        agents={
            "audit": AgentMcpConfig(enabled=True, transport="local", timeout_ms=1000),
        }
    )
    reg = AgentRegistry.__new__(AgentRegistry)
    reg.symbol = ""
    reg._manifest = manifest
    reg._cache = {"audit": object()}

    with patch("trading_mcp.config_loader.get_manifest.cache_clear"):
        with patch("trading_mcp.config_loader.load_manifest", return_value=manifest):
            reg.reload()

    assert "audit" not in reg._cache


def test_registry_get_method_agents_filters_disabled():
    from agents.registry import AgentRegistry, METHOD_AGENT_IDS
    from config.agent_mcp_schema import AgentManifest, AgentMcpConfig

    agents = {
        aid: AgentMcpConfig(enabled=False, transport="local", timeout_ms=2000)
        for aid in METHOD_AGENT_IDS
    }
    manifest = AgentManifest(agents=agents)
    reg = AgentRegistry.__new__(AgentRegistry)
    reg.symbol = ""
    reg._manifest = manifest
    reg._cache = {}

    result = reg.get_method_agents()
    assert result == []


def test_reset_registries_clears_all():
    from agents.registry import get_agent_registry, reset_registries

    reset_registries()
    r1 = get_agent_registry(symbol="MES")
    r2 = get_agent_registry(symbol="MES")
    assert r1 is r2

    reset_registries()
    r3 = get_agent_registry(symbol="MES")
    assert r3 is not r1
