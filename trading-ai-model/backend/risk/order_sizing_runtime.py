"""
Cross-process order sizing in Postgres runtime_controls.

Resolution order (read):
  1. In-memory override in this process (fast path after UI write)
  2. Postgres row runtime_controls.key = 'order_sizing' (when DATABASE_URL set)
     — cached in-process for ORDER_SIZING_CACHE_TTL_SEC (default 2s)
  3. RISK_DEFAULT_ORDER_USD from .env (clamped to min/max)

Write path (set_order_sizing):
  Upsert Postgres, set in-memory override.
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Generator, Optional

from config.settings import get_settings
from data.storage.pg_connect import connect_psycopg2, is_database_url_placeholder

logger = logging.getLogger(__name__)

_ORDER_SIZING_KEY = "order_sizing"
_memory_override: OrderSizingState | None = None
_env_defaults_cached: OrderSizingState | None = None
_postgres_cache_state: OrderSizingState | None = None
_postgres_cache_fetched_at: float = 0.0
_POSTGRES_CACHE_TTL_SEC = float(os.getenv("ORDER_SIZING_CACHE_TTL_SEC", "2"))

RUNTIME_CONTROLS_DDL = """
CREATE TABLE IF NOT EXISTS runtime_controls (
    key         TEXT PRIMARY KEY,
    value       JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


@dataclass(frozen=True)
class OrderSizingLimits:
    min_usd: float
    max_usd: float
    coinbase_account_cap_usd: float
    oanda_account_cap_usd: float


@dataclass(frozen=True)
class OrderSizingState:
    coinbase_order_usd: float
    oanda_order_usd: float
    updated_at: Optional[str] = None
    source: str = "env"  # env | postgres | memory


def _limits() -> OrderSizingLimits:
    return OrderSizingLimits(
        min_usd=float(os.getenv("RISK_MIN_ORDER_USD", "5")),
        max_usd=float(os.getenv("RISK_MAX_ORDER_USD", "50")),
        coinbase_account_cap_usd=float(os.getenv("RISK_ACCOUNT_CAP_USD", "1000")),
        oanda_account_cap_usd=float(os.getenv("OANDA_ACCOUNT_CAP_USD", "100")),
    )


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _env_defaults() -> OrderSizingState:
    """Startup defaults from env — cached so runtime env mutation does not change them."""
    global _env_defaults_cached
    if _env_defaults_cached is not None:
        return _env_defaults_cached

    lim = _limits()
    default = float(os.getenv("RISK_DEFAULT_ORDER_USD", "5"))
    default = _clamp(default, lim.min_usd, lim.max_usd)
    _env_defaults_cached = OrderSizingState(
        coinbase_order_usd=default,
        oanda_order_usd=default,
        source="env",
    )
    return _env_defaults_cached


def _database_url() -> str | None:
    url = (get_settings().database_url or os.getenv("DATABASE_URL", "")).strip()
    if not url or is_database_url_placeholder(url):
        return None
    return url


@contextmanager
def _connect() -> Generator[Any, None, None]:
    url = _database_url()
    if not url:
        raise RuntimeError("DATABASE_URL not configured")
    conn = connect_psycopg2(url)
    try:
        yield conn
    finally:
        conn.close()


def _ensure_table(conn) -> None:
    defaults = _env_defaults()
    seed = json.dumps(
        {
            "coinbase_order_usd": defaults.coinbase_order_usd,
            "oanda_order_usd": defaults.oanda_order_usd,
        }
    )
    with conn.cursor() as cur:
        cur.execute(RUNTIME_CONTROLS_DDL)
        cur.execute(
            """
            INSERT INTO runtime_controls (key, value)
            VALUES (%s, %s::jsonb)
            ON CONFLICT (key) DO NOTHING
            """,
            (_ORDER_SIZING_KEY, seed),
        )


def _parse_postgres_row(row) -> tuple[Optional[OrderSizingState], Optional[datetime]]:
    if not row or row[0] is None or row[1] is None:
        return None, None
    try:
        coinbase = float(row[0])
        oanda = float(row[1])
    except (TypeError, ValueError):
        return None, None
    updated_at = row[2]
    if updated_at is not None and updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return (
        OrderSizingState(
            coinbase_order_usd=coinbase,
            oanda_order_usd=oanda,
            updated_at=updated_at.isoformat() if updated_at else None,
            source="postgres",
        ),
        updated_at,
    )


def _read_postgres() -> tuple[Optional[OrderSizingState], Optional[datetime]]:
    if not _database_url():
        return None, None
    try:
        with _connect() as conn:
            _ensure_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        value->>'coinbase_order_usd',
                        value->>'oanda_order_usd',
                        updated_at
                    FROM runtime_controls
                    WHERE key = %s
                    """,
                    (_ORDER_SIZING_KEY,),
                )
                row = cur.fetchone()
        return _parse_postgres_row(row)
    except Exception as exc:
        logger.warning("Order sizing Postgres read failed: %s", exc)
        return None, None


def _read_postgres_cached() -> Optional[OrderSizingState]:
    global _postgres_cache_state, _postgres_cache_fetched_at

    now = time.monotonic()
    if (
        _postgres_cache_state is not None
        and (now - _postgres_cache_fetched_at) < _POSTGRES_CACHE_TTL_SEC
    ):
        return _postgres_cache_state

    state, _ = _read_postgres()
    if state is not None:
        _postgres_cache_state = state
        _postgres_cache_fetched_at = now
    return state


def _invalidate_postgres_cache() -> None:
    global _postgres_cache_state, _postgres_cache_fetched_at
    _postgres_cache_state = None
    _postgres_cache_fetched_at = 0.0


def _write_postgres(coinbase_order_usd: float, oanda_order_usd: float) -> Optional[datetime]:
    if not _database_url():
        return None
    now = datetime.now(timezone.utc)
    payload = json.dumps(
        {
            "coinbase_order_usd": coinbase_order_usd,
            "oanda_order_usd": oanda_order_usd,
        }
    )
    try:
        with _connect() as conn:
            _ensure_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO runtime_controls (key, value, updated_at)
                    VALUES (%s, %s::jsonb, %s)
                    ON CONFLICT (key) DO UPDATE SET
                        value = EXCLUDED.value,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (_ORDER_SIZING_KEY, payload, now),
                )
            conn.commit()
        _invalidate_postgres_cache()
        return now
    except Exception as exc:
        logger.warning("Order sizing Postgres write failed: %s", exc)
        return None


def _resolve_state() -> OrderSizingState:
    if _memory_override is not None:
        return _memory_override
    pg_state = _read_postgres_cached()
    if pg_state is not None:
        return pg_state
    return _env_defaults()


def _state_to_dict(state: OrderSizingState) -> dict[str, Any]:
    lim = _limits()
    return {
        "coinbase_order_usd": state.coinbase_order_usd,
        "oanda_order_usd": state.oanda_order_usd,
        "updated_at": state.updated_at,
        "source": state.source,
        "limits": {
            "min_usd": lim.min_usd,
            "max_usd": lim.max_usd,
            "coinbase_account_cap_usd": lim.coinbase_account_cap_usd,
            "oanda_account_cap_usd": lim.oanda_account_cap_usd,
        },
    }


def get_order_sizing() -> dict[str, Any]:
    """Return current sizing + limits for API/dashboard."""
    return _state_to_dict(_resolve_state())


def set_order_sizing(*, coinbase_order_usd: float, oanda_order_usd: float) -> dict[str, Any]:
    """Persist clamped order sizes to Postgres and in-memory override."""
    global _memory_override

    lim = _limits()
    cb = _clamp(
        coinbase_order_usd,
        lim.min_usd,
        min(lim.max_usd, lim.coinbase_account_cap_usd),
    )
    oa = _clamp(
        oanda_order_usd,
        lim.min_usd,
        min(lim.max_usd, lim.oanda_account_cap_usd),
    )

    _env_defaults()  # cache startup defaults before any side effects
    updated_at = _write_postgres(cb, oa)
    updated_iso = updated_at.isoformat() if updated_at else None
    _memory_override = OrderSizingState(
        coinbase_order_usd=cb,
        oanda_order_usd=oa,
        updated_at=updated_iso,
        source="memory",
    )

    logger.info(
        "Order sizing updated via UI | coinbase=$%.2f oanda=$%.2f",
        cb,
        oa,
    )
    result = get_order_sizing()
    if updated_iso and result.get("updated_at") is None:
        result["updated_at"] = updated_iso
    return result


def coinbase_order_usd() -> float:
    return _resolve_state().coinbase_order_usd


def oanda_order_usd() -> float:
    return _resolve_state().oanda_order_usd


def reset_order_sizing_runtime() -> None:
    """Clear in-memory override, env cache, and Postgres read cache — for tests."""
    global _memory_override, _env_defaults_cached
    _memory_override = None
    _env_defaults_cached = None
    _invalidate_postgres_cache()
