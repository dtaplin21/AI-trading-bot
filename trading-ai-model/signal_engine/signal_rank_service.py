"""Final SignalRank score (0-100) from weighted layer confirmations."""

from typing import Any, Optional

from config.model_weights import LayerWeights, get_layer_weights
from signal_engine.confirmation_weighter import ConfirmationWeighter
from signal_engine.layer_scores import LayerScores
from signal_engine.signal_schema import SignalStatus, TradingSignal


class SignalRankService:
    """Computes weighted SignalRank from all layer outputs."""

    PAPER_TRADE_THRESHOLD = 75
    WATCH_THRESHOLD = 50

    def __init__(self, weights: Optional[LayerWeights] = None):
        self.weights = weights or get_layer_weights()
        self.weighter = ConfirmationWeighter(self.weights)

    def compute_rank(self, scores: LayerScores) -> int:
        weighted = self.weighter.weight_scores(scores)
        base = sum(weighted.values()) * 100
        # Gann is modifier only (+/-), not a weighted layer
        final = base + scores.gann_modifier
        return int(max(0, min(100, round(final))))

    def determine_status(self, rank: int, risk_approved: bool) -> SignalStatus:
        if not risk_approved:
            return SignalStatus.REJECTED
        if rank >= self.PAPER_TRADE_THRESHOLD:
            return SignalStatus.PAPER_TRADE_CANDIDATE
        if rank >= self.WATCH_THRESHOLD:
            return SignalStatus.WATCH
        return SignalStatus.REJECTED

    def build_signal(
        self,
        symbol: str,
        setup: str,
        scores: LayerScores,
        payload: dict[str, Any],
        risk_approved: bool,
    ) -> TradingSignal:
        rank = self.compute_rank(scores)
        status = self.determine_status(rank, risk_approved)
        return TradingSignal(
            symbol=symbol,
            setup=setup,
            signal_rank=rank,
            risk_approved=risk_approved,
            status=status,
            **payload,
        )
