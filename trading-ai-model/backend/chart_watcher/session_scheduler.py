"""
chart_watcher/session_scheduler.py

Session gating per symbol — routes from config.symbols SESSION_TYPES / SymbolSpec.session.

  cme_globex    — CME Globex (futures)
  forex_24_5    — FX Sun 5pm → Fri 5pm ET
  crypto_24_7   — always on
  equity_us     — US extended 4am–8pm ET Mon–Fri

Replay mode ignores wall-clock sessions.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, time, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

from config.symbols import get_symbol_or_none, session_kind

logger = logging.getLogger(__name__)

ET = ZoneInfo(os.getenv("WATCHER_TZ", "America/New_York"))
UTC = ZoneInfo("UTC")


class WatcherMode(str, Enum):
    LIVE = "live"
    REPLAY = "replay"
    PAPER = "paper"


# CME Globex (ET)
MAINTENANCE_START = time(17, 0)
MAINTENANCE_END = time(18, 0)
FRIDAY_CLOSE = time(17, 0)
SUNDAY_OPEN_CME = time(18, 0)

# Forex (ET) — Sun 5pm → Fri 5pm
FOREX_SUNDAY_OPEN = time(17, 0)
FOREX_FRIDAY_CLOSE = time(17, 0)

# US equities extended (ET)
EQUITY_EXTENDED_START = time(4, 0)
EQUITY_EXTENDED_END = time(20, 0)


class SessionScheduler:
    """Determines whether a symbol is in its trading session."""

    def __init__(self, mode: WatcherMode | None = None) -> None:
        if mode is None:
            mode = WatcherMode(os.getenv("WATCHER_MODE", "paper").lower())
        self.mode = mode

    def is_trading(self, symbol: str, at: datetime | None = None) -> bool:
        if self.mode == WatcherMode.REPLAY:
            return True

        now_et = (at or datetime.now(tz=UTC)).astimezone(ET)
        kind = session_kind(symbol)

        if kind is None:
            logger.debug(
                "SessionScheduler: unknown symbol %s — assuming trading",
                symbol,
            )
            return True

        if kind == "crypto_24_7":
            return True
        if kind == "cme_globex":
            return self._is_cme_session(now_et)
        if kind == "forex_24_5":
            return self._is_forex_session(now_et)
        if kind == "equity_us":
            return self._is_equity_session(now_et)
        return True

    def _is_cme_session(self, now_et: datetime) -> bool:
        weekday = now_et.weekday()
        t = now_et.time()

        if weekday == 5:
            return False
        if weekday == 6:
            return t >= SUNDAY_OPEN_CME
        if weekday == 4:
            return t < FRIDAY_CLOSE
        if MAINTENANCE_START <= t < MAINTENANCE_END:
            return False
        return True

    def _is_forex_session(self, now_et: datetime) -> bool:
        weekday = now_et.weekday()
        t = now_et.time()

        if weekday == 5:
            return False
        if weekday == 6:
            return t >= FOREX_SUNDAY_OPEN
        if weekday == 4:
            return t < FOREX_FRIDAY_CLOSE
        return True

    def _is_equity_session(self, now_et: datetime) -> bool:
        weekday = now_et.weekday()
        if weekday >= 5:
            return False
        t = now_et.time()
        return EQUITY_EXTENDED_START <= t < EQUITY_EXTENDED_END

    def seconds_until_open(self, symbol: str) -> float:
        if self.mode == WatcherMode.REPLAY:
            return 0.0
        if self.is_trading(symbol):
            return 0.0

        kind = session_kind(symbol)
        now_et = datetime.now(tz=UTC).astimezone(ET)

        if kind == "crypto_24_7":
            return 0.0
        if kind == "cme_globex":
            return self._seconds_until_cme_open(now_et)
        if kind == "forex_24_5":
            return self._seconds_until_forex_open(now_et)
        if kind == "equity_us":
            return self._seconds_until_equity_open(now_et, symbol)
        return 0.0

    def _seconds_until_cme_open(self, now_et: datetime) -> float:
        weekday = now_et.weekday()
        t = now_et.time()

        if MAINTENANCE_START <= t < MAINTENANCE_END:
            next_open = now_et.replace(
                hour=MAINTENANCE_END.hour,
                minute=MAINTENANCE_END.minute,
                second=0,
                microsecond=0,
            )
            return max(0.0, (next_open - now_et).total_seconds())

        if weekday == 5 or (weekday == 6 and t < SUNDAY_OPEN_CME):
            next_open = self._next_weekday_time(now_et, 6, SUNDAY_OPEN_CME)
            if weekday == 6 and t < SUNDAY_OPEN_CME:
                next_open = now_et.replace(
                    hour=SUNDAY_OPEN_CME.hour,
                    minute=SUNDAY_OPEN_CME.minute,
                    second=0,
                    microsecond=0,
                )
            return max(0.0, (next_open - now_et).total_seconds())

        if weekday == 4 and t >= FRIDAY_CLOSE:
            next_open = self._next_weekday_time(now_et, 6, SUNDAY_OPEN_CME, days_ahead=2)
            return max(0.0, (next_open - now_et).total_seconds())

        return 0.0

    def _seconds_until_forex_open(self, now_et: datetime) -> float:
        weekday = now_et.weekday()
        t = now_et.time()

        if weekday == 5 or (weekday == 6 and t < FOREX_SUNDAY_OPEN):
            if weekday == 6 and t < FOREX_SUNDAY_OPEN:
                next_open = now_et.replace(
                    hour=FOREX_SUNDAY_OPEN.hour,
                    minute=FOREX_SUNDAY_OPEN.minute,
                    second=0,
                    microsecond=0,
                )
            else:
                next_open = self._next_weekday_time(now_et, 6, FOREX_SUNDAY_OPEN)
            return max(0.0, (next_open - now_et).total_seconds())

        if weekday == 4 and t >= FOREX_FRIDAY_CLOSE:
            next_open = self._next_weekday_time(now_et, 6, FOREX_SUNDAY_OPEN, days_ahead=2)
            return max(0.0, (next_open - now_et).total_seconds())

        return 0.0

    def _seconds_until_equity_open(self, now_et: datetime, symbol: str) -> float:
        weekday = now_et.weekday()
        t = now_et.time()

        if weekday >= 5:
            days_ahead = (7 - weekday) % 7 or 1
            next_open = self._next_weekday_time(
                now_et, 0, EQUITY_EXTENDED_START, days_ahead=days_ahead
            )
            return max(0.0, (next_open - now_et).total_seconds())

        if t < EQUITY_EXTENDED_START:
            next_open = now_et.replace(
                hour=EQUITY_EXTENDED_START.hour,
                minute=EQUITY_EXTENDED_START.minute,
                second=0,
                microsecond=0,
            )
            return max(0.0, (next_open - now_et).total_seconds())

        if t >= EQUITY_EXTENDED_END:
            next_open = (now_et + timedelta(days=1)).replace(
                hour=EQUITY_EXTENDED_START.hour,
                minute=EQUITY_EXTENDED_START.minute,
                second=0,
                microsecond=0,
            )
            if next_open.weekday() >= 5:
                next_open = self._next_weekday_time(next_open, 0, EQUITY_EXTENDED_START)
            return max(0.0, (next_open - now_et).total_seconds())

        return 0.0

    @staticmethod
    def _next_weekday_time(
        now_et: datetime,
        target_weekday: int,
        target_time: time,
        *,
        days_ahead: int | None = None,
    ) -> datetime:
        if days_ahead is not None:
            delta_days = days_ahead
        else:
            delta_days = (target_weekday - now_et.weekday()) % 7
            if delta_days == 0:
                delta_days = 7
        return (now_et + timedelta(days=delta_days)).replace(
            hour=target_time.hour,
            minute=target_time.minute,
            second=0,
            microsecond=0,
        )

    def next_session_label(self, symbol: str) -> str:
        if self.mode == WatcherMode.REPLAY:
            return "REPLAY"
        kind = session_kind(symbol)
        if kind == "crypto_24_7":
            return "24/7"
        secs = self.seconds_until_open(symbol)
        if secs <= 0:
            return "NOW"
        hours = int(secs // 3600)
        mins = int((secs % 3600) // 60)
        spec = get_symbol_or_none(symbol)
        label = spec.session if spec else "unknown"
        return f"{label} in {hours}h {mins}m"

    @property
    def watcher_mode(self) -> WatcherMode:
        return self.mode
