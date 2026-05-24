"""Symbol definitions for futures contracts."""

from dataclasses import dataclass
from typing import Literal

SessionType = Literal["RTH", "ETH", "24H"]


@dataclass(frozen=True)
class SymbolSpec:
    symbol: str
    name: str
    tick_size: float
    tick_value: float
    point_value: float
    session: SessionType
    atr_lookback: int = 14


SYMBOLS: dict[str, SymbolSpec] = {
    "MES": SymbolSpec("MES", "Micro E-mini S&P 500", 0.25, 1.25, 5.0, "RTH"),
    "ES": SymbolSpec("ES", "E-mini S&P 500", 0.25, 12.50, 50.0, "RTH"),
    "MNQ": SymbolSpec("MNQ", "Micro E-mini Nasdaq", 0.25, 0.50, 2.0, "RTH"),
    "NQ": SymbolSpec("NQ", "E-mini Nasdaq", 0.25, 5.0, 20.0, "RTH"),
    "MYM": SymbolSpec("MYM", "Micro E-mini Dow", 1.0, 0.50, 0.50, "RTH"),
    "YM": SymbolSpec("YM", "E-mini Dow", 1.0, 5.0, 5.0, "RTH"),
}


def get_symbol(symbol: str) -> SymbolSpec:
    key = symbol.upper()
    if key not in SYMBOLS:
        raise KeyError(f"Unknown symbol: {symbol}")
    return SYMBOLS[key]
