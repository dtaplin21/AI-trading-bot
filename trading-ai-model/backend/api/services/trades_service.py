"""
Build closed-trade history for GET /trades from live DB rows, confluence outcomes,
and the learning-agent outcomes log.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from config.symbols import SYMBOL_MAP
from data.storage.pg_connect import connect_psycopg2, is_database_url_placeholder
from config.settings import get_settings

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
OUTCOMES_LOG_PATH = Path(
    os.getenv("LEARNING_OUTCOMES_LOG", str(_BACKEND_DIR / "logs" / "outcomes.jsonl"))
)

_EXIT_REASON_MAP = {
    "TP": "target",
    "SL": "stop",
    "target": "target",
    "stop": "stop",
    "manual": "manual",
    "timeout": "timeout",
}


def build_closed_trades(limit: int = 100) -> dict[str, Any]:
    """Return dashboard-shaped closed trades, newest first."""
    trades: list[dict[str, Any]] = []
    seen: set[str] = set()
    outcomes_by_id = _load_outcomes_log()

    for row in _load_live_trades(limit):
        trade_id = str(row.get("trade_id") or "")
        if not trade_id or trade_id in seen:
            continue
        trades.append(_map_live_trade(row))
        seen.add(trade_id)

    for row in _load_confluence_outcomes(limit):
        snapshot_id = str(row.get("snapshot_id") or "")
        if not snapshot_id or snapshot_id in seen:
            continue
        extra = outcomes_by_id.get(snapshot_id, {})
        trades.append(_map_confluence_outcome(row, extra))
        seen.add(snapshot_id)

    for snapshot_id, row in outcomes_by_id.items():
        if snapshot_id in seen:
            continue
        trades.append(_map_outcome_log(row, snapshot_id))
        seen.add(snapshot_id)

    trades.sort(key=lambda t: t.get("timestamp") or "", reverse=True)
    trimmed = trades[:limit]
    return {
        "trades": trimmed,
        "count": len(trimmed),
        "source": "live" if trimmed else "empty",
    }


def _tick_meta(symbol: str) -> tuple[float, float]:
    spec = SYMBOL_MAP.get(symbol.upper())
    if spec:
        return spec.tick_size, spec.tick_value
    return 0.25, 1.25


def _pnl_ticks(
    symbol: str,
    entry: float,
    exit_price: float,
    direction: str,
    pnl_dollars: float,
    quantity: float = 1.0,
) -> float:
    tick_size, tick_value = _tick_meta(symbol)
    diff = exit_price - entry
    if direction == "short":
        diff = -diff
    if tick_size > 0:
        return round(diff / tick_size, 1)
    if tick_value > 0 and quantity > 0:
        return round(pnl_dollars / (tick_value * quantity), 1)
    return 0.0


def _normalize_direction(raw: Any, entry: float, exit_price: float, pnl: float) -> str:
    text = str(raw or "").lower()
    if text in ("long", "buy", "bull", "bullish"):
        return "long"
    if text in ("short", "sell", "bear", "bearish"):
        return "short"
    if entry != exit_price:
        price_up = exit_price > entry
        if pnl > 0:
            return "long" if price_up else "short"
        if pnl < 0:
            return "short" if price_up else "long"
    return "long"


def _infer_levels(
    entry: float,
    exit_price: float,
    direction: str,
    hit_target: bool,
    hit_stop: bool,
) -> tuple[float, float]:
    span = abs(exit_price - entry) or max(abs(entry) * 0.001, 0.25)
    if direction == "long":
        if hit_stop:
            return exit_price, entry + span
        if hit_target:
            return entry - span, exit_price
        return entry - span, entry + span
    if hit_stop:
        return exit_price, entry - span
    if hit_target:
        return entry + span, exit_price
    return entry - span, entry + span


def _exit_reason(raw: Any, hit_target: bool, hit_stop: bool) -> str:
    if hit_target:
        return "target"
    if hit_stop:
        return "stop"
    text = str(raw or "").strip()
    return _EXIT_REASON_MAP.get(text, _EXIT_REASON_MAP.get(text.upper(), "manual"))


def _iso_timestamp(raw: Any) -> str:
    if raw is None:
        return datetime.now(tz=timezone.utc).isoformat()
    if isinstance(raw, datetime):
        dt = raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    text = str(raw).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).isoformat()
    except ValueError:
        return datetime.now(tz=timezone.utc).isoformat()


def _map_live_trade(row: dict[str, Any]) -> dict[str, Any]:
    symbol = str(row.get("symbol") or "").upper()
    entry = float(row.get("entry_price") or 0)
    exit_price = float(row.get("exit_price") or entry)
    stop = float(row.get("stop_price") or 0)
    target = float(row.get("target_price") or 0)
    quantity = float(row.get("quantity") or 1)
    side = row.get("side")
    pnl_raw = row.get("pnl")
    direction = _normalize_direction(side, entry, exit_price, float(pnl_raw or 0))
    if pnl_raw is not None:
        pnl_dollars = float(pnl_raw)
    else:
        _, tick_value = _tick_meta(symbol)
        ticks = _pnl_ticks(symbol, entry, exit_price, direction, 0, quantity)
        pnl_dollars = round(ticks * tick_value * quantity, 2)
    hit_target = str(row.get("exit_reason") or "").upper() == "TP"
    hit_stop = str(row.get("exit_reason") or "").upper() == "SL"
    if not stop or not target:
        stop, target = _infer_levels(entry, exit_price, direction, hit_target, hit_stop)

    return {
        "id": str(row.get("trade_id")),
        "symbol": symbol,
        "timestamp": _iso_timestamp(row.get("closed_at") or row.get("exit_time")),
        "direction": direction,
        "entry_price": entry,
        "exit_price": exit_price,
        "stop_loss": stop,
        "take_profit": target,
        "pnl_dollars": round(pnl_dollars, 2),
        "pnl_ticks": _pnl_ticks(symbol, entry, exit_price, direction, pnl_dollars, quantity),
        "exit_reason": _exit_reason(row.get("exit_reason"), hit_target, hit_stop),
        "signal_rank": int(row.get("signal_rank") or 0),
        "source": "live_trades",
        "broker": row.get("broker"),
    }


def _direction_from_confluence(confluence: Any) -> Optional[str]:
    if not isinstance(confluence, dict):
        return None
    consensus = confluence.get("consensus_direction")
    if consensus == 1:
        return "long"
    if consensus == -1:
        return "short"
    weighted = confluence.get("weighted_consensus")
    if isinstance(weighted, (int, float)):
        if weighted > 0.05:
            return "long"
        if weighted < -0.05:
            return "short"
    cluster = confluence.get("strongest_cluster")
    if isinstance(cluster, dict):
        direction = cluster.get("direction")
        if direction == 1:
            return "long"
        if direction == -1:
            return "short"
    return None


def _map_confluence_outcome(row: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    symbol = str(row.get("symbol") or extra.get("symbol") or "").upper()
    entry = float(extra.get("entry_price") or 0)
    exit_price = float(extra.get("exit_price") or 0)
    pnl_dollars = float(row.get("actual_pnl") if row.get("actual_pnl") is not None else extra.get("pnl") or 0)
    hit_target = bool(row.get("hit_target") or extra.get("hit_target"))
    hit_stop = bool(row.get("hit_stop") or extra.get("hit_stop"))

    direction = _direction_from_confluence(row.get("confluence"))
    if not direction and entry and exit_price:
        direction = _normalize_direction(None, entry, exit_price, pnl_dollars)
    elif not direction:
        direction = "long"

    if not entry or not exit_price:
        move = abs(pnl_dollars) / max(_tick_meta(symbol)[1], 0.01)
        tick_size, _ = _tick_meta(symbol)
        move = move * tick_size
        if direction == "long":
            entry = exit_price - move if exit_price else 0.0
            if not exit_price:
                exit_price = entry + move
        else:
            entry = exit_price + move if exit_price else 0.0
            if not exit_price:
                exit_price = entry - move

    stop, target = _infer_levels(entry, exit_price, direction, hit_target, hit_stop)

    return {
        "id": str(row.get("snapshot_id")),
        "symbol": symbol,
        "timestamp": _iso_timestamp(row.get("closed_at") or extra.get("timestamp")),
        "direction": direction,
        "entry_price": entry,
        "exit_price": exit_price,
        "stop_loss": stop,
        "take_profit": target,
        "pnl_dollars": round(pnl_dollars, 2),
        "pnl_ticks": _pnl_ticks(symbol, entry, exit_price, direction, pnl_dollars),
        "exit_reason": _exit_reason(extra.get("exit_reason"), hit_target, hit_stop),
        "signal_rank": int(row.get("signal_rank") or extra.get("signal_rank") or 0),
        "source": "confluence_outcomes",
    }


def _map_outcome_log(row: dict[str, Any], snapshot_id: str) -> dict[str, Any]:
    symbol = str(row.get("symbol") or "").upper()
    entry = float(row.get("entry_price") or 0)
    exit_price = float(row.get("exit_price") or entry)
    pnl_dollars = float(row.get("pnl") or 0)
    hit_target = bool(row.get("hit_target"))
    hit_stop = bool(row.get("hit_stop"))
    direction = _normalize_direction(row.get("direction"), entry, exit_price, pnl_dollars)
    stop, target = _infer_levels(entry, exit_price, direction, hit_target, hit_stop)

    return {
        "id": snapshot_id,
        "symbol": symbol,
        "timestamp": _iso_timestamp(row.get("timestamp")),
        "direction": direction,
        "entry_price": entry,
        "exit_price": exit_price,
        "stop_loss": stop,
        "take_profit": target,
        "pnl_dollars": round(pnl_dollars, 2),
        "pnl_ticks": _pnl_ticks(symbol, entry, exit_price, direction, pnl_dollars),
        "exit_reason": _exit_reason(row.get("exit_reason"), hit_target, hit_stop),
        "signal_rank": int(row.get("signal_rank") or 0),
        "source": "outcomes_log",
    }


def _database_url() -> str:
    return (get_settings().database_url or os.getenv("DATABASE_URL", "")).strip()


def _load_live_trades(limit: int) -> list[dict[str, Any]]:
    url = _database_url()
    if not url or is_database_url_placeholder(url):
        return []
    sql = """
        SELECT trade_id, symbol, side, entry_price, target_price, stop_price,
               quantity, exit_price, exit_reason, exit_time, closed_at,
               pnl, pnl_pct, broker, status
        FROM live_trades
        WHERE UPPER(status) = 'CLOSED'
          AND exit_price IS NOT NULL
        ORDER BY COALESCE(closed_at, exit_time, opened_at) DESC
        LIMIT %s
    """
    try:
        conn = connect_psycopg2(url)
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, (limit,))
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as exc:
        logger.debug("trades_service: live_trades query failed: %s", exc)
        return []


def _load_confluence_outcomes(limit: int) -> list[dict[str, Any]]:
    url = _database_url()
    if not url or is_database_url_placeholder(url):
        return []
    sql = """
        SELECT snapshot_id, symbol, signal_rank, actual_pnl, actual_r_multiple,
               hit_target, hit_stop, closed_at, confluence
        FROM confluence_outcomes
        WHERE actual_pnl IS NOT NULL
        ORDER BY closed_at DESC
        LIMIT %s
    """
    try:
        conn = connect_psycopg2(url)
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, (limit,))
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        for row in rows:
            conf = row.get("confluence")
            if isinstance(conf, str):
                try:
                    row["confluence"] = json.loads(conf)
                except json.JSONDecodeError:
                    row["confluence"] = None
        return rows
    except Exception as exc:
        logger.debug("trades_service: confluence_outcomes query failed: %s", exc)
        return []


def _load_outcomes_log() -> dict[str, dict[str, Any]]:
    path = OUTCOMES_LOG_PATH
    if not path.is_absolute():
        path = _BACKEND_DIR / path
    if not path.exists():
        return {}

    by_id: dict[str, dict[str, Any]] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        snapshot_id = str(row.get("snapshot_id") or "")
        if snapshot_id:
            by_id[snapshot_id] = row
    return by_id
