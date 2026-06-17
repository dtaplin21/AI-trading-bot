"""Tests for chart watcher heartbeat and feed status."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import chart_watcher.watcher_runtime as watcher_runtime


def test_is_watcher_online_when_recent_heartbeat():
    now = datetime.now(timezone.utc)
    status = {
        "running": True,
        "updated_at": (now - timedelta(seconds=30)).isoformat(),
    }
    assert watcher_runtime.is_watcher_online(status, now=now) is True


def test_is_watcher_offline_when_stale_heartbeat():
    now = datetime.now(timezone.utc)
    status = {
        "running": True,
        "updated_at": (now - timedelta(hours=1)).isoformat(),
    }
    assert watcher_runtime.is_watcher_online(status, now=now) is False


def test_is_watcher_offline_when_not_running():
    now = datetime.now(timezone.utc)
    status = {
        "running": False,
        "updated_at": now.isoformat(),
    }
    assert watcher_runtime.is_watcher_online(status, now=now) is False


def test_compute_feed_status_feeding():
    now = datetime.now(timezone.utc)
    status = {
        "symbol_last_feed_at": {"BTCUSD": (now - timedelta(seconds=30)).isoformat()},
    }
    assert (
        watcher_runtime.compute_feed_status(
            watcher_online=True,
            symbol="BTCUSD",
            session_open=True,
            watcher_status=status,
            now=now,
        )
        == "feeding"
    )


def test_compute_feed_status_feeding_prefers_feed_at_over_stale_bar_open():
    """5m bar open time can be minutes old; wall-clock feed_at must win."""
    now = datetime.now(timezone.utc)
    status = {
        "symbol_last_feed_at": {"MES": (now - timedelta(seconds=45)).isoformat()},
        "symbol_last_bar": {"MES": (now - timedelta(minutes=5)).isoformat()},
    }
    assert (
        watcher_runtime.compute_feed_status(
            watcher_online=True,
            symbol="MES",
            session_open=True,
            watcher_status=status,
            now=now,
        )
        == "feeding"
    )


def test_compute_feed_status_stale_when_session_open():
    now = datetime.now(timezone.utc)
    status = {
        "symbol_last_bar": {"MES": (now - timedelta(hours=2)).isoformat()},
    }
    assert (
        watcher_runtime.compute_feed_status(
            watcher_online=True,
            symbol="MES",
            session_open=True,
            watcher_status=status,
            now=now,
        )
        == "stale"
    )


def test_compute_feed_status_session_closed():
    now = datetime.now(timezone.utc)
    assert (
        watcher_runtime.compute_feed_status(
            watcher_online=True,
            symbol="MES",
            session_open=False,
            watcher_status={"symbol_last_bar": {}},
            now=now,
        )
        == "session_closed"
    )


def test_compute_feed_status_offline():
    now = datetime.now(timezone.utc)
    assert (
        watcher_runtime.compute_feed_status(
            watcher_online=False,
            symbol="MES",
            session_open=True,
            watcher_status=None,
            now=now,
        )
        == "offline"
    )


def test_build_watcher_dashboard_summary_counts():
    charts = [
        {"feed_status": "feeding", "execution_ready": True},
        {"feed_status": "feeding", "execution_ready": False},
        {"feed_status": "stale", "execution_ready": False},
        {"feed_status": "offline", "execution_ready": False},
        {"feed_status": "session_closed", "execution_ready": False},
    ]
    now = datetime.now(timezone.utc)
    status = {
        "running": True,
        "mode": "live",
        "updated_at": now.isoformat(),
        "kill_switch": False,
    }
    summary = watcher_runtime.build_watcher_dashboard_summary(charts, status)
    assert summary["online"] is True
    assert summary["running"] is True
    assert summary["mode"] == "live"
    assert summary["feeding"] == 2
    assert summary["stale"] == 1
    assert summary["offline"] == 1
    assert summary["session_closed"] == 1
    assert summary["execution_ready_count"] == 1
    assert summary["symbol_count"] == 5


def test_publish_watcher_status_noop_without_db(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    watcher_runtime.publish_watcher_status({"running": True})
