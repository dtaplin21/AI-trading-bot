"""Unit tests for ConfluenceAgent."""

import pandas as pd

from agents.news.news_schemas import NewsFeatures
from agents.pipeline_context import PipelineContext
from agents.schemas import MethodOutput
from agents.supervisor import TradingSupervisor
from pipeline.confluence_agent import ConfluenceAgent
from agents.method_analysis_runner import MethodAnalysisRunner
from pipeline.confluence_adapter import (
    ancient_number_from,
    balance_from,
    chart_from_context,
    prepare_confluence_inputs,
)
from pipeline.schemas import (
    AgentStatus,
    AncientNumberOutput,
    BalanceLineOutput,
    CandlestickOutput,
    MonteCarloOutput,
    MomentumOutput,
    StrategyMathOutput,
)
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


def test_all_thirteen_methods_can_vote():
    agent = ConfluenceAgent()
    chart = chart_from_context(
        PipelineContext(symbol="MES", timeframe="5m", ohlcv=_small_ohlcv())
    )
    news = NewsFeatures(news_sentiment_score=0.1)

    report = agent.analyze(
        chart=chart,
        news=news,
        candle=CandlestickOutput(
            status=AgentStatus.OK,
            confidence=0.7,
            open_close_direction=1,
            wick_rejection_score=0.8,
            reversal_probability=0.6,
            momentum_score=0.5,
        ),
        momentum=MomentumOutput(
            status=AgentStatus.OK,
            confidence=0.65,
            momentum_direction=1,
            momentum_score=0.7,
            acceleration_score=0.6,
            volume_shift_score=0.4,
        ),
        balance=BalanceLineOutput(
            status=AgentStatus.OK,
            confidence=0.6,
            balance_price=100.0,
            above_balance=True,
            at_balance=False,
        ),
        ancient_number=AncientNumberOutput(
            status=AgentStatus.OK,
            confidence=0.55,
            number_zone="66.6%",
            level_active=True,
            active_cycles=["7", "21"],
        ),
        strategy=StrategyMathOutput(
            status=AgentStatus.OK,
            confidence=0.7,
            expected_value=5.0,
            win_rate=0.6,
            sample_size=50,
        ),
        monte_carlo=MonteCarloOutput(
            status=AgentStatus.OK,
            confidence=0.65,
            target_hit_prob=0.62,
        ),
    )

    voted_names = {v.method_name for v in report.votes}
    assert "balance_line" in voted_names
    assert "ancient_number" in voted_names
    assert "strategy_math" in voted_names
    assert "monte_carlo" in voted_names
    assert "balance_line" not in report.excluded_methods
    assert "ancient_number" not in report.excluded_methods


def test_balance_adapter_reads_balance_line_feature():
    raw = MethodOutput(
        method="balance_line",
        confidence=0.65,
        features={
            "balance_line": 4521.5,
            "at_balance_line": False,
            "above_balance": True,
        },
    )
    out = balance_from(raw)
    assert out is not None
    assert out.balance_price == 4521.5
    assert out.above_balance is True
    assert out.at_balance is False


def test_ancient_number_adapter():
    raw = MethodOutput(
        method="ancient_number",
        confidence=0.55,
        features={
            "active_cycles": [7, 21],
            "number_zone": "66.6%",
            "mirror_target": 4500.0,
        },
    )
    out = ancient_number_from(raw)
    assert out is not None
    assert out.level_active is True
    assert out.number_zone == "66.6%"


def test_prepare_confluence_inputs_includes_all_method_slots():
    ctx = PipelineContext(symbol="MES", timeframe="5m", ohlcv=sample_ohlcv(60))
    ctx = MethodAnalysisRunner().run(ctx)
    news = NewsFeatures(news_sentiment_score=0.0)
    inputs = prepare_confluence_inputs(ctx, news)
    assert "ancient_number" in inputs
    assert "balance" in inputs
    assert "strategy" in inputs
    assert "monte_carlo" in inputs
    # At least balance and ancient_number adapters produce output from live agents
    assert inputs["balance"] is not None
    assert inputs["ancient_number"] is not None
