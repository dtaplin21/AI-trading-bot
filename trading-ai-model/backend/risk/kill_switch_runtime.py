"""
Cross-process kill switch runtime.

Resolution order (read):
  1. In-memory override in this process (fast path after UI/MCP write)
  2. Postgres row runtime_controls.key = 'kill_switch' (when DATABASE_URL set)
     — cached in-process for KILL_SWITCH_CACHE_TTL_SEC (default 2s)
  3. RISK_KILL_SWITCH from .env

Write path (set_kill_switch_enabled):
  Upsert Postgres, set in-memory override, sync os.environ for legacy callers.
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Generator, Optional

from config.settings import get_settings
from data.storage.pg_connect import connect_psycopg2, is_database_url_placeholder

logger = logging.getLogger(__name__)

_KILL_SWITCH_KEY = "kill_switch"
_memory_override: bool | None = None
_env_default_cached: bool | None = None
_postgres_cache_enabled: bool | None = None
_postgres_cache_updated_at: datetime | None = None
_postgres_cache_fetched_at: float = 0.0
_POSTGRES_CACHE_TTL_SEC = float(os.getenv("KILL_SWITCH_CACHE_TTL_SEC", "2"))

RUNTIME_CONTROLS_DDL = """
CREATE TABLE IF NOT EXISTS runtime_controls (
    key         TEXT PRIMARY KEY,
    value       JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

_KILL_SWITCH_SEED = json.dumps({"enabled": False})


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("true", "1", "yes")


def _env_default() -> bool:
    """Startup RISK_KILL_SWITCH — cached so runtime os.environ updates do not change it."""
    global _env_default_cached
    if _env_default_cached is None:
        _env_default_cached = _parse_bool(os.getenv("RISK_KILL_SWITCH", "false"), False)
    return _env_default_cached


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
    with conn.cursor() as cur:
        cur.execute(RUNTIME_CONTROLS_DDL)
        cur.execute(
            """
            INSERT INTO runtime_controls (key, value)
            VALUES (%s, %s::jsonb)
            ON CONFLICT (key) DO NOTHING
            """,
            (_KILL_SWITCH_KEY, _KILL_SWITCH_SEED),
        )


def _read_postgres() -> tuple[Optional[bool], Optional[datetime]]:
    if not _database_url():
        return None, None
    try:
        with _connect() as conn:
            _ensure_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT (value->>'enabled')::boolean, updated_at
                    FROM runtime_controls
                    WHERE key = %s
                    """,
                    (_KILL_SWITCH_KEY,),
                )
                row = cur.fetchone()
        if not row or row[0] is None:
            return None, None
        updated_at = row[1]
        if updated_at is not None and updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        return bool(row[0]), updated_at
    except Exception as exc:
        logger.warning("Kill switch Postgres read failed: %s", exc)
        return None, None


def _read_postgres_cached() -> tuple[Optional[bool], Optional[datetime]]:
    """Postgres read with in-process TTL — one query per TTL window per process."""
    global _postgres_cache_enabled, _postgres_cache_updated_at, _postgres_cache_fetched_at

    now = time.monotonic()
    if (
        _postgres_cache_enabled is not None
        and (now - _postgres_cache_fetched_at) < _POSTGRES_CACHE_TTL_SEC
    ):
        return _postgres_cache_enabled, _postgres_cache_updated_at

    enabled, updated_at = _read_postgres()
    if enabled is not None:
        _postgres_cache_enabled = enabled
        _postgres_cache_updated_at = updated_at
        _postgres_cache_fetched_at = now
    return enabled, updated_at


def _invalidate_postgres_cache() -> None:
    global _postgres_cache_enabled, _postgres_cache_updated_at, _postgres_cache_fetched_at
    _postgres_cache_enabled = None
    _postgres_cache_updated_at = None
    _postgres_cache_fetched_at = 0.0


def _write_postgres(enabled: bool) -> Optional[datetime]:
    if not _database_url():
        return None
    now = datetime.now(timezone.utc)
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
                    (_KILL_SWITCH_KEY, json.dumps({"enabled": enabled}), now),
                )
            conn.commit()
        _invalidate_postgres_cache()
        return now
    except Exception as exc:
        logger.warning("Kill switch Postgres write failed: %s", exc)
        return None


def is_kill_switch_active() -> bool:
    if _memory_override is not None:
        return _memory_override
    pg_enabled, _ = _read_postgres_cached()
    if pg_enabled is not None:
        return pg_enabled
    return _env_default()


def get_kill_switch_status() -> dict[str, Any]:
    env_default = _env_default()
    if _memory_override is not None:
        status = {
            "enabled": _memory_override,
            "env_default": env_default,
            "updated_at": None,
            "source": "memory",
        }
    else:
        pg_enabled, updated_at = _read_postgres()
        if pg_enabled is not None:
            status = {
                "enabled": pg_enabled,
                "env_default": env_default,
                "updated_at": updated_at.isoformat() if updated_at else None,
                "source": "postgres",
            }
        else:
            status = {
                "enabled": env_default,
                "env_default": env_default,
                "updated_at": None,
                "source": "env",
            }

    status["effective"] = is_kill_switch_active()
    return status


async def set_kill_switch_enabled(enabled: bool) -> dict[str, Any]:
    """Enable or disable the kill switch across Postgres, memory, and env."""
    global _memory_override

    was_active = is_kill_switch_active()
    _env_default()  # cache startup value before os.environ mutation
    updated_at = _write_postgres(enabled)
    _memory_override = enabled
    os.environ["RISK_KILL_SWITCH"] = "true" if enabled else "false"

    flatten_summary = None
    if enabled and not was_active:
        from risk.kill_switch_actions import flatten_all_positions

        flatten_summary = await flatten_all_positions()
        logger.warning("Kill switch ENABLED via UI")
    elif enabled:
        logger.warning("Kill switch ENABLED via UI")
    else:
        from risk.kill_switch_actions import reset_kill_flatten_arm

        reset_kill_flatten_arm()
        logger.info("Kill switch disabled via UI")

    status = get_kill_switch_status()
    if updated_at is not None:
        status["updated_at"] = updated_at.isoformat()
    if flatten_summary is not None:
        status["flatten"] = flatten_summary
    return status


def reset_kill_switch_runtime() -> None:
    """Clear in-memory override, env cache, and Postgres read cache — for tests."""
    global _memory_override, _env_default_cached
    _memory_override = None
    _env_default_cached = None
    _invalidate_postgres_cache()
    from risk.kill_switch_actions import reset_kill_flatten_arm

    reset_kill_flatten_arm()
