"""Base class for method analysis agents."""

from abc import abstractmethod

import pandas as pd

from agents.base import BaseAgent
from agents.pipeline_context import PipelineContext
from agents.schemas import MethodOutput


class BaseMethodAgent(BaseAgent):
    method_name: str = "unknown"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        if ctx.ohlcv is None or ctx.ohlcv.empty:
            ctx.method_outputs.append(
                MethodOutput(
                    method=self.method_name,
                    skipped=True,
                    skip_reason="missing_ohlcv",
                )
            )
            return ctx

        try:
            output = self.analyze(ctx.symbol, ctx.ohlcv, ctx.swings, ctx.historical_sample_size)
            ctx.method_outputs.append(output)
        except Exception as exc:
            ctx.method_outputs.append(
                MethodOutput(
                    method=self.method_name,
                    skipped=True,
                    skip_reason=str(exc),
                )
            )
        return ctx

    @abstractmethod
    def analyze(
        self,
        symbol: str,
        ohlcv: pd.DataFrame,
        swings: list[tuple[int, float]],
        historical_sample_size: int,
    ) -> MethodOutput:
        ...
