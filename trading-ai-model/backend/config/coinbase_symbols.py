"""Map internal crypto symbols to Coinbase Advanced Trade product ids."""

from __future__ import annotations

from config.symbols import get_symbol_or_none

# Internal symbol → Coinbase product_id (BASE-QUOTE)
COINBASE_PRODUCT_MAP: dict[str, str] = {
    "BTCUSD": "BTC-USD",
    "ETHUSD": "ETH-USD",
    "SOLUSD": "SOL-USD",
    "BNBUSD": "BNB-USD",
    "XRPUSD": "XRP-USD",
}


def is_coinbase_tradable(symbol: str) -> bool:
    sym = symbol.upper().replace("/", "")
    spec = get_symbol_or_none(sym)
    if spec and spec.asset_class == "crypto":
        return True
    return sym in COINBASE_PRODUCT_MAP


def to_product_id(symbol: str) -> str | None:
    sym = symbol.upper().replace("/", "")
    if sym in COINBASE_PRODUCT_MAP:
        return COINBASE_PRODUCT_MAP[sym]
    if sym.endswith("USD") and len(sym) > 3:
        base = sym[:-3]
        return f"{base}-USD"
    return None
