"""Map news events to futures symbols."""

from __future__ import annotations

from agents.news.news_schemas import EventType, NewsEvent, SymbolNewsImpact

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
MACRO_EVENT_TYPES = {
    EventType.CPI,
    EventType.FOMC,
    EventType.NFP,
    EventType.FED_POLICY,
    EventType.BREAKING,
    EventType.INFLATION,
    EventType.EMPLOYMENT,
}


class NewsSymbolMapper:
    """Maps headlines to affected futures symbols."""

    def map(self, event: NewsEvent) -> list[SymbolNewsImpact]:
        text = f"{event.headline} {event.summary or ''}".lower()
        impacts: list[SymbolNewsImpact] = []

        for symbol, keywords in SYMBOL_KEYWORDS.items():
            relevance = sum(1 for kw in keywords if kw in text) / max(len(keywords), 1)
            if relevance <= 0:
                continue
            direction = 1 if event.sentiment_score > 0.15 else (-1 if event.sentiment_score < -0.15 else 0)
            confidence = min(1.0, event.impact_score * (0.5 + relevance))
            impacts.append(
                SymbolNewsImpact(
                    news_event_id=event.id or "",
                    symbol=symbol,
                    impact_direction=direction,
                    confidence=confidence,
                )
            )

        if not impacts and event.event_type in MACRO_EVENT_TYPES:
            for symbol in MACRO_SYMBOLS:
                direction = 1 if event.sentiment_score > 0 else (-1 if event.sentiment_score < 0 else 0)
                impacts.append(
                    SymbolNewsImpact(
                        news_event_id=event.id or "",
                        symbol=symbol,
                        impact_direction=direction,
                        confidence=event.impact_score * 0.7,
                    )
                )

        event.symbols_affected = list({i.symbol for i in impacts})
        return impacts
