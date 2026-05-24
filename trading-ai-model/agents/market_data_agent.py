"""Market Data Agent — ingests and stores candles; no trade decisions."""

from datetime import datetime

import pandas as pd

from agents.base import BaseAgent
from agents.pipeline_context import PipelineContext
from data.processors.ohlcv_processor import OHLCVProcessor


class MarketDataAgent(BaseAgent):
    name = "market_data"

    def __init__(self):
        self.processor = OHLCVProcessor()

    def run(self, ctx: PipelineContext) -> PipelineContext:
        ctx.ohlcv = self.processor.clean(ctx.ohlcv)
        ctx.metadata["data_bars"] = len(ctx.ohlcv)
        ctx.metadata["data_stale"] = False
        ctx.timestamp = datetime.utcnow()
        return ctx

    def validate(self, ctx: PipelineContext) -> bool:
        return ctx.ohlcv is not None and not ctx.ohlcv.empty and len(ctx.ohlcv) >= 20
