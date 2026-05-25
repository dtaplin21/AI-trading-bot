"""Map news events to futures symbols."""

from __future__ import annotations

from agents.news.news_schemas import NewsEvent, SymbolNewsImpact

# Keyword → symbols affected
SYMBOL_KEYWORDS: dict[str, list[str]] = {
    "MES": ["s&p", "sp500", "s&p 500", "es futures", "mes", "equity index"],
    "ES": ["s&p", "sp500", "s&p 500", "es futures", "e-mini s&p"],
    "NQ": ["nasdaq", "tech stocks", "nq", "mnq", "mega cap"],
    "MNQ": ["nasdaq", "mnq", "tech"],
    "RTY": ["russell", "small cap", "rty"],
    "YM": ["dow", "djia", "industrial", "ym", "mym"],
    "MYM": ["dow", "djia", "mym"],
}

MACRO_SYMBOLS = ["MES", "ES", "NQ", "MNQ", "RTY", "YM"]


class NewsSymbolMapper:
    """Maps headlines to affected futures symbols."""

    def map(self, event: NewsEvent) -> list[SymbolNewsImpact]:
        text = f"{event.headline} {event.summary}".lower()
        impacts: list[SymbolNewsImpact] = []

        for symbol, keywords in SYMBOL_KEYWORDS.items():
            relevance = sum(1 for kw in keywords if kw in text) / max(len(keywords), 1)
            if relevance <= 0:
                continue
            direction = 1 if event.sentiment_score > 0.15 else (-1 if event.sentiment_score < -0.15 else 0)
            score = event.impact_score * (0.5 + relevance)
            impacts.append(
                SymbolNewsImpact(
                    symbol=symbol,
                    impact_score=score,
                    direction_bias=direction,
                    relevance=relevance,
                )
            )

        if not impacts and event.event_type.value in ("fed", "cpi", "nfp", "macro", "breaking"):
            for symbol in MACRO_SYMBOLS:
                impacts.append(
                    SymbolNewsImpact(
                        symbol=symbol,
                        impact_score=event.impact_score * 0.7,
                        direction_bias=1 if event.sentiment_score > 0 else (-1 if event.sentiment_score < 0 else 0),
                        relevance=0.5,
                    )
                )

        event.symbols_affected = list({i.symbol for i in impacts})
        return impacts
