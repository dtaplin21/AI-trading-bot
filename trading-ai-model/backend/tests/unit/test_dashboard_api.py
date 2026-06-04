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
    assert data.get("source") == "live"
    assert any(p["id"] == "paper" for p in data["platforms"])
    assert any(p["id"] == "robinhood" for p in data["platforms"])
    assert any(p["name"] == "Tradovate" for p in data["platforms"])
    assert any(p["id"] == "oanda" for p in data["platforms"])


def test_broker_platforms_include_retail_and_futures():
    res = client.get("/dashboard")
    platforms = res.json()["platforms"]
    categories = {p["category"] for p in platforms}
    assert "retail" in categories
    assert "futures" in categories
