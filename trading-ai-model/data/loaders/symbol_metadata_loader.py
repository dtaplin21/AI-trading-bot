"""Contract specs, sessions, multipliers."""

from config.symbols import get_symbol


class SymbolMetadataLoader:
    def load(self, symbol: str) -> dict:
        spec = get_symbol(symbol)
        return spec.__dict__

