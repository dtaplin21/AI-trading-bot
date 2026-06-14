"""
live/live_execution_agent.py

Live execution for LevelSetup trades. Routes orders via BrokerRouter.
Only module that calls real broker APIs for live mode.
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

from config.coinbase_symbols import is_coinbase_tradable
from config.execution_config import (
    coinbase_live_allowed,
    oanda_live_allowed,
    resolve_execution_mode,
)
from config.oanda_symbols import is_oanda_tradable
from config.settings import get_settings
from data.storage.pg_connect import connect_psycopg2, is_database_url_placeholder
from live.broker_router import get_broker_router
from live.live_position_monitor import LivePosition, get_position_monitor
from pipeline.level_setup import LevelSetup
from risk.kill_switch_runtime import is_kill_switch_active

logger = logging.getLogger("LiveExecutionAgent")

LIVE_TRADES_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_trades (
    id              BIGSERIAL PRIMARY KEY,
    trade_id        TEXT NOT NULL UNIQUE,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    entry_price     FLOAT NOT NULL,
    target_price    FLOAT NOT NULL,
    stop_price      FLOAT NOT NULL,
    quantity        FLOAT NOT NULL,
    tp_pct          FLOAT,
    sl_pct          FLOAT,
    ev_pct          FLOAT,
    touch_count     INT,
    hold_rate       FLOAT,
    level_price     FLOAT,
    broker_order_id TEXT,
    broker          TEXT,
    status          TEXT DEFAULT 'OPEN',
    opened_at       TIMESTAMPTZ DEFAULT NOW(),
    closed_at       TIMESTAMPTZ,
    exit_time       TIMESTAMPTZ,
    exit_price      FLOAT,
    exit_reason     TEXT,
    bars_held       INT,
    pnl             FLOAT,
    pnl_pct         FLOAT,
    outcome         TEXT
);
CREATE INDEX IF NOT EXISTS idx_live_trades_symbol ON live_trades(symbol, status);
"""

LIVE_TRADES_ALTER = """
ALTER TABLE live_trades ADD COLUMN IF NOT EXISTS exit_time TIMESTAMPTZ;
ALTER TABLE live_trades ADD COLUMN IF NOT EXISTS exit_reason TEXT;
ALTER TABLE live_trades ADD COLUMN IF NOT EXISTS bars_held INT;
ALTER TABLE live_trades ADD COLUMN IF NOT EXISTS pnl_pct FLOAT;
ALTER TABLE live_trades ADD COLUMN IF NOT EXISTS outcome TEXT;
"""

_schema_ready = False


def _kill_switch() -> bool:
    return is_kill_switch_active()


def _max_concurrent() -> int:
    return int(os.getenv("RISK_MAX_CONCURRENT", "3"))


def _max_daily_loss_pct() -> float:
    return float(os.getenv("RISK_MAX_DAILY_LOSS_PCT", "2.0"))


def _max_position_usd() -> float:
    return float(os.getenv("RISK_MAX_POSITION_USD", "500.0"))


def _risk_pct_per_trade() -> float:
    return float(os.getenv("RISK_PCT_PER_TRADE", "1.0"))


def _database_url() -> str:
    return (get_settings().database_url or os.getenv("DATABASE_URL", "")).strip()


def ensure_live_trades_schema() -> None:
    global _schema_ready
    url = _database_url()
    if _schema_ready or not url or is_database_url_placeholder(url):
        return
    try:
        conn = connect_psycopg2(url)
        cur = conn.cursor()
        cur.execute(LIVE_TRADES_SCHEMA)
        cur.execute(LIVE_TRADES_ALTER)
        conn.commit()
        cur.close()
        conn.close()
        _schema_ready = True
    except Exception as exc:
        logger.error("ensure_live_trades_schema: %s", exc)


class LiveExecutionAgent:
    """Submit live orders after risk approval on a LevelSetup."""

    def __init__(self) -> None:
        self._router = get_broker_router()
        self._monitor = get_position_monitor()
        logger.info(
            "LiveExecutionAgent ready | max_concurrent=%d max_position_usd=%.0f "
            "risk_pct=%.1f%% max_daily_loss=%.1f%%",
            _max_concurrent(),
            _max_position_usd(),
            _risk_pct_per_trade(),
            _max_daily_loss_pct(),
        )

    async def execute_level(self, setup: LevelSetup) -> bool:
        if _kill_switch():
            logger.warning("KILL SWITCH ACTIVE — blocking order for %s", setup.symbol)
            return False

        if resolve_execution_mode() == "paper":
            logger.warning(
                "%s: live execution blocked — PAPER_TRADING_ENABLED or live flags not set",
                setup.symbol,
            )
            return False

        symbol = setup.symbol.upper()
        if is_coinbase_tradable(symbol) and not coinbase_live_allowed():
            logger.warning("%s: Coinbase live not enabled", symbol)
            return False
        if is_oanda_tradable(symbol) and not oanda_live_allowed():
            logger.warning("%s: OANDA live not enabled", symbol)
            return False

        open_count = len(self._monitor.open_positions())
        if open_count >= _max_concurrent():
            logger.info(
                "%s: max concurrent positions reached (%d/%d) — skipping",
                setup.symbol,
                open_count,
                _max_concurrent(),
            )
            return False

        broker = self._router.get(setup.symbol)
        account = await broker.get_account()
        daily_loss_pct = (
            abs(min(account.realized_pnl_day, 0)) / max(account.cash_balance, 1) * 100
        )
        if daily_loss_pct >= _max_daily_loss_pct():
            logger.warning(
                "Daily loss limit hit: %.2f%% >= %.2f%% — halting trading",
                daily_loss_pct,
                _max_daily_loss_pct(),
            )
            return False

        for pos in self._monitor.open_positions():
            if pos.symbol == setup.symbol:
                logger.info("%s: already have open position — skipping duplicate", setup.symbol)
                return False

        quantity = self._size_position(setup, account.cash_balance)
        if quantity <= 0:
            logger.warning("%s: position size computed as 0 — skipping", setup.symbol)
            return False

        trade_id = f"live-{setup.symbol}-{uuid.uuid4().hex[:8]}"
        logger.info(
            "LIVE ORDER → %s %s qty=%.4f entry=%.5f tp=%.5f sl=%.5f | "
            "EV=%.3f%% TP=%.3f%% SL=%.3f%% touches=%d",
            setup.symbol,
            setup.entry_side,
            quantity,
            setup.entry_price,
            setup.target_price,
            setup.stop_price,
            setup.expected_value_pct,
            setup.optimal_tp_pct,
            setup.optimal_sl_pct,
            setup.touch_count,
        )

        order = await broker.place_order(
            symbol=setup.symbol,
            side=setup.entry_side,
            quantity=quantity,
            order_type="MARKET",
            tp_price=setup.target_price,
            sl_price=setup.stop_price,
            client_ref=trade_id,
        )

        if order.status in ("REJECTED", "ERROR"):
            logger.error("Order rejected for %s: %s", setup.symbol, order.error_message)
            return False

        position = LivePosition(
            trade_id=trade_id,
            symbol=setup.symbol,
            side="LONG" if setup.entry_side == "BUY" else "SHORT",
            entry_price=order.filled_price or setup.entry_price,
            target_price=setup.target_price,
            stop_price=setup.stop_price,
            quantity=quantity,
            broker_order_id=order.broker_order_id,
            tp_pct=setup.optimal_tp_pct,
            sl_pct=setup.optimal_sl_pct,
            ev_pct=setup.expected_value_pct,
            touch_count=setup.touch_count,
            hold_rate=setup.hold_rate,
        )
        self._monitor.register(position)
        await self._record_open(trade_id, setup, order, quantity)
        return True

    def _size_position(self, setup: LevelSetup, account_balance: float) -> float:
        risk_usd = account_balance * (_risk_pct_per_trade() / 100)
        risk_usd = min(risk_usd, _max_position_usd())
        sl_distance = abs(setup.entry_price - setup.stop_price)
        if sl_distance <= 0:
            return 0.0

        raw_qty = risk_usd / sl_distance
        symbol = setup.symbol.upper()
        if symbol in ("MES", "ES", "MNQ", "NQ", "RTY", "CL", "GC", "ZB"):
            return max(1.0, round(raw_qty))
        if symbol in ("TSLA", "NVDA", "AAPL", "MSFT", "AMZN"):
            return max(1.0, round(raw_qty))
        if symbol in ("EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD"):
            return max(1000.0, round(raw_qty / 1000) * 1000)
        return round(raw_qty, 6)

    async def _record_open(
        self,
        trade_id: str,
        setup: LevelSetup,
        order,
        quantity: float,
    ) -> None:
        url = _database_url()
        if not url or is_database_url_placeholder(url):
            return
        ensure_live_trades_schema()
        try:
            conn = connect_psycopg2(url)
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO live_trades (
                    trade_id, symbol, side, entry_price, target_price, stop_price,
                    quantity, tp_pct, sl_pct, ev_pct, touch_count, hold_rate,
                    level_price, broker_order_id, broker, status, opened_at
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,
                    %s,%s,%s,
                    'OPEN', NOW()
                )
                ON CONFLICT (trade_id) DO NOTHING
                """,
                (
                    trade_id,
                    setup.symbol,
                    setup.entry_side,
                    order.filled_price or setup.entry_price,
                    setup.target_price,
                    setup.stop_price,
                    quantity,
                    setup.optimal_tp_pct,
                    setup.optimal_sl_pct,
                    setup.expected_value_pct,
                    setup.touch_count,
                    setup.hold_rate,
                    setup.level_price,
                    order.broker_order_id,
                    self._router.broker_name(setup.symbol),
                ),
            )
            conn.commit()
            cur.close()
            conn.close()
            logger.info("live_trades record written | trade_id=%s", trade_id)
        except Exception as exc:
            logger.error("_record_open DB write failed: %s", exc)


_agent: Optional[LiveExecutionAgent] = None


def get_live_execution_agent() -> LiveExecutionAgent:
    global _agent
    if _agent is None:
        _agent = LiveExecutionAgent()
    return _agent


def reset_live_execution_agent() -> None:
    global _agent
    _agent = None
