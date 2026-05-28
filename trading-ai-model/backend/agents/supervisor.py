"""
Trading Supervisor Agent — orchestrates the full multi-agent pipeline.

Every candle triggers all method agents before any trade decision.
No risk approval = no trade.
"""

from datetime import datetime, timezone

import pandas as pd

from agents.confluence_agent import ConfluenceAgentRunner
from agents.audit_agent import AuditAgent
from agents.chart_reading_agent import ChartReadingAgent
from agents.execution_agent import ExecutionAgent
from agents.feature_fusion_agent import FeatureFusionAgent
from agents.learning_agent import LearningAgent
from agents.market_data_agent import MarketDataAgent
from agents.method_analysis_runner import MethodAnalysisRunner
from agents.pipeline_context import PipelineContext
from agents.prediction_agent import PredictionAgent
from agents.risk_agent import RiskAgent
from agents.schemas import PipelineDecision
from agents.trade_planning_agent import TradePlanningAgent
from agents.news_runtime import bootstrap_news_sync, get_news_agent


from risk.risk_engine import PortfolioState


class TradingSupervisor:
    """
    Controls workflow — not a chat LLM.
    Ensures all required methods run before decision layer.
    """

    def __init__(self, execution_mode: str = "paper", news_agent=None):
        self.news = news_agent or get_news_agent()
        bootstrap_news_sync()
        self.market_data = MarketDataAgent()
        self.chart_reading = ChartReadingAgent()
        self.method_runner = MethodAnalysisRunner()
        self.confluence = ConfluenceAgentRunner(news_agent=self.news)
        self.feature_fusion = FeatureFusionAgent(news_agent=self.news)
        self.prediction = PredictionAgent()
        self.trade_planning = TradePlanningAgent()
        self.risk = RiskAgent(news_agent=self.news)
        self.execution = ExecutionAgent(mode=execution_mode)
        self.learning = LearningAgent(news_agent=self.news)
        self.audit = AuditAgent(news_agent=self.news)

    def process_candle(
        self,
        symbol: str,
        ohlcv: pd.DataFrame | None = None,
        timeframe: str = "5m",
        portfolio: PortfolioState | None = None,
        historical_sample_size: int = 0,
        execute: bool = False,
        load_from_db: bool = True,
    ) -> PipelineDecision:
        if ohlcv is None or (hasattr(ohlcv, "empty") and ohlcv.empty):
            if load_from_db:
                ohlcv = self.market_data.load_from_db(symbol, timeframe)
            if ohlcv is None or ohlcv.empty:
                ohlcv = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        ctx = PipelineContext(
            symbol=symbol,
            timeframe=timeframe,
            ohlcv=ohlcv,
            timestamp=datetime.now(timezone.utc),
            portfolio=portfolio or PortfolioState(),
            historical_sample_size=historical_sample_size,
        )

        # 1. Market Data
        ctx = self.market_data.run(ctx)
        if not self.market_data.validate(ctx):
            ctx.metadata["pipeline_halted"] = "insufficient_data"
            return ctx.to_decision()

        # 2. Chart Reading
        ctx = self.chart_reading.run(ctx)

        # 3. All Method Agents (every method, every time)
        ctx = self.method_runner.run(ctx)
        if not ctx.metadata.get("all_methods_ran"):
            ctx.metadata["pipeline_halted"] = "incomplete_method_review"

        # 4. Confluence — world state brain (all methods → one report)
        ctx = self.confluence.run(ctx)

        # 5. Feature Fusion
        ctx = self.feature_fusion.run(ctx)

        # 6. Prediction
        ctx = self.prediction.run(ctx)

        # 7. Trade Planning (MCTS)
        ctx = self.trade_planning.run(ctx)

        # 8. Risk Agent (veto gate)
        ctx = self.risk.run(ctx)

        # Store world state before execution so paper trades link to snapshots
        if ctx.confluence and not ctx.metadata.get("world_state_stored"):
            self.learning._store_world_state(ctx)

        # 9. Execution (only if approved and execute=True)
        if execute and ctx.risk and ctx.risk.approved:
            ctx = self.execution.run(ctx)
        else:
            ctx.execution = None

        # 10. Learning (always log)
        ctx = self.learning.run(ctx)

        # 11. Audit / Explainability
        ctx = self.audit.run(ctx)

        return ctx.to_decision()

    def explain_last(self, decision: PipelineDecision) -> str:
        if decision.llm_explanation:
            return decision.llm_explanation
        if not decision.audit:
            return "No explanation available."
        lines = [decision.audit.summary] + [f"- {r}" for r in decision.audit.reasons]
        return "\n".join(lines)
