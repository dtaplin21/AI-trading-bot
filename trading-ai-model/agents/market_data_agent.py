"""Market Data Agent — ingests, stores in TimescaleDB; no trade decisions."""

from datetime import datetime, timezone, timedelta

import pandas as pd

from agents.base import BaseAgent
from agents.pipeline_context import PipelineContext
from config.settings import get_settings
from data.processors.ohlcv_processor import OHLCVProcessor
from data.storage.timescale_store import TimescaleStore


class MarketDataAgent(BaseAgent):
    name = "market_data"

    def __init__(self, store: TimescaleStore | None = None):
        self.processor = OHLCVProcessor()
        self.store = store or TimescaleStore()
        self.settings = get_settings()

    def run(self, ctx: PipelineContext) -> PipelineContext:
        ctx.ohlcv = self.processor.clean(ctx.ohlcv)

        if self.store.available:
            df = self._ensure_datetime_index(ctx.ohlcv)
            self.store.upsert_ohlcv(ctx.symbol, ctx.timeframe, df)
            db_bars = self.store.load_ohlcv(ctx.symbol, ctx.timeframe, limit=500)
            if not db_bars.empty and len(db_bars) >= len(ctx.ohlcv):
                ctx.ohlcv = db_bars

        ctx.metadata["data_bars"] = len(ctx.ohlcv)
        ctx.metadata["db_connected"] = self.store.available
        ctx.metadata["data_stale"] = self._is_stale(ctx)
        ctx.timestamp = datetime.now(timezone.utc)
        return ctx

    def load_from_db(self, symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame:
        return self.store.load_ohlcv(symbol, timeframe, limit=limit)

    def validate(self, ctx: PipelineContext) -> bool:
        return ctx.ohlcv is not None and not ctx.ohlcv.empty and len(ctx.ohlcv) >= 20

    def _is_stale(self, ctx: PipelineContext) -> bool:
        if ctx.ohlcv.empty:
            return True
        idx = ctx.ohlcv.index
        if isinstance(idx, pd.DatetimeIndex) and len(idx):
            last = idx[-1].to_pydatetime()
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
        elif self.store.available:
            last = self.store.latest_bar_time(ctx.symbol, ctx.timeframe)
            if last is None:
                return True
        else:
            return False

        age = datetime.now(timezone.utc) - last
        return age > timedelta(minutes=self.settings.data_stale_minutes)

    def _ensure_datetime_index(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        if not isinstance(out.index, pd.DatetimeIndex):
            out.index = pd.date_range(end=datetime.now(timezone.utc), periods=len(out), freq="5min")
        return out
