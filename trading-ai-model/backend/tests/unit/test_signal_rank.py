"""Tests for SignalRank service."""

from signal_engine.layer_scores import LayerScores
from signal_engine.signal_rank_service import SignalRankService
from signal_engine.signal_schema import SignalStatus


def test_rank_bounded_0_100():
    svc = SignalRankService()
    scores = LayerScores(
        candlestick=0.9, harmonic=0.8, elliott=0.7, fibonacci=0.6,
        number_zone=0.5, fractal=0.4, markov=0.6, ml=0.7, ev=0.8,
    )
    rank = svc.compute_rank(scores)
    assert 0 <= rank <= 100


def test_paper_trade_threshold():
    svc = SignalRankService()
    assert svc.determine_status(86, True) == SignalStatus.PAPER_TRADE_CANDIDATE
    assert svc.determine_status(40, True) == SignalStatus.REJECTED
