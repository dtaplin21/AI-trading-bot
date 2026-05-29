"""CME futures and crypto session schedule for the chart watcher."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from enum import Enum
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = timezone.utc

# CME equity index / metals / energy — Sun 6:00 PM ET open, Fri 5:00 PM ET close
# Daily maintenance ~5:00–6:00 PM ET (no new bars)
CME_SYMBOLS = frozenset(
    {"MES", "ES", "MNQ", "NQ", "MYM", "YM", "RTY", "M2K", "CL", "GC", "SI", "ZB", "ZN", "ZF", "ZT"}
)
CRYPTO_SYMBOLS = frozenset({"BTC", "ETH", "SOL", "XRP"})


class WatcherMode(str, Enum):
    LIVE = "live"
    REPLAY = "replay"
    PAPER = "paper"


class SessionScheduler:
    """Returns whether a symbol's market is open for trading."""

    def is_trading(self, symbol: str) -> bool:
        sym = symbol.upper()
        if sym in CRYPTO_SYMBOLS:
            return True
        if sym in CME_SYMBOLS or sym:
            return self._cme_open(datetime.now(tz=ET))
        return self._cme_open(datetime.now(tz=ET))

    def seconds_until_open(self, symbol: str) -> float:
        sym = symbol.upper()
        if sym in CRYPTO_SYMBOLS:
            return 0.0
        now_et = datetime.now(tz=ET)
        if self._cme_open(now_et):
            return 0.0
        # Walk forward in 5-minute steps (max 7 days)
        probe = now_et
        for _ in range(7 * 24 * 12):
            probe += timedelta(minutes=5)
            if self._cme_open(probe):
                return max(0.0, (probe - now_et).total_seconds())
        return 3600.0

    def next_session_label(self, symbol: str) -> str:
        sym = symbol.upper()
        if sym in CRYPTO_SYMBOLS:
            return "24/7"
        wait = self.seconds_until_open(sym)
        if wait <= 0:
            return "open now"
        hours = int(wait // 3600)
        return f"opens in ~{hours}h" if hours else "opens soon"

    def _cme_open(self, dt_et: datetime) -> bool:
        """CME-style week: Sun 18:00 ET → Fri 17:00 ET; daily halt 17:00–18:00 ET."""
        wd = dt_et.weekday()  # Mon=0 … Sun=6
        t = dt_et.time()

        # Daily maintenance window
        if time(17, 0) <= t < time(18, 0):
            return False

        if wd == 6:  # Sunday — opens 18:00 ET
            return t >= time(18, 0)
        if wd == 4:  # Friday — closes 17:00 ET
            return t < time(17, 0)
        if wd == 5:  # Saturday
            return False
        return True
