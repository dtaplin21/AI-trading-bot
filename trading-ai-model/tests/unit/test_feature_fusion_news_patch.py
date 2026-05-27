"""Tests for pipeline news fusion patch."""

from datetime import datetime, timezone

from agents.news.news_schemas import NewsFeatures
from agents.pipeline_context import PipelineContext
from agents.schemas import ChartStructure, MethodOutput
from pipeline.feature_fusion_news_patch import (
    apply_news_signal_rank_penalty,
    inject_news_into_features_dict,
    resolve_technical_direction,
)
from pipeline.schemas import NewsIntelligenceBlock
import pandas as pd


def _ctx(**kwargs) -> PipelineContext:
    defaults = {
        "symbol": "MES",
        "timeframe": "5m",
        "ohlcv": pd.DataFrame({"close": [100.0]}),
        "timestamp": datetime.now(timezone.utc),
    }
    defaults.update(kwargs)
    return PipelineContext(**defaults)


def test_resolve_technical_direction_from_markov():
    ctx = _ctx(
        method_outputs=[
            MethodOutput(
                method="markov_state",
                features={"markov_continuation_probability": 0.85, "markov_reversal_probability": 0.15},
                confidence=0.8,
            )
        ]
    )
    assert resolve_technical_direction(ctx) == 1


def test_resolve_technical_direction_from_chart_fallback():
    ctx = _ctx(chart=ChartStructure(trend_direction="down"))
    assert resolve_technical_direction(ctx) == -1


def test_inject_news_into_features_dict():
    news = NewsFeatures(
        news_impact_score=0.9,
        trading_blocked=True,
        reduce_size_recommended=True,
        news_risk_reason="CPI release",
    )
    features = inject_news_into_features_dict({}, news)
    assert features["news_trading_blocked"] is True
    assert features["news_reduce_size"] is True
    assert features["trading_blocked"] is True
    assert features["news_impact_score"] == 0.9


def test_apply_news_signal_rank_penalty_blocked():
    news = NewsFeatures(trading_blocked=True, news_impact_score=0.5)
    assert apply_news_signal_rank_penalty(80, news) == 65


def test_fused_feature_set_news_fields_from():
    news = NewsFeatures(news_sentiment_score=0.42, breaking_news_active=True)
    fields = NewsIntelligenceBlock.from_news_features(news).model_dump()
    assert fields["news_sentiment_score"] == 0.42
    assert fields["breaking_news_active"] is True
