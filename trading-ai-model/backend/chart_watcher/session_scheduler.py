"""Session schedule for CME futures, forex, equity cash, and crypto."""

from __future__ import annotations

import os
from datetime import datetime, time, timedelta, timezone
from enum import Enum
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = timezone.utc

# CME equity index / metals / energy — Sun 6:00 PM ET open, Fri 5:00 PM ET close
# Daily maintenance ~5:00–6:00 PM ET
CME_SYMBOLS = frozenset(
    {"MES", "ES", "MNQ", "NQ", "MYM", "YM", "RTY", "M2K", "CL", "GC", "SI", "ZB", "ZN", "ZF", "ZT"}
)
CME_ALL = CME_SYMBOLS

FOREX_SYMBOLS = frozenset(
    {
        "EURUSD",
        "GBPUSD",
        "USDJPY",
        "AUDUSD",
        "USDCAD",
        "USDCHF",
        "NZDUSD",
        "EURGBP",
        "EURJPY",
        "EUR/USD",
        "GBP/USD",
        "USD/JPY",
    }
)

# Forex: Sunday 5pm ET → Friday 5pm ET (nearly 24/5)
FOREX_OPEN = time(17, 0)
FOREX_CLOSE = time(17, 0)

EQUITY_CASH = frozenset({"SPY", "SPX", "QQQ", "IWM", "DIA"})
EQUITY_OPEN = time(9, 30)
EQUITY_CLOSE = time(16, 0)

CRYPTO_SYMBOLS = frozenset(
    {
        "BTC",
        "ETH",
        "SOL",
        "XRP",
        "BTC-USD",
        "ETH-USD",
        "SOL-USD",
        "BTCUSDT",
        "ETHUSDT",
        "SOLUSDT",
        "XBTUSD",
        "ETHUSD",
    }
)


class WatcherMode(str, Enum):
    LIVE = "live"
    REPLAY = "replay"
    PAPER = "paper"


def _normalize_symbol(symbol: str) -> str:
    return symbol.upper().replace("/", "").replace("-", "")


_CRYPTO_NORM = frozenset(_normalize_symbol(s) for s in CRYPTO_SYMBOLS)
_FOREX_NORM = frozenset(_normalize_symbol(s) for s in FOREX_SYMBOLS)
_EQUITY_NORM = frozenset(_normalize_symbol(s) for s in EQUITY_CASH)
_CME_NORM = frozenset(_normalize_symbol(s) for s in CME_ALL)


class SessionScheduler:
    """Returns whether a symbol's market is open for trading."""

    def __init__(self, mode: WatcherMode | None = None) -> None:
        if mode is None:
            mode = WatcherMode(os.getenv("WATCHER_MODE", "paper").lower())
        self.mode = mode

    def is_trading(self, symbol: str, at: datetime | None = None) -> bool:
        if self.mode == WatcherMode.REPLAY:
            return True

        now_et = (at or datetime.now(tz=UTC)).astimezone(ET)
        symbol_upper = _normalize_symbol(symbol)

        if symbol_upper in _CRYPTO_NORM:
            return True

        if symbol_upper in _FOREX_NORM:
            return self._is_forex_session(now_et)

        if symbol_upper in _EQUITY_NORM:
            return self._is_equity_session(now_et)

        if symbol_upper in _CME_NORM:
            return self._is_cme_session(now_et)

        return True

    def seconds_until_open(self, symbol: str) -> float:
        if self.mode == WatcherMode.REPLAY:
            return 0.0

        sym = _normalize_symbol(symbol)
        if sym in _CRYPTO_NORM:
            return 0.0

        now_et = datetime.now(tz=ET)
        if self.is_trading(symbol, at=now_et):
            return 0.0

        probe = now_et
        for _ in range(7 * 24 * 12):
            probe += timedelta(minutes=5)
            if self.is_trading(symbol, at=probe):
                return max(0.0, (probe - now_et).total_seconds())
        return 3600.0

    def next_session_label(self, symbol: str) -> str:
        sym = _normalize_symbol(symbol)
        if sym in _CRYPTO_NORM:
            return "24/7"
        if self.mode == WatcherMode.REPLAY:
            return "replay (always on)"
        wait = self.seconds_until_open(symbol)
        if wait <= 0:
            return "open now"
        hours = int(wait // 3600)
        return f"opens in ~{hours}h" if hours else "opens soon"

    def _is_forex_session(self, now_et: datetime) -> bool:
        """Forex: Sun 5pm ET → Fri 5pm ET. Nearly 24/5."""
        weekday = now_et.weekday()
        t = now_et.time()
        if weekday == 5:
            return False
        if weekday == 6 and t < FOREX_OPEN:
            return False
        if weekday == 4 and t >= FOREX_CLOSE:
            return False
        return True

    def _is_equity_session(self, now_et: datetime) -> bool:
        """NYSE/Nasdaq cash: Mon–Fri 9:30am–4pm ET only."""
        weekday = now_et.weekday()
        if weekday >= 5:
            return False
        t = now_et.time()
        return EQUITY_OPEN <= t < EQUITY_CLOSE

    def _is_cme_session(self, now_et: datetime) -> bool:
        """CME-style week: Sun 18:00 ET → Fri 17:00 ET; daily halt 17:00–18:00 ET."""
        weekday = now_et.weekday()
        t = now_et.time()

        if time(17, 0) <= t < time(18, 0):
            return False

        if weekday == 6:
            return t >= time(18, 0)
        if weekday == 4:
            return t < time(17, 0)
        if weekday == 5:
            return False
        return True
