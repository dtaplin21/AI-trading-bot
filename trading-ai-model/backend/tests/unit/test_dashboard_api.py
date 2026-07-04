"""Unit tests for dashboard API."""

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def test_dashboard_overview():
    res = client.get("/dashboard")
    assert res.status_code == 200
    data = res.json()
    assert "platforms" in data
    assert "open_positions" in data
    assert "watched_charts" in data
    assert "active_broker" in data
    assert len(data["watched_charts"]) == 23
    assert "watched_charts_grouped" in data
    assert "watcher_symbol_summary" in data
    assert "session_summary" in data
    assert "system_status" in data
    assert "market_data_feeds" in data
    assert "label" in data["market_data_feeds"]
    assert "market_data_feeds" in data["system_status"]
    assert "kill_switch" in data
    assert "order_sizing" in data
    os = data["order_sizing"]
    assert "coinbase_order_usd" in os
    assert "oanda_order_usd" in os
    assert "limits" in os
    assert "coinbase_order_usd" in data["risk_limits"]
    assert "oanda_order_usd" in data["risk_limits"]
    ks = data["kill_switch"]
    assert "enabled" in ks
    assert "env_default" in ks
    assert "updated_at" in ks
    assert data["system_status"]["kill_switch"]["enabled"] == ks["enabled"]
    assert data.get("source") == "live"
    assert "watcher_status" in data
    ws = data["watcher_status"]
    assert "online" in ws
    assert "feeding" in ws
    assert "stale" in ws
    assert "offline" in ws
    assert "session_closed" in ws
    chart = data["watched_charts"][0]
    assert "feed_status" in chart
    assert "pipeline_running" in chart
    assert "execution_ready" in chart
    assert "watcher_bars_processed" in chart
    assert "market_data_source" in chart
    assert any(p["id"] == "paper" for p in data["platforms"])
    assert any(p["id"] == "robinhood" for p in data["platforms"])
    assert any(p["name"] == "Tradovate" for p in data["platforms"])
    assert any(p["id"] == "oanda" for p in data["platforms"])


def test_dashboard_kill_switch_object_shape():
    res = client.get("/dashboard")
    assert res.status_code == 200
    ks = res.json()["kill_switch"]
    assert isinstance(ks, dict)
    assert isinstance(ks["enabled"], bool)
    assert isinstance(ks["env_default"], bool)
    assert "updated_at" in ks


def test_broker_platforms_include_retail_and_futures():
    res = client.get("/dashboard")
    platforms = res.json()["platforms"]
    categories = {p["category"] for p in platforms}
    assert "retail" in categories
    assert "futures" in categories


def test_trades_returns_real_payload_not_mock():
    res = client.get("/trades")
    assert res.status_code == 200
    data = res.json()
    assert "trades" in data
    assert "source" in data
    assert data["source"] in ("live", "empty")
    for trade in data["trades"]:
        assert trade["id"] != "t1"
        assert "source" in trade
