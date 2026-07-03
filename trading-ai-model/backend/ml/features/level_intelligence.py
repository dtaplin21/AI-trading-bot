"""
ml/features/level_intelligence.py

Forever-growing reversal intelligence system.

Every time price touches a level and we later know the outcome,
we record a complete snapshot and update the probability for that
specific price forever.

Connects to:
  - ChartWatchRunner — calls process_bar() on every completed bar
  - TradingPipelineSupervisor — calls get_watchlist() to know what to watch
  - FeaturePipeline / ReversalPredictor — level probability features
  - MCTS state — nearest_level_hold_rate in trade planning
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, cast

import numpy as np
import pandas as pd

from config.settings import get_settings
from data.storage.pg_connect import connect_psycopg2, is_database_url_placeholder

logger = logging.getLogger("level_intelligence")

OUTCOME_WINDOW = int(os.getenv("LEVEL_INTEL_OUTCOME_WINDOW", "20"))
TOUCH_LOOKBACK = int(os.getenv("LEVEL_INTEL_TOUCH_LOOKBACK", "5"))
REVERSAL_PCT = float(os.getenv("LEVEL_INTEL_REVERSAL_PCT", "0.15"))

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS price_levels (
    id              SERIAL PRIMARY KEY,
    symbol          TEXT NOT NULL,
    level_price     FLOAT NOT NULL,
    price_min       FLOAT NOT NULL,
    price_max       FLOAT NOT NULL,
    touch_count     INT DEFAULT 0,
    hold_count      INT DEFAULT 0,
    break_count     INT DEFAULT 0,
    support_count   INT DEFAULT 0,
    resistance_count INT DEFAULT 0,
    hold_rate       FLOAT DEFAULT 0,
    strength_score  FLOAT DEFAULT 0,
    role            TEXT DEFAULT 'UNKNOWN',
    first_seen      TIMESTAMPTZ DEFAULT NOW(),
    last_touched    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, level_price)
);

CREATE TABLE IF NOT EXISTS level_touches (
    id              BIGSERIAL PRIMARY KEY,
    symbol          TEXT NOT NULL,
    level_price     FLOAT NOT NULL,
    touched_at      TIMESTAMPTZ NOT NULL,
    price_at_touch  FLOAT NOT NULL,
    approach        TEXT NOT NULL,
    outcome         TEXT DEFAULT 'pending',
    volume_at_touch FLOAT,
    volume_ratio    FLOAT,
    rsi_14          FLOAT,
    macd_histogram  FLOAT,
    atr_pct         FLOAT,
    bb_position     FLOAT,
    session         TEXT,
    price_move_after FLOAT,
    bars_to_outcome INT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS level_watchlist (
    id              SERIAL PRIMARY KEY,
    symbol          TEXT NOT NULL,
    level_price     FLOAT NOT NULL,
    hold_rate       FLOAT NOT NULL,
    touch_count     INT NOT NULL,
    strength_score  FLOAT NOT NULL,
    role            TEXT NOT NULL,
    entry_side      TEXT NOT NULL,
    added_at        TIMESTAMPTZ DEFAULT NOW(),
    expires_at      TIMESTAMPTZ,
    is_active       BOOLEAN DEFAULT TRUE,
    UNIQUE(symbol, level_price)
);

CREATE INDEX IF NOT EXISTS idx_level_touches_symbol ON level_touches(symbol, touched_at DESC);
CREATE INDEX IF NOT EXISTS idx_price_levels_symbol  ON price_levels(symbol, hold_rate DESC);
CREATE INDEX IF NOT EXISTS idx_watchlist_active     ON level_watchlist(symbol, is_active);
"""


@dataclass
class TouchSnapshot:
    symbol: str
    level_price: float
    touched_at: datetime
    price_at_touch: float
    approach: str
    outcome: str
    volume_at_touch: float
    volume_ratio: float
    rsi_14: float
    macd_histogram: float
    atr_pct: float
    bb_position: float
    session: str
    price_move_after: float = 0.0
    bars_to_outcome: int = 0

    def to_db_row(self) -> dict:
        touched = self.touched_at
        if touched.tzinfo is None:
            touched = touched.replace(tzinfo=timezone.utc)
        return {
            "symbol": self.symbol,
            "level_price": self.level_price,
            "touched_at": touched.isoformat(),
            "price_at_touch": round(self.price_at_touch, 6),
            "approach": self.approach,
            "outcome": self.outcome,
            "volume_at_touch": round(self.volume_at_touch, 2),
            "volume_ratio": round(self.volume_ratio, 3),
            "rsi_14": round(self.rsi_14, 2),
            "macd_histogram": round(self.macd_histogram, 6),
            "atr_pct": round(self.atr_pct, 4),
            "bb_position": round(self.bb_position, 4),
            "session": self.session,
            "price_move_after": round(self.price_move_after, 4),
            "bars_to_outcome": self.bars_to_outcome,
        }


@dataclass
class PriceLevel:
    symbol: str
    level_price: float
    price_min: float
    price_max: float
    touch_count: int = 0
    hold_count: int = 0
    break_count: int = 0
    support_count: int = 0
    resistance_count: int = 0
    hold_rate: float = 0.0
    strength_score: float = 0.0
    role: str = "UNKNOWN"

    @property
    def is_reliable(self) -> bool:
        return self.touch_count >= 5

    @property
    def is_high_confidence(self) -> bool:
        return self.touch_count >= 15

    @property
    def wilson_score(self) -> float:
        n = self.touch_count
        if n == 0:
            return 0.0
        p = self.hold_rate
        z = 1.96
        num = p + z * z / (2 * n) - z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
        den = 1 + z * z / n
        return round(max(0.0, num / den), 4)

    def classify_role(self) -> str:
        total = self.support_count + self.resistance_count
        if total < 3:
            return "UNKNOWN"
        below_pct = self.support_count / total
        above_pct = self.resistance_count / total
        if below_pct >= 0.65:
            return "SUPPORT"
        if above_pct >= 0.65:
            return "RESISTANCE"
        return "MIXED"


def _database_url() -> str:
    return (get_settings().database_url or os.getenv("DATABASE_URL", "")).strip()


def _db_available() -> bool:
    url = _database_url()
    return bool(url) and not is_database_url_placeholder(url)


def _get_conn():
    return connect_psycopg2(_database_url())


def wilson_lower_bound(hold_rate: float, n: int) -> float:
    if n < 1:
        return 0.0
    p = hold_rate
    z = 1.96
    num = p + z * z / (2 * n) - z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    den = 1 + z * z / n
    return max(0.0, num / den)


_pending_touches: dict[str, list[dict[str, Any]]] = {}
_schema_ready = False


class LevelIntelligenceSystem:
    """Forever-growing reversal intelligence backed by Postgres."""

    CLUSTER_PCT = {
        "futures": 0.10,
        "forex": 0.05,
        "crypto": 0.20,
        "equity": 0.12,
    }

    WATCHLIST_MIN_HOLD_RATE = float(os.getenv("LEVEL_INTEL_WATCH_MIN_HOLD", "0.62"))
    WATCHLIST_MIN_TOUCHES = int(os.getenv("LEVEL_INTEL_WATCH_MIN_TOUCHES", "5"))
    WATCHLIST_MIN_STRENGTH = float(os.getenv("LEVEL_INTEL_WATCH_MIN_STRENGTH", "0.55"))

    def __init__(self, symbol: str, asset_class: str):
        self.symbol = symbol.upper()
        self.asset_class = asset_class
        self.cluster_pct = self.CLUSTER_PCT.get(asset_class, 0.10)

    def ensure_schema(self) -> None:
        global _schema_ready
        if _schema_ready or not _db_available():
            return
        try:
            conn = _get_conn()
            cur = conn.cursor()
            cur.execute(SCHEMA_SQL)
            conn.commit()
            cur.close()
            conn.close()
            _schema_ready = True
            logger.info("LevelIntelligenceSystem: schema ready")
        except Exception as exc:
            logger.error("Schema creation failed: %s", exc)

    def _find_or_create_level(self, price: float) -> float:
        if not _db_available():
            return round(price, 5)

        tol = self.cluster_pct / 100.0
        try:
            conn = _get_conn()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT level_price FROM price_levels
                WHERE symbol = %s AND %s BETWEEN price_min AND price_max
                ORDER BY ABS(level_price - %s) ASC
                LIMIT 1
                """,
                (self.symbol, price, price),
            )
            row = cur.fetchone()
            if row:
                cur.close()
                conn.close()
                return float(row[0])

            price_min = price * (1 - tol * 0.5)
            price_max = price * (1 + tol * 0.5)
            self._create_level(price, price_min, price_max)
            cur.close()
            conn.close()
            return round(price, 5)
        except Exception as exc:
            logger.error("_find_or_create_level error: %s", exc)
            return round(price, 5)

    def _create_level(self, price: float, price_min: float, price_max: float) -> None:
        if not _db_available():
            return
        try:
            conn = _get_conn()
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO price_levels (symbol, level_price, price_min, price_max)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (symbol, level_price) DO NOTHING
                """,
                (self.symbol, round(price, 5), round(price_min, 6), round(price_max, 6)),
            )
            conn.commit()
            cur.close()
            conn.close()
        except Exception as exc:
            logger.debug("_create_level: %s", exc)

    def record_touch(self, snapshot: TouchSnapshot) -> int:
        if not _db_available():
            return -1

        level_price = self._find_or_create_level(snapshot.price_at_touch)
        snapshot.level_price = level_price

        try:
            conn = _get_conn()
            cur = conn.cursor()
            row = snapshot.to_db_row()
            cur.execute(
                """
                INSERT INTO level_touches
                    (symbol, level_price, touched_at, price_at_touch, approach,
                     outcome, volume_at_touch, volume_ratio, rsi_14, macd_histogram,
                     atr_pct, bb_position, session, price_move_after, bars_to_outcome)
                VALUES
                    (%(symbol)s, %(level_price)s, %(touched_at)s, %(price_at_touch)s,
                     %(approach)s, %(outcome)s, %(volume_at_touch)s, %(volume_ratio)s,
                     %(rsi_14)s, %(macd_histogram)s, %(atr_pct)s, %(bb_position)s,
                     %(session)s, %(price_move_after)s, %(bars_to_outcome)s)
                RETURNING id
                """,
                row,
            )
            touch_id = int(cur.fetchone()[0])
            conn.commit()
            cur.close()
            conn.close()
            logger.debug(
                "%s: recorded touch @ %.5f approach=%s touch_id=%d",
                self.symbol,
                level_price,
                snapshot.approach,
                touch_id,
            )
            return touch_id
        except Exception as exc:
            logger.error("record_touch error: %s", exc)
            return -1

    def update_outcome(
        self,
        touch_id: int,
        outcome: str,
        price_move_after: float,
        bars_to_outcome: int,
    ) -> None:
        if not _db_available() or touch_id < 0:
            return

        try:
            conn = _get_conn()
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE level_touches
                SET outcome = %s, price_move_after = %s, bars_to_outcome = %s
                WHERE id = %s
                RETURNING symbol, level_price, approach
                """,
                (outcome, round(price_move_after, 4), bars_to_outcome, touch_id),
            )
            row = cur.fetchone()
            if not row:
                conn.close()
                return

            symbol, level_price, approach = row
            is_hold = outcome == "hold"
            is_support = approach == "from_below"
            is_resistance = approach == "from_above"

            cur.execute(
                """
                UPDATE price_levels
                SET touch_count = touch_count + 1,
                    hold_count = hold_count + %s,
                    break_count = break_count + %s,
                    support_count = support_count + %s,
                    resistance_count = resistance_count + %s,
                    last_touched = NOW(),
                    hold_rate = (hold_count + %s)::FLOAT / NULLIF(touch_count + 1, 0),
                    strength_score = %s
                WHERE symbol = %s AND level_price = %s
                """,
                (
                    int(is_hold),
                    int(not is_hold),
                    int(is_support),
                    int(is_resistance),
                    int(is_hold),
                    0.0,
                    symbol,
                    level_price,
                ),
            )

            cur.execute(
                """
                SELECT touch_count, hold_rate FROM price_levels
                WHERE symbol = %s AND level_price = %s
                """,
                (symbol, level_price),
            )
            stats = cur.fetchone()
            if stats:
                tc, hr = stats
                strength = wilson_lower_bound(float(hr or 0), int(tc or 0))
                cur.execute(
                    """
                    UPDATE price_levels SET strength_score = %s WHERE symbol = %s AND level_price = %s
                    """,
                    (round(strength, 4), symbol, level_price),
                )

            cur.execute(
                """
                UPDATE price_levels
                SET role = CASE
                    WHEN support_count::FLOAT / NULLIF(support_count + resistance_count, 0) >= 0.65
                        THEN 'SUPPORT'
                    WHEN resistance_count::FLOAT / NULLIF(support_count + resistance_count, 0) >= 0.65
                        THEN 'RESISTANCE'
                    WHEN touch_count < 3 THEN 'UNKNOWN'
                    ELSE 'MIXED'
                END
                WHERE symbol = %s AND level_price = %s
                """,
                (symbol, level_price),
            )

            conn.commit()
            cur.close()
            conn.close()

            logger.info(
                "%s: outcome=%s @ %.5f approach=%s move=%.2f%% in %d bars",
                symbol,
                outcome,
                level_price,
                approach,
                price_move_after,
                bars_to_outcome,
            )
            self._refresh_watchlist_entry(symbol, float(level_price))
        except Exception as exc:
            logger.error("update_outcome error: %s", exc)

    def _refresh_watchlist_entry(self, symbol: str, level_price: float) -> None:
        if not _db_available():
            return
        try:
            conn = _get_conn()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT touch_count, hold_rate, role
                FROM price_levels
                WHERE symbol = %s AND level_price = %s
                """,
                (symbol, level_price),
            )
            row = cur.fetchone()
            if not row:
                conn.close()
                return

            touch_count, hold_rate, role = row
            n = int(touch_count or 0)
            p = float(hold_rate or 0)
            strength = wilson_lower_bound(p, n)
            entry_side = (
                "BUY" if role == "SUPPORT" else "SELL" if role == "RESISTANCE" else "EITHER"
            )
            qualifies = (
                n >= self.WATCHLIST_MIN_TOUCHES
                and p >= self.WATCHLIST_MIN_HOLD_RATE
                and strength >= self.WATCHLIST_MIN_STRENGTH
            )

            if qualifies:
                cur.execute(
                    """
                    INSERT INTO level_watchlist
                        (symbol, level_price, hold_rate, touch_count,
                         strength_score, role, entry_side, is_active)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE)
                    ON CONFLICT (symbol, level_price) DO UPDATE SET
                        hold_rate = EXCLUDED.hold_rate,
                        touch_count = EXCLUDED.touch_count,
                        strength_score = EXCLUDED.strength_score,
                        role = EXCLUDED.role,
                        entry_side = EXCLUDED.entry_side,
                        is_active = TRUE,
                        added_at = NOW()
                    """,
                    (
                        symbol,
                        level_price,
                        round(p, 4),
                        n,
                        round(strength, 4),
                        role,
                        entry_side,
                    ),
                )
            else:
                cur.execute(
                    """
                    UPDATE level_watchlist SET is_active = FALSE
                    WHERE symbol = %s AND level_price = %s
                    """,
                    (symbol, level_price),
                )

            conn.commit()
            cur.close()
            conn.close()
        except Exception as exc:
            logger.error("_refresh_watchlist_entry: %s", exc)

    def get_watchlist(self, min_touches: int = 5) -> pd.DataFrame:
        if not _db_available():
            return pd.DataFrame()
        try:
            conn = _get_conn()
            df = pd.read_sql(
                """
                SELECT level_price, hold_rate, touch_count, strength_score,
                       role, entry_side, added_at
                FROM level_watchlist
                WHERE symbol = %s AND is_active = TRUE AND touch_count >= %s
                ORDER BY strength_score DESC
                """,
                conn,
                params=(self.symbol, min_touches),
            )
            conn.close()
            return df
        except Exception as exc:
            logger.error("get_watchlist: %s", exc)
            return pd.DataFrame()

    def get_probability(self, price: float) -> dict:
        if not _db_available():
            return {"found": False, "probability": 0.3}

        tol = self.cluster_pct / 100.0
        try:
            conn = _get_conn()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT level_price, touch_count, hold_count, break_count,
                       hold_rate, role, support_count, resistance_count
                FROM price_levels
                WHERE symbol = %s AND %s BETWEEN price_min AND price_max
                ORDER BY ABS(level_price - %s) ASC
                LIMIT 1
                """,
                (self.symbol, price, price),
            )
            row = cur.fetchone()
            cur.close()
            conn.close()

            if not row:
                return {"found": False, "probability": 0.3}

            lp, tc, hc, bc, hr, role, sup, res = row
            n = int(tc or 0)
            p = float(hr or 0)
            strength = wilson_lower_bound(p, n)
            return {
                "found": True,
                "level_price": float(lp),
                "touch_count": n,
                "hold_rate": round(p, 4),
                "probability": round(strength, 4),
                "role": role,
                "is_reliable": n >= 5,
                "is_high_conf": n >= 15,
                "support_count": int(sup or 0),
                "resistance_count": int(res or 0),
            }
        except Exception as exc:
            logger.error("get_probability: %s", exc)
            return {"found": False, "probability": 0.3}

    def get_features(self, price: float) -> dict:
        """ML feature dict — li_* prefix."""
        prob = self.get_probability(price)
        if not prob.get("found"):
            return {
                "li_found": 0.0,
                "li_probability": 0.3,
                "li_hold_rate": 0.0,
                "li_touch_count": 0.0,
                "li_is_reliable": 0.0,
                "li_nearest_dist_pct": 5.0,
                "li_role_support": 0.0,
                "li_role_resistance": 0.0,
            }

        level_price = float(prob["level_price"])
        dist_pct = abs(price - level_price) / (price + 1e-10) * 100
        role = str(prob.get("role", "UNKNOWN"))
        return {
            "li_found": 1.0,
            "li_probability": float(prob["probability"]),
            "li_hold_rate": float(prob["hold_rate"]),
            "li_touch_count": float(prob["touch_count"]),
            "li_is_reliable": 1.0 if prob.get("is_reliable") else 0.0,
            "li_nearest_dist_pct": round(dist_pct, 4),
            "li_role_support": 1.0 if role == "SUPPORT" else 0.0,
            "li_role_resistance": 1.0 if role == "RESISTANCE" else 0.0,
        }

    def get_top_levels(self, n: int = 10) -> pd.DataFrame:
        if not _db_available():
            return pd.DataFrame()
        try:
            conn = _get_conn()
            df = pd.read_sql(
                """
                SELECT
                    ROW_NUMBER() OVER (ORDER BY touch_count DESC) AS rank,
                    level_price, touch_count, hold_count, break_count,
                    ROUND(hold_rate::NUMERIC, 4) AS hold_rate,
                    role, support_count, resistance_count, last_touched
                FROM price_levels
                WHERE symbol = %s AND touch_count >= 3
                ORDER BY touch_count DESC
                LIMIT %s
                """,
                conn,
                params=(self.symbol, n),
            )
            conn.close()
            return df
        except Exception as exc:
            logger.error("get_top_levels: %s", exc)
            return pd.DataFrame()

    def process_bar(self, df: pd.DataFrame) -> None:
        """
        Resolve pending touch outcomes and detect new level touches on the latest bar.
        Called by ChartWatchRunner after each completed bar.
        """
        if df is None or df.empty or len(df) < TOUCH_LOOKBACK + OUTCOME_WINDOW:
            return

        bar_idx = len(df) - 1
        latest_close = float(df["close"].iloc[bar_idx])
        if latest_close <= 0:
            return

        self._resolve_pending(df, bar_idx)
        touch = self._detect_touch(df, bar_idx)
        if touch is None:
            return

        approach, _ = touch
        snapshot = build_snapshot(self.symbol, df, bar_idx, approach)
        touch_id = self.record_touch(snapshot)
        if touch_id < 0:
            return

        _pending_touches.setdefault(self.symbol, []).append(
            {
                "touch_id": touch_id,
                "bar_index": bar_idx,
                "level_price": snapshot.level_price,
                "approach": approach,
            }
        )

    def _resolve_pending(self, df: pd.DataFrame, current_idx: int) -> None:
        pending = _pending_touches.get(self.symbol, [])
        if not pending:
            return

        close = np.asarray(df["close"], dtype=float)
        high = np.asarray(df["high"], dtype=float)
        low = np.asarray(df["low"], dtype=float)
        rev = REVERSAL_PCT / 100.0
        remaining: list[dict[str, Any]] = []

        for item in pending:
            bars_elapsed = current_idx - int(item["bar_index"])
            if bars_elapsed < OUTCOME_WINDOW:
                remaining.append(item)
                continue

            touch_idx = int(item["bar_index"])
            start = touch_idx + 1
            end = min(touch_idx + OUTCOME_WINDOW + 1, len(df))
            if start >= end:
                remaining.append(item)
                continue

            current_price = float(close[touch_idx])
            future_high = float(np.max(high[start:end]))
            future_low = float(np.min(low[start:end]))
            up_move = (future_high - current_price) / (current_price + 1e-10)
            down_move = (current_price - future_low) / (current_price + 1e-10)
            approach = item["approach"]

            if approach == "from_above":
                outcome = "hold" if up_move >= rev else "break"
                move = up_move if outcome == "hold" else -down_move
            else:
                outcome = "hold" if down_move >= rev else "break"
                move = -down_move if outcome == "hold" else up_move

            self.update_outcome(
                int(item["touch_id"]),
                outcome,
                float(move) * 100,
                bars_elapsed,
            )

        _pending_touches[self.symbol] = remaining

    def _detect_touch(self, df: pd.DataFrame, bar_idx: int) -> Optional[tuple[str, float]]:
        """Return (approach, level_price) if the bar touches a watched level."""
        close = float(df["close"].iloc[bar_idx])
        high = float(df["high"].iloc[bar_idx])
        low = float(df["low"].iloc[bar_idx])
        tol = self.cluster_pct / 100.0

        candidates: list[float] = []
        watchlist = self.get_watchlist(min_touches=3)
        if not watchlist.empty:
            candidates.extend(float(p) for p in watchlist["level_price"].tolist())

        prob = self.get_probability(close)
        if prob.get("found"):
            candidates.append(float(prob["level_price"]))

        if not candidates:
            candidates.append(round(close, 5))

        prev_close = float(df["close"].iloc[bar_idx - TOUCH_LOOKBACK])
        for level_price in candidates:
            zone_min = level_price * (1 - tol)
            zone_max = level_price * (1 + tol)
            touched = low <= zone_max and high >= zone_min
            if not touched:
                continue

            came_from_above = prev_close > level_price * (1 + tol * 0.5)
            came_from_below = prev_close < level_price * (1 - tol * 0.5)
            if came_from_above:
                return "from_above", level_price
            if came_from_below:
                return "from_below", level_price

        return None


def build_snapshot(
    symbol: str,
    df: pd.DataFrame,
    bar_index: int,
    approach: str,
) -> TouchSnapshot:
    close = np.asarray(df["close"], dtype=float)
    volume = np.asarray(df["volume"], dtype=float)
    bar_time = pd.to_datetime(cast(Any, df.index[bar_index]), utc=True)
    touched_at = bar_time.to_pydatetime()

    vol_roll = cast(pd.Series, pd.Series(volume).rolling(20).mean())
    vol_ma = float(vol_roll.iloc[bar_index])
    vol_ratio = float(volume[bar_index] / (vol_ma + 1e-10))

    close_s = cast(pd.Series, df["close"])
    delta = close_s.diff()
    gain = cast(pd.Series, delta.clip(lower=0).rolling(14).mean())
    loss = cast(pd.Series, (-delta.clip(upper=0)).rolling(14).mean())
    rs = float(gain.iloc[bar_index] / (loss.iloc[bar_index] + 1e-10))
    rsi14 = float(100 - 100 / (1 + rs))

    ef = close_s.ewm(span=12).mean()
    es = close_s.ewm(span=26).mean()
    macd_h = float((ef - es).ewm(span=9).mean().iloc[bar_index])

    atr = cast(
        pd.Series,
        pd.concat(
            [
                df["high"] - df["low"],
                (df["high"] - close_s.shift()).abs(),
                (df["low"] - close_s.shift()).abs(),
            ],
            axis=1,
        )
        .max(axis=1)
        .rolling(14)
        .mean(),
    )
    atr_pct = float(atr.iloc[bar_index] / (close[bar_index] + 1e-10))

    sma = cast(pd.Series, close_s.rolling(20).mean())
    sd = cast(pd.Series, close_s.rolling(20).std())
    upper = cast(pd.Series, sma + 2 * sd)
    lower = cast(pd.Series, sma - 2 * sd)
    bb_pos = float(
        (close[bar_index] - lower.iloc[bar_index])
        / (upper.iloc[bar_index] - lower.iloc[bar_index] + 1e-10)
    )

    hour = int(bar_time.hour)
    session = (
        "OVERLAP"
        if 13 <= hour < 16
        else "NEW_YORK"
        if 13 <= hour < 21
        else "LONDON"
        if 7 <= hour < 16
        else "ASIA"
    )

    return TouchSnapshot(
        symbol=symbol,
        level_price=round(float(close[bar_index]), 5),
        touched_at=touched_at,
        price_at_touch=round(float(close[bar_index]), 6),
        approach=approach,
        outcome="pending",
        volume_at_touch=round(float(volume[bar_index]), 2),
        volume_ratio=round(vol_ratio, 3),
        rsi_14=round(rsi14, 2),
        macd_histogram=round(macd_h, 6),
        atr_pct=round(atr_pct, 4),
        bb_position=round(bb_pos, 4),
        session=session,
    )


_registry: dict[str, LevelIntelligenceSystem] = {}


def get_system(symbol: str, asset_class: str = "equity") -> LevelIntelligenceSystem:
    sym = symbol.upper()
    if sym not in _registry:
        from config.symbols import get_symbol_or_none

        spec = get_symbol_or_none(sym)
        ac = spec.asset_class if spec else asset_class
        system = LevelIntelligenceSystem(sym, ac)
        system.ensure_schema()
        _registry[sym] = system
    return _registry[sym]
