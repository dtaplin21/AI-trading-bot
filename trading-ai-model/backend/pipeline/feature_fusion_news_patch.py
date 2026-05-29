"""
pipeline/feature_fusion_news_patch.py

Market News Intelligence integration for Feature Fusion.
Inject at construction time — no global state.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, Protocol

from agents.news.news_schemas import NewsFeatures
from agents.pipeline_context import PipelineContext
from pipeline.schemas import FusedFeatureSet, NewsIntelligenceBlock

logger = logging.getLogger(__name__)


class NewsAgentProtocol(Protocol):
    def get_news_features(
        self,
        symbol: str,
        technical_direction: int = 0,
        at: Optional[datetime] = None,
    ) -> NewsFeatures: ...


def resolve_technical_direction(ctx: PipelineContext) -> int:
    """
    Directional bias for news conflict scoring.
    Prefers Markov continuation vs reversal; falls back to chart trend.
    """
    for output in ctx.method_outputs:
        if output.skipped or output.method != "markov_state":
            continue
        cont = float(output.features.get("markov_continuation_probability", 0.5))
        rev = float(output.features.get("markov_reversal_probability", 1.0 - cont))
        if cont > rev + 0.08:
            return 1
        if rev > cont + 0.08:
            return -1

    if ctx.chart:
        if ctx.chart.trend_direction == "up":
            return 1
        if ctx.chart.trend_direction == "down":
            return -1
    return 0


def fetch_news_features(
    news_agent: Optional[NewsAgentProtocol],
    symbol: str,
    technical_direction: int,
    at: Optional[datetime] = None,
) -> NewsFeatures:
    if not news_agent:
        return NewsFeatures()
    return news_agent.get_news_features(
        symbol=symbol,
        technical_direction=technical_direction,
        at=at,
    )


def inject_news_into_features_dict(
    features: dict,
    news: NewsFeatures,
) -> dict:
    """Merge news fields into the flat ML feature dict (legacy + patch names)."""
    patch = NewsIntelligenceBlock.from_news_features(news).model_dump()
    features.update(patch)
    # Aliases consumed by prediction agent + feature_vector.py
    features["trading_blocked"] = news.trading_blocked
    features["reduce_size_recommended"] = news.reduce_size_recommended
    features["manual_approval_required"] = news.manual_approval_required
    features["news_trading_blocked"] = news.trading_blocked
    features["news_reduce_size_recommended"] = news.reduce_size_recommended
    features["news_manual_approval_required"] = news.manual_approval_required
    if news.latest_headline:
        features["latest_headline"] = news.latest_headline
    if news.latest_event_type:
        features["latest_event_type"] = news.latest_event_type
    if news.latest_sentiment_label:
        features["latest_sentiment_label"] = news.latest_sentiment_label
    return features


def apply_news_signal_rank_penalty(signal_rank: int, news: NewsFeatures) -> int:
    """Reduce signal rank when news volatility/impact or blocks are active."""
    penalty = news.volatility_risk_score * news.news_impact_score
    if news.trading_blocked:
        penalty = 1.0
    elif news.reduce_size_recommended:
        penalty = max(penalty, 0.35)
    if news.news_conflict_score > 0.5:
        penalty = max(penalty, news.news_conflict_score * 0.5)
    if penalty <= 0:
        return signal_rank
    return int(max(0, signal_rank - penalty * 15))


def fuse_news_into_pipeline(
    ctx: PipelineContext,
    features: dict,
    news_agent: Optional[NewsAgentProtocol],
) -> tuple[dict, NewsFeatures, int]:
    """
    Full news fusion step: fetch, inject into dict, compute rank penalty.
    Returns (updated_features, news_features, penalized_signal_rank placeholder).
    Caller applies rank penalty after base signal_rank is computed.
    """
    tech_dir = resolve_technical_direction(ctx)
    news = fetch_news_features(news_agent, ctx.symbol, tech_dir, ctx.timestamp)
    ctx.metadata["news_features"] = news.model_dump()
    inject_news_into_features_dict(features, news)
    logger.debug(
        "FeatureFusion news: %s | blocked=%s impact=%.2f conflict=%.2f",
        ctx.symbol,
        news.trading_blocked,
        news.news_impact_score,
        news.news_conflict_score,
    )
    return features, news, tech_dir


class FeatureFusionAgent:
    """Pipeline-facing fusion agent — builds FusedFeatureSet from pipeline context."""

    def __init__(self, news_agent=None) -> None:
        from agents.feature_fusion_agent import FeatureFusionAgent as AgentFusion

        self._inner = AgentFusion(news_agent=news_agent)

    def build_from_context(self, ctx: PipelineContext) -> FusedFeatureSet:
        self._inner.run(ctx)
        if ctx.fused is None:
            raise ValueError("Feature fusion produced no fused features")
        return FusedFeatureSet.from_fused_features(ctx.fused)

    def build(self, ctx: PipelineContext, **kwargs) -> FusedFeatureSet:
        """Alias — kwargs ignored; ctx must include method outputs and confluence."""
        return self.build_from_context(ctx)
