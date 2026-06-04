"""Map internal forex symbols to OANDA v20 instrument names."""

from __future__ import annotations

from config.symbols import FOREX_SYMBOLS, get_symbol_or_none


def is_oanda_tradable(symbol: str) -> bool:
    sym = symbol.upper().replace("/", "")
    spec = get_symbol_or_none(sym)
    if spec and spec.asset_class == "forex":
        return True
    return sym in FOREX_SYMBOLS


def to_instrument(symbol: str) -> str | None:
    """EURUSD → EUR_USD (OANDA v20 instrument id)."""
    sym = symbol.upper().replace("/", "")
    if not is_oanda_tradable(sym):
        return None
    if len(sym) == 6:
        return f"{sym[:3]}_{sym[3:]}"
    return None
