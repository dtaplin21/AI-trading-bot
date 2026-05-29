"""
chart_watcher/session_scheduler.py

Knows exactly when each futures symbol trades.
Controls whether the bar loop runs, pauses, or skips.

CME Globex hours (US Eastern):
  Equity index futures (MES, ES, NQ, MNQ, YM, RTY):
    Sunday 6:00 PM → Friday 5:00 PM
    Daily maintenance break: 5:00 PM → 6:00 PM ET

  Energy (CL, NG), Metals (GC, SI), Treasuries (ZB, ZN): same Globex schedule.

Crypto: 24/7, no breaks.
Replay: ignores real time entirely — walks date range.

Env:
  WATCHER_MODE     live | replay | paper
  WATCHER_TZ       America/New_York (default)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, time, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo(os.getenv("WATCHER_TZ", "America/New_York"))
UTC = ZoneInfo("UTC")


class WatcherMode(str, Enum):
    LIVE = "live"
    REPLAY = "replay"
    PAPER = "paper"


# Symbols that trade CME Globex hours
CME_EQUITY_INDEX = {"MES", "ES", "MNQ", "NQ", "MYM", "YM", "RTY", "M2K"}
CME_ENERGY = {"CL", "QM", "NG", "RB", "HO"}
CME_METALS = {"GC", "MGC", "SI", "HG", "PL"}
CME_TREASURIES = {"ZB", "ZN", "ZF", "ZT", "ZC", "ZS", "ZW"}
CME_FX = {"6E", "6J", "6B", "6C", "6A", "6N", "6S"}
CME_ALL = CME_EQUITY_INDEX | CME_ENERGY | CME_METALS | CME_TREASURIES | CME_FX

CRYPTO_SYMBOLS = {"BTC", "ETH", "SOL", "BNB", "XRP", "BTCUSD", "ETHUSD"}

# Daily maintenance break window (ET)
MAINTENANCE_START = time(17, 0)
MAINTENANCE_END = time(18, 0)

# Weekend break: Friday 5 PM → Sunday 6 PM ET
FRIDAY_CLOSE = time(17, 0)
SUNDAY_OPEN = time(18, 0)


def _normalize_symbol(symbol: str) -> str:
    return symbol.upper().replace("/", "").replace("-", "")


_CRYPTO_NORM = frozenset(_normalize_symbol(s) for s in CRYPTO_SYMBOLS)
_CME_NORM = frozenset(_normalize_symbol(s) for s in CME_ALL)


class SessionScheduler:
    """
    Determines whether a symbol is currently in its trading session.
    Called by ChartWatchRunner before processing each bar.
    """

    def __init__(self, mode: WatcherMode | None = None) -> None:
        if mode is None:
            mode = WatcherMode(os.getenv("WATCHER_MODE", "paper").lower())
        self.mode = mode

    def is_trading(self, symbol: str, at: datetime | None = None) -> bool:
        """Is this symbol currently in its trading session?"""
        if self.mode == WatcherMode.REPLAY:
            return True

        now_et = (at or datetime.now(tz=UTC)).astimezone(ET)
        symbol_upper = _normalize_symbol(symbol)

        if symbol_upper in _CRYPTO_NORM:
            return True

        if symbol_upper in _CME_NORM:
            return self._is_cme_session(now_et)

        logger.debug(
            "SessionScheduler: unknown symbol %s — assuming trading",
            symbol,
        )
        return True

    def _is_cme_session(self, now_et: datetime) -> bool:
        """CME Globex session check in ET timezone."""
        weekday = now_et.weekday()
        t = now_et.time()

        if weekday == 5:
            return False

        if weekday == 6:
            return t >= SUNDAY_OPEN

        if weekday == 4:
            return t < FRIDAY_CLOSE

        if MAINTENANCE_START <= t < MAINTENANCE_END:
            return False

        return True

    def seconds_until_open(self, symbol: str) -> float:
        """Seconds until next session open (for sleep-until-open in live mode)."""
        if self.mode == WatcherMode.REPLAY:
            return 0.0

        if _normalize_symbol(symbol) in _CRYPTO_NORM:
            return 0.0

        if self.is_trading(symbol):
            return 0.0

        now_et = datetime.now(tz=UTC).astimezone(ET)
        weekday = now_et.weekday()
        t = now_et.time()

        if MAINTENANCE_START <= t < MAINTENANCE_END:
            next_open = now_et.replace(
                hour=MAINTENANCE_END.hour,
                minute=MAINTENANCE_END.minute,
                second=0,
                microsecond=0,
            )
            return (next_open - now_et).total_seconds()

        if weekday == 5 or (weekday == 6 and t < SUNDAY_OPEN):
            days_until_sunday = (6 - weekday) % 7 or 7
            next_open = now_et.replace(
                hour=SUNDAY_OPEN.hour,
                minute=SUNDAY_OPEN.minute,
                second=0,
                microsecond=0,
            ) + timedelta(days=days_until_sunday if weekday == 5 else 0)
            return max(0.0, (next_open - now_et).total_seconds())

        if weekday == 4 and t >= FRIDAY_CLOSE:
            days_ahead = 2
            next_open = now_et.replace(
                hour=SUNDAY_OPEN.hour,
                minute=SUNDAY_OPEN.minute,
                second=0,
                microsecond=0,
            ) + timedelta(days=days_ahead)
            return max(0.0, (next_open - now_et).total_seconds())

        return 0.0

    def next_session_label(self, symbol: str) -> str:
        """Human-readable label for next session open."""
        if self.mode == WatcherMode.REPLAY:
            return "REPLAY"
        if _normalize_symbol(symbol) in _CRYPTO_NORM:
            return "24/7"
        secs = self.seconds_until_open(symbol)
        if secs <= 0:
            return "NOW"
        hours = int(secs // 3600)
        mins = int((secs % 3600) // 60)
        return f"in {hours}h {mins}m"

    @property
    def watcher_mode(self) -> WatcherMode:
        return self.mode
