"""
agents/news/news_symbol_mapper.py

Maps every news event to the specific trading symbols it affects.
Uses keyword matching + entity detection on headline/summary.
Returns a list of SymbolNewsImpact records with directional confidence.

This is critical because the Feature Fusion Agent needs to know
whether a news item affects THE SPECIFIC SYMBOL being traded right now.
"""
from __future__ import annotations

import logging

from agents.news.news_schemas import EventType, NewsEvent, SentimentLabel, SymbolNewsImpact

logger = logging.getLogger(__name__)

SYMBOL_KEYWORDS: dict[str, list[str]] = {
    "ES": ["s&p", "s&p 500", "sp500", "spy", "equity market", "stock market", "dow jones", "indices", "index futures"],
    "MES": ["s&p", "s&p 500", "sp500", "micro es", "micro s&p"],
    "NQ": ["nasdaq", "tech stocks", "technology sector", "qqq", "nasdaq 100", "faang", "magnificent seven"],
    "MNQ": ["nasdaq", "micro nq", "micro nasdaq"],
    "RTY": ["russell 2000", "small cap", "iwm", "rty", "small-cap stocks"],
    "YM": ["dow jones", "djia", "blue chip", "dow 30"],
    "MYM": ["dow jones", "micro ym"],
    "CL": ["crude oil", "oil price", "wti", "brent", "opec", "petroleum", "oil inventory", "eia crude", "energy"],
    "QM": ["crude oil", "micro crude", "wti"],
    "NG": ["natural gas", "natgas", "lng", "henry hub"],
    "GC": ["gold", "gold price", "precious metals", "haven", "safe haven", "gold futures"],
    "SI": ["silver", "silver price", "precious metals"],
    "PL": ["platinum"],
    "HG": ["copper", "copper price", "industrial metals"],
    "ZB": ["treasury", "30-year", "long bond", "treasury yield", "fed funds", "interest rate", "bond market"],
    "ZN": ["10-year", "treasury note", "treasury yield", "interest rate", "bond"],
    "ZF": ["5-year", "treasury", "bond"],
    "ZT": ["2-year", "treasury", "short rate"],
    "6E": ["euro", "eur/usd", "ecb", "european central bank", "eurozone"],
    "6J": ["yen", "jpy", "bank of japan", "boj", "japan"],
    "6B": ["pound", "gbp", "bank of england", "boe", "brexit", "uk economy"],
    "6C": ["canadian dollar", "cad", "bank of canada", "canada"],
    "6A": ["australian dollar", "aud", "rba", "australia"],
    "ZC": ["corn", "grain", "usda", "crop report"],
    "ZS": ["soybean", "soy", "usda"],
    "ZW": ["wheat", "grain"],
    "TSLA": ["tesla", "elon musk", "ev market", "electric vehicle"],
    "AAPL": ["apple", "iphone", "tim cook", "app store"],
    "NVDA": ["nvidia", "gpu", "ai chips", "cuda", "jensen huang"],
    "MSFT": ["microsoft", "azure", "copilot", "satya nadella"],
    "AMZN": ["amazon", "aws", "prime", "andy jassy"],
    "META": ["meta", "facebook", "instagram", "zuckerberg"],
    "GOOGL": ["google", "alphabet", "gemini", "youtube", "sundar pichai"],
}

BROAD_INDEX_EVENTS = {
    EventType.FOMC,
    EventType.FED_POLICY,
    EventType.FED_SPEECH,
    EventType.CPI,
    EventType.PPI,
    EventType.NFP,
    EventType.GDP,
    EventType.JOBLESS_CLAIMS,
    EventType.INFLATION,
    EventType.EMPLOYMENT,
    EventType.GEOPOLITICAL,
    EventType.BREAKING,
    EventType.TREASURY_YIELD,
}

INDEX_FUTURES = ["ES", "MES", "NQ", "MNQ", "RTY", "YM", "MYM"]
BOND_FUTURES = ["ZB", "ZN", "ZF", "ZT"]
ENERGY_FUTURES = ["CL", "QM", "NG"]


class NewsSymbolMapper:
    """Maps news events to the symbols they affect and scores directional impact."""

    def map(self, event: NewsEvent) -> list[SymbolNewsImpact]:
        """
        Returns SymbolNewsImpact records for this news event.
        Each record captures which symbol is affected, direction, and confidence.
        """
        text = f"{event.headline} {event.summary or ''}".lower()
        direction = self._sentiment_to_direction(event.sentiment_label)
        results: dict[str, SymbolNewsImpact] = {}

        if event.event_type in BROAD_INDEX_EVENTS:
            for sym in INDEX_FUTURES:
                results[sym] = SymbolNewsImpact(
                    news_event_id=event.id or "",
                    symbol=sym,
                    impact_direction=direction,
                    confidence=min(0.95, event.impact_score * 0.9),
                )
            if event.event_type in {EventType.FOMC, EventType.FED_POLICY, EventType.TREASURY_YIELD, EventType.CPI}:
                for sym in BOND_FUTURES:
                    bond_direction = -direction if event.event_type in {EventType.CPI, EventType.INFLATION} else direction
                    results[sym] = SymbolNewsImpact(
                        news_event_id=event.id or "",
                        symbol=sym,
                        impact_direction=bond_direction,
                        confidence=min(0.90, event.impact_score * 0.85),
                    )

        if event.event_type == EventType.OIL_INVENTORY:
            for sym in ENERGY_FUTURES:
                results[sym] = SymbolNewsImpact(
                    news_event_id=event.id or "",
                    symbol=sym,
                    impact_direction=direction,
                    confidence=0.88,
                )

        for symbol, keywords in SYMBOL_KEYWORDS.items():
            if symbol in results:
                continue
            matched = [kw for kw in keywords if kw in text]
            if matched:
                confidence = min(0.95, 0.50 + len(matched) * 0.10)
                results[symbol] = SymbolNewsImpact(
                    news_event_id=event.id or "",
                    symbol=symbol,
                    impact_direction=direction,
                    confidence=round(confidence, 3),
                )

        event.symbols_affected = list(results.keys())
        event.asset_classes = self._asset_classes(results)

        logger.debug(
            "Symbol mapper: event '%s' → %d symbols affected: %s",
            event.headline[:60],
            len(results),
            list(results.keys()),
        )
        return list(results.values())

    def is_symbol_affected(self, event: NewsEvent, symbol: str) -> tuple[bool, float]:
        """Quick check: does this event affect this specific symbol?"""
        normalized = symbol.upper().replace("/", "")
        for sym in event.symbols_affected:
            if sym.upper() == normalized:
                return True, event.impact_score
        return False, 0.0

    def _asset_classes(self, results: dict[str, SymbolNewsImpact]) -> list[str]:
        asset_classes: set[str] = set()
        if any(s in results for s in INDEX_FUTURES):
            asset_classes.add("equity_index_futures")
        if any(s in results for s in BOND_FUTURES):
            asset_classes.add("treasury_futures")
        if any(s in results for s in ENERGY_FUTURES):
            asset_classes.add("energy_futures")
        if any(s in results for s in ["GC", "SI", "PL", "HG"]):
            asset_classes.add("metals_futures")
        if any(s in results for s in ["6E", "6J", "6B", "6C", "6A"]):
            asset_classes.add("fx_futures")
        if any(s in results for s in ["TSLA", "AAPL", "NVDA", "MSFT", "AMZN", "META", "GOOGL"]):
            asset_classes.add("single_stock")
        return list(asset_classes)

    def _sentiment_to_direction(self, sentiment: SentimentLabel) -> int:
        if sentiment == SentimentLabel.BULLISH:
            return +1
        if sentiment == SentimentLabel.BEARISH:
            return -1
        return 0
