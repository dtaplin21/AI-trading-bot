"""
pipeline/confluence_agent.py

The Confluence Agent — the world state brain.

This is the most important agent in the system.
It receives outputs from all 13 method agents and produces
one structured ConfluenceReport that every downstream agent reads.

No method agent talks directly to the ML model.
No method agent talks directly to the MCTS planner.
Everything flows through this agent first.

What it does on every candle:
  1. Reads all method outputs (only proven methods vote)
  2. Computes weighted consensus direction
  3. Identifies agreeing and disagreeing clusters
  4. Checks news alignment vs technical picture
  5. Scores overall confluence strength
  6. Decides if the setup is ready for prediction
  7. Writes the ConfluenceReport

Mark Douglas principle embedded here:
  We count agreements. We score probabilities.
  We never output a trade command — only a confidence level.
  Every prediction is a guess. We state that in the output.
"""
from __future__ import annotations

import logging
from typing import Optional

from agents.news.news_schemas import NewsFeatures
from config.agent_config import METHOD_WEIGHTS, TRADING_PHILOSOPHY
from pipeline.confluence_report import ConfluenceReport, MethodCluster, MethodVote
from pipeline.schemas import (
    Agent369Output,
    AncientNumberOutput,
    BalanceLineOutput,
    CandlestickOutput,
    ChartStructure,
    ElliottWaveOutput,
    FibonacciOutput,
    FractalOutput,
    GannOutput,
    HarmonicOutput,
    MarkovOutput,
    MomentumOutput,
    MonteCarloOutput,
    StrategyMathOutput,
)
from validation.method_isolation.method_isolation_validator import MethodEdgeRegistry

logger = logging.getLogger(__name__)

# ─── Thresholds ───────────────────────────────────────────────────────────────
MIN_METHODS_FOR_SIGNAL = int(TRADING_PHILOSOPHY["confluence_minimum_methods"])
MIN_CONFLUENCE_SCORE = 0.35  # Below this = not worth scoring
CONFLICT_THRESHOLD = float(TRADING_PHILOSOPHY["max_conflict_score"])
GANN_MAX_WEIGHT = 0.02  # Hard cap regardless of config
STRATEGY_MIN_SAMPLE = 10
MONTE_CARLO_DIR_THRESHOLD = 0.05  # prob must exceed 0.5±this to vote direction


class ConfluenceAgent:
    """
    Aggregates all method outputs into one ConfluenceReport.

    Injected dependencies:
      - method_registry: MethodEdgeRegistry — knows which methods are proven
      - news_features:   NewsFeatures — current news context

    Called once per candle per symbol/timeframe by the TradingPipelineSupervisor.
    """

    def __init__(self, method_registry: Optional[MethodEdgeRegistry] = None) -> None:
        self._registry = method_registry or MethodEdgeRegistry()
        logger.info("ConfluenceAgent initialized")

    # ─── Main entry point ─────────────────────────────────────────────────────

    def analyze(
        self,
        chart: ChartStructure,
        news: NewsFeatures,
        # All 13 method outputs — None if agent not yet built
        candle: Optional[CandlestickOutput] = None,
        fibonacci: Optional[FibonacciOutput] = None,
        harmonic: Optional[HarmonicOutput] = None,
        elliott: Optional[ElliottWaveOutput] = None,
        gann: Optional[GannOutput] = None,
        agent_369: Optional[Agent369Output] = None,
        fractal: Optional[FractalOutput] = None,
        markov: Optional[MarkovOutput] = None,
        momentum: Optional[MomentumOutput] = None,
        strategy: Optional[StrategyMathOutput] = None,
        monte_carlo: Optional[MonteCarloOutput] = None,
        balance: Optional[BalanceLineOutput] = None,
        ancient_number: Optional[AncientNumberOutput] = None,
    ) -> ConfluenceReport:
        """
        Core method. Produces one ConfluenceReport from all inputs.
        Called on every candle. Fast — no I/O, pure computation.
        """
        symbol = chart.symbol
        timeframe = chart.timeframe
        regime = chart.regime.value

        # ── Step 1: Collect raw votes from each method ────────────────────────
        raw_votes = self._collect_votes(
            symbol,
            timeframe,
            regime,
            candle,
            fibonacci,
            harmonic,
            elliott,
            gann,
            agent_369,
            fractal,
            markov,
            momentum,
            strategy,
            monte_carlo,
            balance,
            ancient_number,
        )

        voted = [v for v in raw_votes if v is not None]
        excluded = self._get_excluded_methods(symbol, timeframe, regime, voted)

        # ── Step 2: Separate proven votes from excluded ───────────────────────
        proven_votes = [v for v in voted if v.is_proven]

        # ── Step 3: Count directions ──────────────────────────────────────────
        bullish = [v for v in proven_votes if v.direction == +1]
        bearish = [v for v in proven_votes if v.direction == -1]
        neutral = [v for v in proven_votes if v.direction == 0]

        # ── Step 4: Weighted consensus ────────────────────────────────────────
        total_weight = sum(v.weight for v in proven_votes)
        weighted_sum = sum(v.weighted_score for v in proven_votes)
        weighted_consensus = (weighted_sum / total_weight) if total_weight > 0 else 0.0
        weighted_consensus = max(-1.0, min(1.0, weighted_consensus))

        consensus_direction = (
            +1
            if weighted_consensus > 0.15
            else -1
            if weighted_consensus < -0.15
            else 0
        )

        # ── Step 5: Conflict score ─────────────────────────────────────────────
        conflict_score = self._compute_conflict(proven_votes, weighted_consensus)

        # ── Step 6: Identify clusters ──────────────────────────────────────────
        strongest, opposing = self._find_clusters(proven_votes)

        # ── Step 7: News vs technical ─────────────────────────────────────────
        news_sentiment = news.news_sentiment_score
        news_aligned = self._check_news_alignment(consensus_direction, news_sentiment)
        news_conflict = self._compute_news_conflict(consensus_direction, news_sentiment)

        # ── Step 8: Overall confluence score ──────────────────────────────────
        confluence_score = self._compute_confluence_score(
            proven_votes=proven_votes,
            weighted_consensus=weighted_consensus,
            conflict_score=conflict_score,
            news_conflict=news_conflict,
            news_blocked=news.trading_blocked,
        )

        # ── Step 9: Top signals for audit ─────────────────────────────────────
        top_signals = self._extract_top_signals(proven_votes, news)

        # ── Step 10: Readiness check ──────────────────────────────────────────
        min_methods_met = (
            len(bullish if consensus_direction == 1 else bearish) >= MIN_METHODS_FOR_SIGNAL
        )
        ready = (
            min_methods_met
            and confluence_score >= MIN_CONFLUENCE_SCORE
            and conflict_score <= CONFLICT_THRESHOLD
            and not news.trading_blocked
        )

        # ── Step 11: Probability statement ────────────────────────────────────
        prob_statement = self._build_probability_statement(
            proven_votes,
            confluence_score,
            consensus_direction,
            bullish,
            bearish,
            news_aligned,
        )

        report = ConfluenceReport(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=chart.timestamp,
            regime=regime,
            votes=proven_votes,
            excluded_methods=excluded,
            bullish_count=len(bullish),
            bearish_count=len(bearish),
            neutral_count=len(neutral),
            total_voting=len(proven_votes),
            weighted_consensus=round(weighted_consensus, 4),
            consensus_direction=consensus_direction,
            conflict_score=round(conflict_score, 4),
            strongest_cluster=strongest,
            opposing_cluster=opposing,
            news_sentiment_score=news_sentiment,
            news_aligned=news_aligned,
            news_conflict_score=news_conflict,
            news_trading_blocked=news.trading_blocked,
            news_risk_reason=news.news_risk_reason or "",
            confluence_score=round(confluence_score, 4),
            probability_statement=prob_statement,
            top_signals=top_signals,
            min_methods_met=min_methods_met,
            ready_for_prediction=ready,
        )

        logger.info("ConfluenceAgent: %s", report.summary())
        return report

    # ─── Vote collection ──────────────────────────────────────────────────────

    def _collect_votes(
        self,
        symbol: str,
        timeframe: str,
        regime: str,
        candle: Optional[CandlestickOutput],
        fibonacci: Optional[FibonacciOutput],
        harmonic: Optional[HarmonicOutput],
        elliott: Optional[ElliottWaveOutput],
        gann: Optional[GannOutput],
        agent_369: Optional[Agent369Output],
        fractal: Optional[FractalOutput],
        markov: Optional[MarkovOutput],
        momentum: Optional[MomentumOutput],
        strategy: Optional[StrategyMathOutput],
        monte_carlo: Optional[MonteCarloOutput],
        balance: Optional[BalanceLineOutput],
        ancient_number: Optional[AncientNumberOutput],
    ) -> list[Optional[MethodVote]]:
        """Convert each method output into a MethodVote."""
        return [
            self._vote_candlestick(candle, symbol, timeframe, regime),
            self._vote_fibonacci(fibonacci, symbol, timeframe, regime),
            self._vote_harmonic(harmonic, symbol, timeframe, regime),
            self._vote_elliott(elliott, symbol, timeframe, regime),
            self._vote_gann(gann, symbol, timeframe, regime),
            self._vote_369(agent_369, symbol, timeframe, regime),
            self._vote_fractal(fractal, symbol, timeframe, regime),
            self._vote_markov(markov, symbol, timeframe, regime),
            self._vote_momentum(momentum, symbol, timeframe, regime),
            self._vote_balance(balance, symbol, timeframe, regime),
            self._vote_ancient(ancient_number, symbol, timeframe, regime),
            self._vote_strategy(strategy, symbol, timeframe, regime),
            self._vote_monte_carlo(monte_carlo, symbol, timeframe, regime),
        ]

    def _make_vote(
        self,
        method_name: str,
        direction: int,
        confidence: float,
        key_feature: str,
        symbol: str,
        timeframe: str,
        regime: str,
    ) -> MethodVote:
        weight = METHOD_WEIGHTS.get(method_name, 0.02)
        if method_name == "gann":
            weight = min(weight, GANN_MAX_WEIGHT)
        is_proven = self._registry.is_approved(method_name, symbol, timeframe, regime)
        return MethodVote(
            method_name=method_name,
            direction=direction,
            confidence=round(confidence, 4),
            weight=weight,
            weighted_score=round(direction * confidence * weight, 6),
            key_feature=key_feature,
            is_proven=is_proven,
        )

    # ─── Per-method vote extractors ───────────────────────────────────────────

    def _vote_candlestick(
        self, m: Optional[CandlestickOutput], sym: str, tf: str, reg: str
    ) -> Optional[MethodVote]:
        if not m or m.status.value == "error":
            return None
        direction = m.open_close_direction
        confidence = (
            m.wick_rejection_score * 0.40
            + m.reversal_probability * 0.35
            + m.momentum_score * 0.25
        )
        feature = f"{m.pattern or 'candle'} wick={m.wick_rejection_score:.2f}"
        return self._make_vote("candlestick", direction, confidence, feature, sym, tf, reg)

    def _vote_fibonacci(
        self, m: Optional[FibonacciOutput], sym: str, tf: str, reg: str
    ) -> Optional[MethodVote]:
        if not m or m.status.value == "error":
            return None
        if not m.reversal_zone_active:
            return None
        # Fibonacci confirms zones — does not vote direction independently
        direction = 0
        confidence = m.confidence
        dist = m.distance_ticks if m.distance_ticks is not None else 0.0
        feature = f"fib {m.nearest_level} dist={dist:.1f}t"
        return self._make_vote("fibonacci", direction, confidence, feature, sym, tf, reg)

    def _vote_harmonic(
        self, m: Optional[HarmonicOutput], sym: str, tf: str, reg: str
    ) -> Optional[MethodVote]:
        if not m or m.status.value == "error":
            return None
        if not m.reversal_zone_active:
            return None
        direction = m.direction or 0
        confidence = m.completion_score * m.confidence
        feature = f"{m.pattern_type or 'harmonic'} complete={m.completion_score:.2f}"
        return self._make_vote("harmonic", direction, confidence, feature, sym, tf, reg)

    def _vote_elliott(
        self, m: Optional[ElliottWaveOutput], sym: str, tf: str, reg: str
    ) -> Optional[MethodVote]:
        if not m or m.status.value == "error":
            return None
        if m.primary_confidence < 0.60:
            return None  # Elliott rule: 60%+ or no vote
        bullish_states = {"wave_1", "wave_3", "wave_5", "wave_c_up"}
        bearish_states = {"wave_a_down", "wave_c_down", "wave_3_down"}
        state = m.primary_state.lower()
        direction = (
            +1
            if any(b in state for b in bullish_states)
            else -1
            if any(b in state for b in bearish_states)
            else 0
        )
        feature = f"elliott {m.primary_state} conf={m.primary_confidence:.2f}"
        return self._make_vote("elliott_wave", direction, m.primary_confidence, feature, sym, tf, reg)

    def _vote_gann(
        self, m: Optional[GannOutput], sym: str, tf: str, reg: str
    ) -> Optional[MethodVote]:
        if not m:
            return None
        # Gann only adds/subtracts tiny weight — never drives direction
        direction = 0
        confidence = min(0.30, m.confidence)
        angle = m.angle_1x1_distance if m.angle_1x1_distance is not None else 0.0
        feature = f"gann angle={angle:.1f} research_only"
        return self._make_vote("gann", direction, confidence, feature, sym, tf, reg)

    def _vote_369(
        self, m: Optional[Agent369Output], sym: str, tf: str, reg: str
    ) -> Optional[MethodVote]:
        if not m or not m.level_active:
            return None
        direction = 0  # 369 levels confirm zones, not direction alone
        confidence = m.confidence
        dist = m.distance_ticks if m.distance_ticks is not None else 0.0
        feature = f"369 level={m.nearest_369_level} dist={dist:.1f}t"
        return self._make_vote("agent_369", direction, confidence, feature, sym, tf, reg)

    def _vote_fractal(
        self, m: Optional[FractalOutput], sym: str, tf: str, reg: str
    ) -> Optional[MethodVote]:
        if not m or m.status.value == "error":
            return None
        if not (m.fractal_high_confirmed or m.fractal_low_confirmed):
            return None
        direction = -1 if m.fractal_high_confirmed else +1
        confidence = m.fractal_strength * m.confidence
        kind = "high" if m.fractal_high_confirmed else "low"
        feature = f"fractal {kind} str={m.fractal_strength:.2f}"
        return self._make_vote("fractal", direction, confidence, feature, sym, tf, reg)

    def _vote_markov(
        self, m: Optional[MarkovOutput], sym: str, tf: str, reg: str
    ) -> Optional[MethodVote]:
        if not m or m.status.value == "error":
            return None
        if m.bullish_continuation_prob > m.bearish_continuation_prob:
            direction = +1
            confidence = m.bullish_continuation_prob
        elif m.bearish_continuation_prob > m.bullish_continuation_prob:
            direction = -1
            confidence = m.bearish_continuation_prob
        else:
            direction = 0
            confidence = 0.0
        feature = f"markov {m.current_state}→{m.next_state} p={m.transition_probability:.2f}"
        return self._make_vote("markov", direction, confidence, feature, sym, tf, reg)

    def _vote_momentum(
        self, m: Optional[MomentumOutput], sym: str, tf: str, reg: str
    ) -> Optional[MethodVote]:
        if not m or m.status.value == "error":
            return None
        direction = m.momentum_direction
        confidence = (
            m.momentum_score * 0.50 + m.acceleration_score * 0.30 + m.volume_shift_score * 0.20
        )
        feature = (
            f"momentum={m.momentum_score:.2f} accel={m.acceleration_score:.2f} "
            f"vol={m.volume_shift_score:.2f}"
        )
        return self._make_vote("momentum", direction, confidence, feature, sym, tf, reg)

    def _vote_balance(
        self, m: Optional[BalanceLineOutput], sym: str, tf: str, reg: str
    ) -> Optional[MethodVote]:
        if not m or m.status.value == "error":
            return None
        if m.balance_price is None or m.at_balance:
            return None
        direction = +1 if m.above_balance else -1
        confidence = min(1.0, m.confidence + m.distance_to_balance * 0.01)
        feature = f"balance={m.balance_price:.2f} above={m.above_balance}"
        return self._make_vote("balance_line", direction, confidence, feature, sym, tf, reg)

    def _vote_ancient(
        self, m: Optional[AncientNumberOutput], sym: str, tf: str, reg: str
    ) -> Optional[MethodVote]:
        if not m or not m.level_active:
            return None
        direction = 0  # zone confirmer — cycles/666 levels confirm, not direct
        confidence = m.confidence
        zone = m.number_zone or "cycles"
        cycles = ",".join(m.active_cycles[:3]) if m.active_cycles else "none"
        feature = f"ancient zone={zone} cycles=[{cycles}]"
        return self._make_vote("ancient_number", direction, confidence, feature, sym, tf, reg)

    def _vote_strategy(
        self, m: Optional[StrategyMathOutput], sym: str, tf: str, reg: str
    ) -> Optional[MethodVote]:
        if not m or m.status.value == "error":
            return None
        if m.sample_size < STRATEGY_MIN_SAMPLE:
            return None
        ev = m.expected_value
        if abs(ev) < 0.01:
            return None
        direction = +1 if ev > 0 else -1
        confidence = min(1.0, m.win_rate * min(1.0, m.sample_size / 100.0))
        feature = f"EV={'+' if ev > 0 else ''}{ev:.2f} wr={m.win_rate:.2f} n={m.sample_size}"
        return self._make_vote("strategy_math", direction, confidence, feature, sym, tf, reg)

    def _vote_monte_carlo(
        self, m: Optional[MonteCarloOutput], sym: str, tf: str, reg: str
    ) -> Optional[MethodVote]:
        if not m or m.status.value == "error":
            return None
        prob = m.target_hit_prob
        if prob > 0.5 + MONTE_CARLO_DIR_THRESHOLD:
            direction = +1
        elif prob < 0.5 - MONTE_CARLO_DIR_THRESHOLD:
            direction = -1
        else:
            direction = 0
        confidence = abs(prob - 0.5) * 2.0
        if direction == 0 and confidence < 0.05:
            return None
        feature = f"mc prob={prob:.2f} target={m.target_hit_prob:.2f}"
        return self._make_vote("monte_carlo", direction, confidence, feature, sym, tf, reg)

    # ─── Scoring helpers ──────────────────────────────────────────────────────

    def _compute_conflict(self, votes: list[MethodVote], consensus: float) -> float:
        """
        Conflict = how spread out are the votes?
        0.0 = everyone agrees. 1.0 = total disagreement.
        """
        if not votes:
            return 0.0
        directional = [v for v in votes if v.direction != 0]
        if not directional:
            return 0.0
        bullish_w = sum(v.weighted_score for v in directional if v.direction == +1)
        bearish_w = sum(v.weighted_score for v in directional if v.direction == -1)
        total_w = abs(bullish_w) + abs(bearish_w)
        if total_w == 0:
            return 0.0
        minority_w = min(abs(bullish_w), abs(bearish_w))
        return round(minority_w / total_w, 4)

    def _find_clusters(
        self, votes: list[MethodVote]
    ) -> tuple[Optional[MethodCluster], Optional[MethodCluster]]:
        """Find the strongest agreeing cluster and the opposing cluster."""
        bull_votes = [v for v in votes if v.direction == +1]
        bear_votes = [v for v in votes if v.direction == -1]

        def make_cluster(vs: list[MethodVote], direction: int) -> Optional[MethodCluster]:
            if not vs:
                return None
            total_w = sum(v.weight for v in vs)
            avg_c = sum(v.confidence for v in vs) / len(vs)
            return MethodCluster(
                direction=direction,
                methods=[v.method_name for v in vs],
                avg_confidence=round(avg_c, 4),
                total_weight=round(total_w, 4),
                cluster_score=round(avg_c * total_w, 4),
            )

        bull_cluster = make_cluster(bull_votes, +1)
        bear_cluster = make_cluster(bear_votes, -1)

        if bull_cluster and bear_cluster:
            if bull_cluster.cluster_score >= bear_cluster.cluster_score:
                return bull_cluster, bear_cluster
            return bear_cluster, bull_cluster
        return bull_cluster or bear_cluster, None

    def _compute_confluence_score(
        self,
        proven_votes: list[MethodVote],
        weighted_consensus: float,
        conflict_score: float,
        news_conflict: float,
        news_blocked: bool,
    ) -> float:
        """
        Overall confluence score 0.0–1.0.
        High score = strong agreement, low conflict.
        Strategy/monte_carlo contribute via weighted votes — no separate boosts.
        """
        if not proven_votes:
            return 0.0

        # Base: strength of consensus
        base = abs(weighted_consensus) * 0.50

        # Boost: number of directional methods (capped)
        directional = [v for v in proven_votes if v.direction != 0]
        count_boost = min(0.20, len(directional) * 0.04)

        # Boost: zone confirmers active (fib, 369, ancient with dir=0)
        zone_confirmers = [
            v for v in proven_votes if v.direction == 0 and v.confidence >= 0.4
        ]
        zone_boost = min(0.10, len(zone_confirmers) * 0.03)

        # Penalty: conflict
        conflict_penalty = conflict_score * 0.30

        # Penalty: news conflict
        news_penalty = news_conflict * 0.20

        # Hard penalty: news block
        news_block_penalty = 0.50 if news_blocked else 0.0

        score = (
            base + count_boost + zone_boost
            - conflict_penalty - news_penalty - news_block_penalty
        )
        return max(0.0, min(1.0, round(score, 4)))

    def _check_news_alignment(self, direction: int, news_sentiment: float) -> bool:
        if direction == 0:
            return True
        if direction == +1:
            return news_sentiment >= -0.20
        if direction == -1:
            return news_sentiment <= +0.20
        return True

    def _compute_news_conflict(self, direction: int, news_sentiment: float) -> float:
        if direction == 0:
            return 0.0
        alignment = news_sentiment * direction
        if alignment >= 0:
            return 0.0
        return min(1.0, abs(alignment))

    def _get_excluded_methods(
        self,
        symbol: str,
        timeframe: str,
        regime: str,
        voted: list[MethodVote],
    ) -> list[str]:
        voted_names = {v.method_name for v in voted}
        all_methods = set(METHOD_WEIGHTS.keys())
        return [m for m in all_methods if m not in voted_names]

    def _extract_top_signals(
        self,
        votes: list[MethodVote],
        news: NewsFeatures,
    ) -> list[str]:
        """Top 3 most influential signals for audit explanation."""
        signals: list[str] = []
        sorted_votes = sorted(
            votes,
            key=lambda v: abs(v.weighted_score) if v.direction != 0 else v.confidence * v.weight,
            reverse=True,
        )
        for v in sorted_votes[:3]:
            signals.append(v.key_feature)
        if news.high_impact_news_active:
            headline = news.latest_headline[:40] if news.latest_headline else "high impact active"
            signals.append(f"news: {headline}")
        return signals[:5]

    def _build_probability_statement(
        self,
        votes: list[MethodVote],
        confluence: float,
        direction: int,
        bullish: list[MethodVote],
        bearish: list[MethodVote],
        news_aligned: bool,
    ) -> str:
        dir_str = "bullish" if direction == 1 else "bearish" if direction == -1 else "neutral"
        agree_ct = len(bullish) if direction == 1 else len(bearish)
        total_ct = len(votes)
        return (
            f"Confluence score {confluence:.2f} | {dir_str} | "
            f"{agree_ct} of {total_ct} proven methods agree | "
            f"news {'aligned' if news_aligned else 'conflicts'} | "
            f"Each prediction is a guess. "
            f"Risk of this edge not working always exists."
        )
