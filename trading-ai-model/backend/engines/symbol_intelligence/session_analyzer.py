"""engines/symbol_intelligence/session_analyzer.py"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


SESSIONS = {
    "ASIA": (0, 8),
    "LONDON": (7, 16),
    "NEW_YORK": (13, 21),
    "OVERLAP": (13, 16),
}

SESSION_SYMBOLS = {
    "ASIA": ["USDJPY", "AUDUSD", "BTCUSD", "ETHUSD"],
    "LONDON": ["EURUSD", "GBPUSD", "USDCHF", "GC", "CL"],
    "NEW_YORK": [
        "ES",
        "MES",
        "NQ",
        "MNQ",
        "RTY",
        "TSLA",
        "NVDA",
        "AAPL",
        "MSFT",
        "AMZN",
    ],
    "OVERLAP": ["EURUSD", "GBPUSD", "ES", "MES"],
}

CRYPTO_24_7 = {"BTCUSD", "ETHUSD", "SOLUSD", "BNBUSD", "XRPUSD"}


class SessionAnalyzer:
    def get_current_session(self, dt: Optional[datetime] = None) -> str:
        if dt is None:
            dt = datetime.now(tz=timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        hour = dt.hour
        if 13 <= hour < 16:
            return "OVERLAP"
        if 13 <= hour < 21:
            return "NEW_YORK"
        if 7 <= hour < 16:
            return "LONDON"
        return "ASIA"

    def active_session(self, timestamp=None) -> str:
        """Legacy alias for get_current_session."""
        if timestamp is None:
            return self.get_current_session()
        if isinstance(timestamp, datetime):
            return self.get_current_session(timestamp)
        return self.get_current_session()

    def is_symbol_active(self, symbol: str, dt: Optional[datetime] = None) -> bool:
        sym = symbol.upper()
        if sym in CRYPTO_24_7:
            return True
        session = self.get_current_session(dt)
        active = SESSION_SYMBOLS.get(session, [])
        return sym in active or session == "OVERLAP"

    def session_quality(self, symbol: str, dt: Optional[datetime] = None) -> float:
        """Returns 0-1 quality score for trading this symbol now."""
        sym = symbol.upper()
        session = self.get_current_session(dt)
        if session == "OVERLAP":
            return 1.0
        active = SESSION_SYMBOLS.get(session, [])
        if sym in active:
            return 0.8
        if sym in ("BTCUSD", "ETHUSD", "SOLUSD"):
            return 0.7
        return 0.3
