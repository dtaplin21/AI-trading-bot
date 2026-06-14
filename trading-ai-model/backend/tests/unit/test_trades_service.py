"""Tests for api.services.trades_service."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from api.services import trades_service as svc


def test_map_outcome_log_long_win():
    row = {
        "symbol": "MES",
        "entry_price": 5412.5,
        "exit_price": 5428.75,
        "pnl": 812.5,
        "hit_target": True,
        "hit_stop": False,
        "signal_rank": 86,
        "timestamp": "2026-05-24T09:32:00+00:00",
    }
    trade = svc._map_outcome_log(row, "snap-1")
    assert trade["id"] == "snap-1"
    assert trade["symbol"] == "MES"
    assert trade["direction"] == "long"
    assert trade["exit_reason"] == "target"
    assert trade["pnl_dollars"] == 812.5


def test_map_live_trade_short_stop():
    row = {
        "trade_id": "live-1",
        "symbol": "EURUSD",
        "side": "short",
        "entry_price": 1.0850,
        "exit_price": 1.0865,
        "stop_price": 1.0865,
        "target_price": 1.0820,
        "quantity": 1000.0,
        "exit_reason": "SL",
        "closed_at": datetime(2026, 5, 24, 10, 0, tzinfo=timezone.utc),
        "pnl": -150.0,
        "broker": "oanda",
    }
    trade = svc._map_live_trade(row)
    assert trade["id"] == "live-1"
    assert trade["direction"] == "short"
    assert trade["exit_reason"] == "stop"
    assert trade["source"] == "live_trades"


def test_build_closed_trades_from_outcomes_log(tmp_path, monkeypatch):
    log_path = tmp_path / "outcomes.jsonl"
    log_path.write_text(
        json.dumps(
            {
                "snapshot_id": "snap-a",
                "symbol": "NQ",
                "entry_price": 19340.0,
                "exit_price": 19310.0,
                "pnl": 600.0,
                "hit_target": True,
                "hit_stop": False,
                "signal_rank": 91,
                "timestamp": "2026-05-24T10:14:00+00:00",
            }
        )
        + "\n"
    )
    monkeypatch.setattr(svc, "OUTCOMES_LOG_PATH", log_path)
    monkeypatch.setattr(svc, "_load_live_trades", lambda limit: [])
    monkeypatch.setattr(svc, "_load_confluence_outcomes", lambda limit: [])

    payload = svc.build_closed_trades(limit=10)
    assert payload["source"] == "live"
    assert payload["count"] == 1
    assert payload["trades"][0]["symbol"] == "NQ"
    assert payload["trades"][0]["pnl_dollars"] == 600.0


def test_build_closed_trades_empty_when_no_sources(monkeypatch):
    monkeypatch.setattr(svc, "_load_outcomes_log", lambda: {})
    monkeypatch.setattr(svc, "_load_live_trades", lambda limit: [])
    monkeypatch.setattr(svc, "_load_confluence_outcomes", lambda limit: [])

    payload = svc.build_closed_trades()
    assert payload["trades"] == []
    assert payload["source"] == "empty"
