"""Per-symbol behavior profiles."""

class SymbolProfileService:
    def profile(self, symbol: str) -> dict:
        return {"symbol": symbol, "avg_daily_range": 0.0}

