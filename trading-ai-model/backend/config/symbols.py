"""
config/symbols.py — single source of truth for watched instruments.

Used by: session scheduler, Polygon/Massive ticker resolution, tick PnL,
paper position book, trade planning, and WATCHER_SYMBOLS defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

AssetClass = Literal["futures", "forex", "crypto", "equity"]
SessionKind = Literal["cme_globex", "forex_24_5", "crypto_24_7", "equity_us"]


@dataclass(frozen=True)
class SymbolSpec:
    symbol: str
    name: str
    asset_class: AssetClass
    session: SessionKind
    tick_size: float
    tick_value: float
    point_value: float
    atr_lookback: int = 14


# Session routing — session_scheduler reads SESSION_TYPES via spec.session
SESSION_TYPES: dict[SessionKind, str] = {
    "cme_globex": "CME Globex Sun 6pm–Fri 5pm ET; daily 5–6pm maintenance",
    "forex_24_5": "FX Sun 5pm–Fri 5pm ET",
    "crypto_24_7": "Crypto 24/7",
    "equity_us": "US equities extended 4am–8pm ET Mon–Fri",
}

DEFAULT_WATCHER_SYMBOLS: tuple[str, ...] = (
    # Futures (8)
    "MES",
    "ES",
    "MNQ",
    "NQ",
    "CL",
    "GC",
    "ZB",
    "RTY",
    # Forex (5)
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "USDCHF",
    "AUDUSD",
    # Crypto (5)
    "BTCUSD",
    "ETHUSD",
    "SOLUSD",
    "BNBUSD",
    "XRPUSD",
    # Equities (5)
    "TSLA",
    "NVDA",
    "AAPL",
    "MSFT",
    "AMZN",
)

SYMBOLS: dict[str, SymbolSpec] = {
    # --- Futures ---
    "MES": SymbolSpec(
        "MES", "Micro S&P 500", "futures", "cme_globex", 0.25, 1.25, 5.0
    ),
    "ES": SymbolSpec(
        "ES", "E-mini S&P 500", "futures", "cme_globex", 0.25, 12.50, 50.0
    ),
    "MNQ": SymbolSpec(
        "MNQ", "Micro Nasdaq", "futures", "cme_globex", 0.25, 0.50, 2.0
    ),
    "NQ": SymbolSpec(
        "NQ", "E-mini Nasdaq", "futures", "cme_globex", 0.25, 5.0, 20.0
    ),
    "CL": SymbolSpec(
        "CL", "Crude Oil WTI", "futures", "cme_globex", 0.01, 10.0, 1000.0
    ),
    "GC": SymbolSpec(
        "GC", "Gold", "futures", "cme_globex", 0.10, 10.0, 100.0
    ),
    "ZB": SymbolSpec(
        "ZB", "30-Year Treasury", "futures", "cme_globex", 1 / 32, 31.25, 1000.0
    ),
    "RTY": SymbolSpec(
        "RTY", "Russell 2000", "futures", "cme_globex", 0.10, 5.0, 50.0
    ),
    # --- Forex ---
    "EURUSD": SymbolSpec(
        "EURUSD", "Euro/Dollar", "forex", "forex_24_5", 0.0001, 10.0, 100000.0
    ),
    "GBPUSD": SymbolSpec(
        "GBPUSD", "Pound/Dollar", "forex", "forex_24_5", 0.0001, 10.0, 100000.0
    ),
    "USDJPY": SymbolSpec(
        "USDJPY", "Dollar/Yen", "forex", "forex_24_5", 0.01, 9.0, 100000.0
    ),
    "USDCHF": SymbolSpec(
        "USDCHF", "Dollar/Swiss", "forex", "forex_24_5", 0.0001, 10.0, 100000.0
    ),
    "AUDUSD": SymbolSpec(
        "AUDUSD", "Aussie/Dollar", "forex", "forex_24_5", 0.0001, 10.0, 100000.0
    ),
    # --- Crypto ---
    "BTCUSD": SymbolSpec(
        "BTCUSD", "Bitcoin", "crypto", "crypto_24_7", 1.0, 1.0, 1.0
    ),
    "ETHUSD": SymbolSpec(
        "ETHUSD", "Ethereum", "crypto", "crypto_24_7", 0.01, 0.01, 1.0
    ),
    "SOLUSD": SymbolSpec(
        "SOLUSD", "Solana", "crypto", "crypto_24_7", 0.01, 0.01, 1.0
    ),
    "BNBUSD": SymbolSpec(
        "BNBUSD", "BNB", "crypto", "crypto_24_7", 0.01, 0.01, 1.0
    ),
    "XRPUSD": SymbolSpec(
        "XRPUSD", "XRP", "crypto", "crypto_24_7", 0.0001, 0.0001, 1.0
    ),
    # --- Equities ---
    "TSLA": SymbolSpec(
        "TSLA", "Tesla", "equity", "equity_us", 0.01, 0.01, 1.0
    ),
    "NVDA": SymbolSpec(
        "NVDA", "NVIDIA", "equity", "equity_us", 0.01, 0.01, 1.0
    ),
    "AAPL": SymbolSpec(
        "AAPL", "Apple", "equity", "equity_us", 0.01, 0.01, 1.0
    ),
    "MSFT": SymbolSpec(
        "MSFT", "Microsoft", "equity", "equity_us", 0.01, 0.01, 1.0
    ),
    "AMZN": SymbolSpec(
        "AMZN", "Amazon", "equity", "equity_us", 0.01, 0.01, 1.0
    ),
}

TICK_VALUES: dict[str, float] = {sym: spec.tick_value for sym, spec in SYMBOLS.items()}
TICK_SIZES: dict[str, float] = {sym: spec.tick_size for sym, spec in SYMBOLS.items()}


def normalize_symbol(symbol: str) -> str:
    """MES, mes, EUR/USD → EURUSD."""
    return symbol.upper().replace("/", "").replace("-", "").strip()


def get_symbol(symbol: str) -> SymbolSpec:
    key = normalize_symbol(symbol)
    if key not in SYMBOLS:
        raise KeyError(f"Unknown symbol: {symbol}")
    return SYMBOLS[key]


def get_symbol_or_none(symbol: str) -> SymbolSpec | None:
    key = normalize_symbol(symbol)
    return SYMBOLS.get(key)


def session_kind(symbol: str) -> SessionKind | None:
    spec = get_symbol_or_none(symbol)
    return spec.session if spec else None


def massive_symbol(symbol: str) -> str:
    """
    Polygon / Massive aggregate ticker prefix.
    Futures & forex: C:SYMBOL
    Crypto: X:SYMBOL
    US equities: plain ticker (e.g. TSLA)
    """
    spec = get_symbol(symbol)
    sym = spec.symbol
    if spec.asset_class == "crypto":
        return f"X:{sym}"
    if spec.asset_class in ("futures", "forex"):
        return f"C:{sym}"
    return sym


def polygon_ticker_map() -> dict[str, str]:
    """All registered symbols → Massive/Polygon tickers."""
    return {sym: massive_symbol(sym) for sym in SYMBOLS}


def watcher_symbols_from_env() -> list[str]:
    raw = os.getenv("WATCHER_SYMBOLS", "")
    if raw.strip():
        return [normalize_symbol(s) for s in raw.split(",") if s.strip()]
    return list(DEFAULT_WATCHER_SYMBOLS)
