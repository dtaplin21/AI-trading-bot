"""Converts raw OHLCV to numeric psychology features for ML."""

import pandas as pd
from engines.price_action.candlestick_psychology_service import CandlestickPsychologyService


class CandleFeatureExtractor:
    def __init__(self):
        self.service = CandlestickPsychologyService()

    def extract(self, ohlcv: pd.DataFrame) -> dict:
        c = self.service.analyze(ohlcv)
        return {k: getattr(c, k) for k in c.__dataclass_fields__}

