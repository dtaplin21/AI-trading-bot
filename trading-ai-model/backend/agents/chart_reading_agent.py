"""Chart Reading Agent — swing/trend/range structure (the 'eyes')."""

import numpy as np
import pandas as pd

from agents.base import BaseAgent
from agents.pipeline_context import PipelineContext
from agents.schemas import ChartStructure


class ChartReadingAgent(BaseAgent):
    name = "chart_reading"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        ohlcv = ctx.ohlcv
        highs = ohlcv["high"].values
        lows = ohlcv["low"].values
        closes = ohlcv["close"].values

        swing_highs, swing_lows, swings = self._detect_swings(highs, lows)
        ctx.swings = swings

        trend = self._trend(closes)
        hh, hl, lh, ll = self._structure(swing_highs, swing_lows)

        ctx.chart = ChartStructure(
            swing_highs=swing_highs[-5:],
            swing_lows=swing_lows[-5:],
            trend_direction=trend,
            support_levels=sorted(set(swing_lows[-3:])),
            resistance_levels=sorted(set(swing_highs[-3:])),
            higher_highs=hh,
            higher_lows=hl,
            lower_highs=lh,
            lower_lows=ll,
            session_high=float(highs.max()),
            session_low=float(lows.min()),
            vwap_relation=self._vwap_relation(ohlcv),
        )
        return ctx

    def _detect_swings(
        self, highs: np.ndarray, lows: np.ndarray, window: int = 2
    ) -> tuple[list[float], list[float], list[tuple[int, float]]]:
        swing_highs, swing_lows, swings = [], [], []
        for i in range(window, len(highs) - window):
            if highs[i] == max(highs[i - window : i + window + 1]):
                swing_highs.append(float(highs[i]))
                swings.append((i, float(highs[i])))
            if lows[i] == min(lows[i - window : i + window + 1]):
                swing_lows.append(float(lows[i]))
                swings.append((i, float(lows[i])))
        swings.sort(key=lambda x: x[0])
        return swing_highs, swing_lows, swings

    def _trend(self, closes: np.ndarray) -> str:
        if len(closes) < 10:
            return "unknown"
        slope = closes[-1] - closes[-10]
        if slope > 0:
            return "up"
        if slope < 0:
            return "down"
        return "range"

    def _structure(self, highs: list[float], lows: list[float]) -> tuple[bool, bool, bool, bool]:
        hh = len(highs) >= 2 and highs[-1] > highs[-2]
        hl = len(lows) >= 2 and lows[-1] > lows[-2]
        lh = len(highs) >= 2 and highs[-1] < highs[-2]
        ll = len(lows) >= 2 and lows[-1] < lows[-2]
        return hh, hl, lh, ll

    def _vwap_relation(self, ohlcv: pd.DataFrame) -> str:
        if "volume" not in ohlcv.columns:
            return "unknown"
        typical = (ohlcv["high"] + ohlcv["low"] + ohlcv["close"]) / 3
        vwap = (typical * ohlcv["volume"]).sum() / (ohlcv["volume"].sum() + 1e-9)
        price = float(ohlcv["close"].iloc[-1])
        if price > vwap * 1.001:
            return "above"
        if price < vwap * 0.999:
            return "below"
        return "at"
