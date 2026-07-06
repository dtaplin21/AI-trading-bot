-- 010_live_trades.sql — live order attempts and open/closed positions

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
