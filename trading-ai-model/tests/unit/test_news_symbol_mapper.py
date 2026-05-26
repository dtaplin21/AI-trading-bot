"""Tests for NewsSymbolMapper."""

from datetime import datetime, timezone

from agents.news.news_schemas import EventType, NewsEvent, NewsMode, NewsSource, SentimentLabel
from agents.news.news_symbol_mapper import NewsSymbolMapper


def _event(**kwargs) -> NewsEvent:
    defaults = {
        "source": NewsSource.RSS,
        "headline": "Test headline",
        "published_at": datetime.now(timezone.utc),
        "id": "evt-1",
    }
    defaults.update(kwargs)
    return NewsEvent(**defaults)


def test_fomc_maps_index_and_bond_futures():
    mapper = NewsSymbolMapper()
    event = _event(
        headline="FOMC holds rates steady",
        event_type=EventType.FOMC,
        impact_score=0.90,
        sentiment_label=SentimentLabel.BULLISH,
    )
    impacts = mapper.map(event)

    symbols = {i.symbol for i in impacts}
    assert "ES" in symbols
    assert "MES" in symbols
    assert "ZN" in symbols
    assert {"equity_index_futures", "treasury_futures"} <= set(event.asset_classes)


def test_oil_inventory_maps_energy_futures():
    mapper = NewsSymbolMapper()
    event = _event(
        headline="EIA crude oil inventory falls",
        event_type=EventType.OIL_INVENTORY,
        impact_score=0.70,
        sentiment_label=SentimentLabel.BULLISH,
    )
    impacts = mapper.map(event)

    symbols = {i.symbol for i in impacts}
    assert symbols == {"CL", "QM", "NG"}
    assert "energy_futures" in event.asset_classes


def test_keyword_match_single_stock():
    mapper = NewsSymbolMapper()
    event = _event(
        headline="Nvidia beats earnings expectations",
        event_type=EventType.EARNINGS,
        impact_score=0.60,
        sentiment_label=SentimentLabel.BULLISH,
    )
    impacts = mapper.map(event)

    nvda = next(i for i in impacts if i.symbol == "NVDA")
    assert nvda.impact_direction == 1
    assert nvda.confidence >= 0.50
    assert "single_stock" in event.asset_classes


def test_is_symbol_affected():
    mapper = NewsSymbolMapper()
    event = _event(
        headline="Gold prices surge on safe haven demand",
        event_type=EventType.GENERAL_MARKET,
        impact_score=0.45,
        sentiment_label=SentimentLabel.BULLISH,
    )
    mapper.map(event)

    affected, confidence = mapper.is_symbol_affected(event, "GC")
    assert affected is True
    assert confidence == 0.45

    affected, confidence = mapper.is_symbol_affected(event, "MES")
    assert affected is False
    assert confidence == 0.0
