"""
Cross-process chart watcher heartbeat in Postgres runtime_controls.

Worker publishes; API/dashboard reads for live feed status per symbol.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from config.settings import get_settings
from data.storage.pg_connect import connect_psycopg2, is_database_url_placeholder

logger = logging.getLogger(__name__)

_WATCHER_STATUS_KEY = "watcher_status"


def _database_url() -> str | None:
    url = (get_settings().database_url or os.getenv("DATABASE_URL", "")).strip()
    if not url or is_database_url_placeholder(url):
        return None
    return url


def heartbeat_stale_seconds() -> int:
    """Watcher considered offline if heartbeat older than this."""
    bar_interval = int(os.getenv("WATCHER_BAR_INTERVAL", "60"))
    raw = os.getenv("WATCHER_HEARTBEAT_STALE_SEC", "").strip()
    if raw:
        try:
            return max(30, int(raw))
        except ValueError:
            pass
    return max(120, bar_interval * 3)


def symbol_feed_stale_seconds() -> int:
    """Symbol not feeding if last bar from watcher older than this."""
    bar_interval = int(os.getenv("WATCHER_BAR_INTERVAL", "60"))
    raw = os.getenv("WATCHER_SYMBOL_STALE_SEC", "").strip()
    if raw:
        try:
            return max(30, int(raw))
        except ValueError:
            pass
    return max(180, bar_interval * 3)


def publish_watcher_status(payload: dict[str, Any]) -> None:
    """Upsert watcher heartbeat (no-op without DATABASE_URL)."""
    url = _database_url()
    if not url:
        return

    body = dict(payload)
    body["updated_at"] = datetime.now(timezone.utc).isoformat()

    try:
        conn = connect_psycopg2(url)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO runtime_controls (key, value, updated_at)
                    VALUES (%s, %s::jsonb, NOW())
                    ON CONFLICT (key) DO UPDATE SET
                        value = EXCLUDED.value,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (_WATCHER_STATUS_KEY, json.dumps(body, default=str)),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("Watcher heartbeat write failed: %s", exc)


def read_watcher_status() -> Optional[dict[str, Any]]:
    url = _database_url()
    if not url:
        return None
    try:
        conn = connect_psycopg2(url)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT value, updated_at
                    FROM runtime_controls
                    WHERE key = %s
                    """,
                    (_WATCHER_STATUS_KEY,),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        if not row:
            return None
        value = row[0]
        if isinstance(value, str):
            value = json.loads(value)
        if not isinstance(value, dict):
            return None
        updated_at = row[1]
        if updated_at is not None:
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            value.setdefault("updated_at", updated_at.isoformat())
        return value
    except Exception as exc:
        logger.warning("Watcher heartbeat read failed: %s", exc)
        return None


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def is_watcher_online(status: dict[str, Any] | None, now: datetime | None = None) -> bool:
    if not status or not status.get("running"):
        return False
    now = now or datetime.now(timezone.utc)
    updated = _parse_iso(status.get("updated_at"))
    if updated is None:
        return False
    return (now - updated).total_seconds() <= heartbeat_stale_seconds()


def symbol_last_bar_at(status: dict[str, Any] | None, symbol: str) -> datetime | None:
    if not status:
        return None
    raw = (status.get("symbol_last_bar") or {}).get(symbol.upper())
    return _parse_iso(raw if isinstance(raw, str) else None)


def compute_feed_status(
    *,
    watcher_online: bool,
    symbol: str,
    session_open: bool,
    watcher_status: dict[str, Any] | None,
    now: datetime | None = None,
) -> str:
    """
    feeding | stale | offline | session_closed

    offline — worker heartbeat missing or stale
    feeding — worker reported a recent bar for this symbol
    session_closed — market session closed and not feeding
    stale — worker online but no recent bar for this symbol
    """
    if not watcher_online:
        return "offline"

    now = now or datetime.now(timezone.utc)
    last = symbol_last_bar_at(watcher_status, symbol)
    if last is not None:
        age = (now - last).total_seconds()
        if age <= symbol_feed_stale_seconds():
            return "feeding"

    if not session_open:
        return "session_closed"
    return "stale"


def build_watcher_dashboard_summary(
    charts: list[dict[str, Any]],
    watcher_status: dict[str, Any] | None,
) -> dict[str, Any]:
    online = is_watcher_online(watcher_status)
    feeding = sum(1 for c in charts if c.get("feed_status") == "feeding")
    stale = sum(1 for c in charts if c.get("feed_status") == "stale")
    offline = sum(1 for c in charts if c.get("feed_status") == "offline")
    session_closed = sum(1 for c in charts if c.get("feed_status") == "session_closed")
    exec_ready = sum(1 for c in charts if c.get("execution_ready"))

    return {
        "online": online,
        "running": bool(watcher_status and watcher_status.get("running")),
        "mode": (watcher_status or {}).get("mode") or os.getenv("WATCHER_MODE", "paper"),
        "updated_at": (watcher_status or {}).get("updated_at"),
        "started_at": (watcher_status or {}).get("started_at"),
        "symbol_count": len(charts),
        "feeding": feeding,
        "stale": stale,
        "offline": offline,
        "session_closed": session_closed,
        "execution_ready_count": exec_ready,
        "kill_switch": (watcher_status or {}).get("kill_switch"),
    }
