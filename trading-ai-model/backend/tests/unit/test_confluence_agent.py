"""Unit tests for ConfluenceAgent."""

import pandas as pd

from agents.news.news_schemas import NewsFeatures
from agents.pipeline_context import PipelineContext
from agents.supervisor import TradingSupervisor
from pipeline.confluence_agent import ConfluenceAgent
from pipeline.confluence_adapter import chart_from_context
from pipeline.schemas import AgentStatus, CandlestickOutput, MomentumOutput
from tests.fixtures.sample_ohlcv import sample_ohlcv


def _small_ohlcv() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [100.0, 101.0, 102.0, 103.0, 104.0],
            "high": [101.0, 102.0, 103.0, 104.0, 105.0],
            "low": [99.0, 100.0, 101.0, 102.0, 103.0],
            "close": [100.5, 101.5, 102.5, 103.5, 104.5],
            "volume": [1000, 1100, 1200, 1300, 1400],
        }
    )


def test_confluence_agent_produces_report():
    agent = ConfluenceAgent()
    chart = chart_from_context(
        PipelineContext(symbol="MES", timeframe="5m", ohlcv=_small_ohlcv())
    )
    news = NewsFeatures(news_sentiment_score=0.1)
    candle = CandlestickOutput(
        status=AgentStatus.OK,
        confidence=0.7,
        open_close_direction=1,
        wick_rejection_score=0.8,
        reversal_probability=0.6,
        momentum_score=0.5,
        pattern="hammer",
    )
    momentum = MomentumOutput(
        status=AgentStatus.OK,
        confidence=0.65,
        momentum_direction=1,
        momentum_score=0.7,
        acceleration_score=0.6,
        volume_shift_score=0.4,
    )

    report = agent.analyze(chart=chart, news=news, candle=candle, momentum=momentum)

    assert report.symbol == "MES"
    assert report.total_voting >= 2
    assert 0.0 <= report.confluence_score <= 1.0
    assert report.probability_statement
    assert "guess" in report.probability_statement.lower()


def test_supervisor_runs_confluence_step():
    sup = TradingSupervisor()
    decision = sup.process_candle(
        "MES",
        ohlcv=sample_ohlcv(60),
        timeframe="5m",
        execute=False,
        load_from_db=False,
    )
    assert decision.fused_features is not None


def test_confluence_blocks_when_news_blocked():
    agent = ConfluenceAgent()
    chart = chart_from_context(
        PipelineContext(symbol="MES", timeframe="5m", ohlcv=_small_ohlcv())
    )
    news = NewsFeatures(news_sentiment_score=0.0, trading_blocked=True, news_risk_reason="FOMC")
    candle = CandlestickOutput(
        status=AgentStatus.OK,
        confidence=0.9,
        open_close_direction=1,
        wick_rejection_score=0.9,
        reversal_probability=0.8,
        momentum_score=0.7,
    )
    report = agent.analyze(chart=chart, news=news, candle=candle)
    assert report.ready_for_prediction is False
    assert report.news_trading_blocked is True
