"""Convert target notional USD to OANDA forex units."""


def usd_to_units(symbol: str, order_usd: float, entry_price: float) -> int:
    """Convert target notional USD to OANDA units (1 unit = 1 base currency)."""
    if entry_price <= 0 or order_usd <= 0:
        return 0
    sym = symbol.upper()
    # EURUSD, GBPUSD, AUDUSD: base units; notional ≈ units * price for USD quote
    if sym.endswith("USD") and not sym.startswith("USD"):
        units = int(order_usd / entry_price)
    # USDJPY, USDCHF: USD is base
    elif sym.startswith("USD"):
        units = int(order_usd)
    else:
        units = int(order_usd / entry_price)
    return max(1, units)
