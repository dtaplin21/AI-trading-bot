"""
config/watchlist.py

Single source of truth for the watched chart list.

Both the worker (ChartWatchRunner) and the API/UI (build_watched_charts)
read from here. CHART_WATCHLIST in settings is optional legacy override only.

Priority order for symbol source:
  1. CHART_WATCHLIST env var (explicit override)
  2. WATCHER_SYMBOLS env var (primary — set on both worker + web service)
  3. DEFAULT_SYMBOLS from config/symbols.py (fallback)

Priority order for timeframe source:
  1. WATCHER_TIMEFRAMES env var
  2. DEFAULT_TIMEFRAMES constant below

UI layout (WATCHLIST_UI_MODE):
  symbol — one row per symbol, primary timeframe (default, 23 rows)
  grid   — symbol × timeframe matrix (up to 92 rows)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from config.symbols import (
    CRYPTO_SYMBOLS,
    EQUITY_SYMBOLS,
    FOREX_SYMBOLS,
    FUTURES_SYMBOLS,
    SYMBOL_MAP,
    SymbolSpec,
    massive_symbol,
    normalize_symbol,
)

DEFAULT_SYMBOLS: list[str] = list(
    FUTURES_SYMBOLS + FOREX_SYMBOLS + CRYPTO_SYMBOLS + EQUITY_SYMBOLS
)

DEFAULT_TIMEFRAMES: list[str] = ["1m", "5m", "15m", "1h"]


def _primary_tf() -> str:
    return os.getenv("WATCHLIST_PRIMARY_TF", "5m")


def _ui_mode() -> str:
    return os.getenv("WATCHLIST_UI_MODE", "symbol").strip().lower()

ASSET_CLASS_ORDER = ["futures", "forex", "crypto", "equity"]
ASSET_CLASS_LABELS = {
    "futures": "Futures",
    "forex": "Forex",
    "crypto": "Crypto",
    "equity": "Equities",
}


@dataclass
class WatchedChart:
    """One row in GET /dashboard watched_charts."""

    symbol: str
    timeframe: str
    display_name: str
    asset_class: str
    session_type: str
    tick_value: float
    massive_api_symbol: str
    is_active: bool = True
    last_price: Optional[float] = None
    last_bar_at: Optional[str] = None
    bar_count: int = 0
    session_open: bool = False
    session_label: str = ""

    @property
    def label(self) -> str:
        return self.display_name

    def to_dict(self) -> dict:
        status = "live" if self.last_bar_at else ("watching" if self.session_open else "closed")
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "display_name": self.display_name,
            "label": self.display_name,
            "asset_class": self.asset_class,
            "session_type": self.session_type,
            "tick_value": self.tick_value,
            "massive_api_symbol": self.massive_api_symbol,
            "is_active": self.is_active,
            "last_price": self.last_price,
            "last_bar_at": self.last_bar_at,
            "bar_count": self.bar_count,
            "session_open": self.session_open,
            "session_label": self.session_label,
            "status": status,
            "pipeline_active": self.session_open,
        }


def watcher_symbols_from_env() -> list[str]:
    raw = os.getenv("WATCHER_SYMBOLS", "")
    if not raw.strip():
        return list(DEFAULT_SYMBOLS)
    return [normalize_symbol(s) for s in raw.split(",") if s.strip()]


def watcher_timeframes_from_env() -> list[str]:
    raw = os.getenv("WATCHER_TIMEFRAMES", "")
    if not raw.strip():
        return list(DEFAULT_TIMEFRAMES)
    return [t.strip() for t in raw.split(",") if t.strip()]


def parse_legacy_chart_watchlist(raw: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" in entry:
            sym, tf = entry.split(":", 1)
            pairs.append((normalize_symbol(sym), tf.strip()))
        else:
            pairs.append((normalize_symbol(entry), _primary_tf()))
    return pairs


def _symbol_timeframe_pairs() -> list[tuple[str, str]]:
    legacy = os.getenv("CHART_WATCHLIST", "").strip()
    if legacy:
        return parse_legacy_chart_watchlist(legacy)

    symbols = watcher_symbols_from_env()
    timeframes = watcher_timeframes_from_env()

    if _ui_mode() == "grid":
        return [(sym, tf) for sym in symbols for tf in timeframes]

    primary_tf = _primary_tf()
    primary = primary_tf if primary_tf in timeframes else timeframes[0]
    return [(sym, primary) for sym in symbols]


def watcher_charts_for_dashboard(
    include_session_status: bool = True,
) -> list[WatchedChart]:
    charts: list[WatchedChart] = []
    for symbol, timeframe in _symbol_timeframe_pairs():
        spec = SYMBOL_MAP.get(symbol)
        if spec:
            chart = _build_chart_entry(spec, timeframe)
        else:
            chart = WatchedChart(
                symbol=symbol,
                timeframe=timeframe,
                display_name=symbol,
                asset_class="unknown",
                session_type="unknown",
                tick_value=1.0,
                massive_api_symbol=symbol,
            )
        if include_session_status:
            _attach_session_status(chart)
        charts.append(chart)

    order = {cls: i for i, cls in enumerate(ASSET_CLASS_ORDER)}
    charts.sort(key=lambda c: (order.get(c.asset_class, 99), c.symbol))
    return charts


def charts_grouped_by_asset_class(
    charts: list[WatchedChart] | None = None,
) -> dict[str, list[dict]]:
    if charts is None:
        charts = watcher_charts_for_dashboard()
    groups: dict[str, list[dict]] = {cls: [] for cls in ASSET_CLASS_ORDER}
    for chart in charts:
        groups.setdefault(chart.asset_class, []).append(chart.to_dict())
    return {
        ASSET_CLASS_LABELS.get(k, k.title()): v
        for k, v in groups.items()
        if v
    }


def get_symbol_count() -> dict[str, int]:
    symbols = watcher_symbols_from_env()
    counts: dict[str, int] = {cls: 0 for cls in ASSET_CLASS_ORDER}
    unknown = 0
    for sym in symbols:
        spec = SYMBOL_MAP.get(sym)
        cls = spec.asset_class if spec else "unknown"
        if cls in counts:
            counts[cls] += 1
        else:
            unknown += 1
    if unknown:
        counts["unknown"] = unknown
    return {k: v for k, v in counts.items() if v > 0}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_chart_entry(spec: SymbolSpec, timeframe: str) -> WatchedChart:
    return WatchedChart(
        symbol=spec.symbol,
        timeframe=timeframe,
        display_name=spec.name,
        asset_class=spec.asset_class,
        session_type=spec.session,
        tick_value=spec.tick_value,
        massive_api_symbol=massive_symbol(spec.symbol),
    )


def _attach_session_status(chart: WatchedChart) -> None:
    try:
        from chart_watcher.session_scheduler import SessionScheduler

        sched = SessionScheduler()
        chart.session_open = sched.is_trading(chart.symbol)
        chart.session_label = sched.session_label(chart.symbol)
    except Exception:
        chart.session_open = True
        chart.session_label = ""
