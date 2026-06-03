"""
pipeline/schemas.py
Shared Pydantic v2 schemas for the entire multi-agent trading system.
Every agent speaks this language. Nothing moves without validation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from agents.news.news_schemas import NewsFeatures


# ─── Enums ────────────────────────────────────────────────────────────────────


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class MarketRegime(str, Enum):
    TREND_UP = "trend_up"
    TREND_DOWN = "trend_down"
    RANGING = "ranging"
    BREAKOUT = "breakout"
    REVERSAL = "reversal"
    CHOP = "chop"


class TradeAction(str, Enum):
    ENTER_LONG = "enter_long"
    ENTER_SHORT = "enter_short"
    WAIT = "wait"
    SCALE_IN = "scale_in"
    PARTIAL_EXIT = "partial_exit"
    TRAIL_STOP = "trail_stop"
    EXIT = "exit"
    DO_NOTHING = "do_nothing"


class ExitReason(str, Enum):
    TARGET = "target"
    STOP = "stop"
    MANUAL = "manual"
    TIMEOUT = "timeout"
    RISK = "risk_engine_veto"


class AgentStatus(str, Enum):
    OK = "ok"
    SKIPPED = "skipped"
    ERROR = "error"


# ─── Core Market Data ──────────────────────────────────────────────────────────


class OHLCV(BaseModel):
    symbol: str
    timeframe: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None


class SwingPoint(BaseModel):
    timestamp: datetime
    price: float
    kind: str
    confirmed: bool = False


class ChartStructure(BaseModel):
    symbol: str
    timeframe: str
    timestamp: datetime
    trend_direction: Direction
    regime: MarketRegime
    swing_highs: list[SwingPoint] = Field(default_factory=list)
    swing_lows: list[SwingPoint] = Field(default_factory=list)
    support_levels: list[float] = Field(default_factory=list)
    resistance_levels: list[float] = Field(default_factory=list)
    session_high: Optional[float] = None
    session_low: Optional[float] = None
    opening_range_high: Optional[float] = None
    opening_range_low: Optional[float] = None
    vwap: Optional[float] = None
    atr_14: float = 0.0
    higher_highs: bool = False
    higher_lows: bool = False
    lower_highs: bool = False
    lower_lows: bool = False


# ─── Method Agent Outputs ──────────────────────────────────────────────────────


class MethodOutput(BaseModel):
    method: str
    status: AgentStatus = AgentStatus.OK
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    error_msg: Optional[str] = None
    features: dict[str, float | bool | str | None] = Field(default_factory=dict)


class Agent369Output(MethodOutput):
    method: str = "agent_369"
    nearest_369_level: Optional[float] = None
    distance_ticks: Optional[float] = None
    level_active: bool = False
    level_type: Optional[str] = None
    time_cycle_active: bool = False
    opposition_level: Optional[float] = None
    reflection_level: Optional[float] = None


class FibonacciOutput(MethodOutput):
    method: str = "fibonacci_spiral"
    nearest_level: Optional[str] = None
    nearest_price: Optional[float] = None
    distance_ticks: Optional[float] = None
    reversal_zone_active: bool = False
    extension_target: Optional[float] = None
    golden_spiral_level: Optional[float] = None


class GannOutput(MethodOutput):
    method: str = "gann"
    angle_1x1_distance: Optional[float] = None
    angle_2x1_distance: Optional[float] = None
    fan_support_active: bool = False
    fan_resist_active: bool = False
    time_cycle_active: bool = False
    sq9_level: Optional[float] = None
    module_status: str = "research_only"


class ElliottWaveOutput(MethodOutput):
    method: str = "elliott_wave"
    wave_1_prob: float = 0.0
    wave_2_prob: float = 0.0
    wave_3_prob: float = 0.0
    wave_4_prob: float = 0.0
    wave_5_prob: float = 0.0
    wave_a_prob: float = 0.0
    wave_b_prob: float = 0.0
    wave_c_prob: float = 0.0
    primary_state: str = "unknown"
    primary_confidence: float = 0.0
    wave_3_extension: bool = False
    wave_5_exhaustion: bool = False
    abc_correction: bool = False
    next_expected_state: str = "unknown"


class HarmonicOutput(MethodOutput):
    method: str = "harmonic"
    pattern_type: Optional[str] = None
    direction: Optional[int] = None
    xab_ratio: Optional[float] = None
    abc_ratio: Optional[float] = None
    bcd_ratio: Optional[float] = None
    completion_score: float = 0.0
    completion_zone: Optional[float] = None
    reversal_zone_active: bool = False


class CandlestickOutput(MethodOutput):
    method: str = "candlestick"
    pattern: Optional[str] = None
    body_to_range_ratio: float = 0.0
    upper_wick_ratio: float = 0.0
    lower_wick_ratio: float = 0.0
    close_location: float = 0.0
    open_close_direction: int = 0
    wick_rejection_score: float = 0.0
    indecision_score: float = 0.0
    momentum_score: float = 0.0
    exhaustion_score: float = 0.0
    reversal_probability: float = 0.0
    confirmation: bool = False


class FractalOutput(MethodOutput):
    method: str = "fractal"
    fractal_high_confirmed: bool = False
    fractal_low_confirmed: bool = False
    current_fractal_type: Optional[str] = None
    fractal_strength: float = 0.0
    chaos_score: float = 0.0


class MarkovOutput(MethodOutput):
    method: str = "markov_state"
    current_state: str = "unknown"
    next_state: str = "unknown"
    transition_probability: float = 0.0
    state_duration_bars: int = 0
    bullish_continuation_prob: float = 0.0
    bearish_continuation_prob: float = 0.0
    reversal_prob: float = 0.0


class MomentumOutput(MethodOutput):
    method: str = "momentum"
    momentum_score: float = 0.0
    acceleration_score: float = 0.0
    momentum_direction: int = 0
    divergence_detected: bool = False
    volume_shift_score: float = 0.0
    roc_5: float = 0.0
    roc_10: float = 0.0


class StrategyMathOutput(MethodOutput):
    method: str = "strategy_math"
    expected_value: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    risk_of_ruin: float = 0.0
    r_multiple_avg: float = 0.0
    win_rate: float = 0.0
    sample_size: int = 0
    kelly_fraction: float = 0.0
    max_drawdown_pct: float = 0.0


class MonteCarloOutput(MethodOutput):
    method: str = "monte_carlo"
    median_outcome: float = 0.0
    p10_outcome: float = 0.0
    p90_outcome: float = 0.0
    ruin_probability: float = 0.0
    target_hit_prob: float = 0.0
    stop_hit_prob: float = 0.0
    breakeven_prob: float = 0.0


class BalanceLineOutput(MethodOutput):
    method: str = "balance_line"
    balance_price: Optional[float] = None
    above_balance: bool = False
    at_balance: bool = False
    distance_to_balance: float = 0.0
    balance_slope: float = 0.0


class AncientNumberOutput(MethodOutput):
    method: str = "ancient_number"
    number_zone: Optional[str] = None
    mirror_target: Optional[float] = None
    active_cycles: list[str] = Field(default_factory=list)
    level_active: bool = False


# ─── News Intelligence (MarketNewsAgent) ─────────────────────────────────────


class NewsIntelligenceBlock(BaseModel):
    news_sentiment_score: float = 0.0
    news_impact_score: float = 0.0
    news_urgency_score: float = 0.0
    volatility_risk_score: float = 0.0
    minutes_since_last_news: float = 9999.0
    minutes_until_next_event: float = 9999.0
    high_impact_news_active: bool = False
    breaking_news_active: bool = False
    affected_symbol_match: bool = False
    news_conflict_score: float = 0.0
    news_trading_blocked: bool = False
    news_reduce_size: bool = False
    news_manual_required: bool = False
    news_risk_reason: str = ""

    @classmethod
    def from_news_features(cls, news: NewsFeatures) -> "NewsIntelligenceBlock":
        return cls(
            news_sentiment_score=news.news_sentiment_score,
            news_impact_score=news.news_impact_score,
            news_urgency_score=news.news_urgency_score,
            volatility_risk_score=news.volatility_risk_score,
            minutes_since_last_news=news.minutes_since_last_news,
            minutes_until_next_event=news.minutes_until_next_event,
            high_impact_news_active=news.high_impact_news_active,
            breaking_news_active=news.breaking_news_active,
            affected_symbol_match=news.affected_symbol_match,
            news_conflict_score=news.news_conflict_score,
            news_trading_blocked=news.trading_blocked,
            news_reduce_size=news.reduce_size_recommended,
            news_manual_required=news.manual_approval_required,
            news_risk_reason=news.news_risk_reason or "",
        )


# ─── Feature Fusion ────────────────────────────────────────────────────────────


class FusedFeatureSet(NewsIntelligenceBlock):
    """The single structured feature set fed to the ML model."""

    symbol: str
    timeframe: str
    timestamp: datetime

    near_369_level: bool = False
    level_369_distance_ticks: float = 0.0
    time_cycle_369_active: bool = False

    near_618_fib: bool = False
    near_786_fib: bool = False
    near_500_fib: bool = False
    fib_distance_ticks: float = 0.0
    fib_reversal_zone: bool = False

    bullish_rejection_candle: bool = False
    bearish_rejection_candle: bool = False
    wick_rejection_score: float = 0.0
    indecision_score: float = 0.0
    candle_exhaustion_score: float = 0.0
    candle_reversal_prob: float = 0.0
    body_to_range_ratio: float = 0.0
    close_location: float = 0.0

    fractal_down_confirmed: bool = False
    fractal_up_confirmed: bool = False
    fractal_strength: float = 0.0
    chaos_score: float = 0.0

    elliott_state: str = "unknown"
    elliott_confidence: float = 0.0
    wave_3_extension: bool = False
    wave_5_exhaustion: bool = False
    abc_correction: bool = False

    harmonic_pattern_active: bool = False
    harmonic_pattern_type: Optional[str] = None
    harmonic_completion_score: float = 0.0
    harmonic_reversal_zone: bool = False

    gann_angle_support: bool = False
    gann_confluence_score: float = 0.0

    markov_current_state: str = "unknown"
    markov_next_state: str = "unknown"
    markov_continuation_probability: float = 0.0
    markov_reversal_probability: float = 0.0

    momentum_score: float = 0.0
    acceleration_score: float = 0.0
    volume_shift_score: float = 0.0
    divergence_detected: bool = False

    strategy_ev: float = 0.0
    risk_of_ruin: float = 0.0
    sample_size: int = 0
    win_rate: float = 0.0
    kelly_fraction: float = 0.0

    mc_target_hit_prob: float = 0.0
    mc_stop_hit_prob: float = 0.0
    mc_ruin_prob: float = 0.0

    above_balance_line: bool = False
    balance_distance: float = 0.0

    trend_direction: str = "flat"
    regime: str = "chop"
    atr_14: float = 0.0

    signal_rank: int = Field(0, ge=0, le=100)

    @classmethod
    def from_fused_features(cls, fused) -> "FusedFeatureSet":
        """Map agents.schemas.FusedFeatures → pipeline FusedFeatureSet."""
        f = fused.features

        def g(key: str, default: Any = 0.0):
            return f.get(key, default)

        def gb(key: str) -> bool:
            return bool(f.get(key, False))

        trend = str(f.get("trend_direction", "flat"))
        trend_map = {"up": "long", "down": "short", "unknown": "flat"}
        trend = trend_map.get(trend, trend if trend in ("long", "short", "flat") else "flat")

        news_block = NewsIntelligenceBlock.from_news_features(fused.news)

        return cls(
            symbol=fused.symbol,
            timeframe=fused.timeframe,
            timestamp=fused.timestamp,
            signal_rank=fused.signal_rank,
            **news_block.model_dump(),
            near_369_level=gb("level_369_reversal_zone_active") or gb("ancient_number_number_zone"),
            near_618_fib=gb("near_618_fib") or gb("fibonacci_spiral_near_618_fib"),
            fib_reversal_zone=gb("fibonacci_spiral_reversal_zone_active"),
            bullish_rejection_candle=gb("bullish_rejection_candle") or gb("candlestick_bullish_rejection_candle"),
            bearish_rejection_candle=gb("candlestick_bearish_rejection_candle"),
            wick_rejection_score=float(g("candlestick_wick_rejection_score", 0)),
            indecision_score=float(g("candlestick_indecision_score", 0)),
            candle_exhaustion_score=float(g("candlestick_exhaustion_score", 0)),
            candle_reversal_prob=float(g("candlestick_reversal_probability", 0)),
            body_to_range_ratio=float(g("candlestick_body_to_range_ratio", 0)),
            close_location=float(g("candlestick_close_location", 0)),
            fractal_down_confirmed=gb("fractal_down_confirmed") or gb("fractal_fractal_down_confirmed"),
            fractal_up_confirmed=gb("fractal_fractal_up_confirmed"),
            fractal_strength=float(g("fractal_fractal_strength", 0)),
            chaos_score=float(g("fractal_chaos_score", 0)),
            elliott_confidence=float(g("elliott_wave_primary_confidence", 0)),
            wave_3_extension=gb("elliott_wave_wave_3_extension"),
            harmonic_pattern_active=gb("harmonic_reversal_zone_active"),
            harmonic_pattern_type=f.get("harmonic_pattern_type"),
            harmonic_completion_score=float(g("harmonic_pattern_completion_score", 0)),
            harmonic_reversal_zone=gb("harmonic_reversal_zone_active"),
            gann_angle_support=gb("gann_angle_support") or gb("gann_gann_angle_support"),
            gann_confluence_score=min(0.05, float(g("gann_confidence", 0))),
            markov_current_state=str(g("markov_state_current_state", "unknown")),
            markov_next_state=str(g("markov_state_next_state", "unknown")),
            markov_continuation_probability=float(
                g("markov_continuation_probability", g("markov_state_markov_continuation_probability", 0))
            ),
            markov_reversal_probability=float(g("markov_state_markov_reversal_probability", 0)),
            momentum_score=float(g("momentum_score", g("momentum_momentum_score", 0))),
            acceleration_score=float(g("acceleration_score", g("momentum_acceleration_score", 0))),
            volume_shift_score=float(g("volume_shift_score", g("momentum_volume_shift_score", 0))),
            divergence_detected=gb("momentum_divergence_detected"),
            strategy_ev=float(g("strategy_ev", g("strategy_math_strategy_ev", 0))),
            risk_of_ruin=float(g("risk_of_ruin", g("strategy_math_risk_of_ruin", 0))),
            sample_size=int(g("strategy_math_sample_size", 0)),
            win_rate=float(g("strategy_math_win_rate", 0)),
            kelly_fraction=float(g("strategy_math_kelly_fraction", 0)),
            mc_target_hit_prob=float(g("monte_carlo_target_hit_prob", 0)),
            mc_stop_hit_prob=float(g("monte_carlo_stop_hit_prob", 0)),
            mc_ruin_prob=float(g("monte_carlo_ruin_probability", 0)),
            trend_direction=trend,
            atr_14=float(g("atr_14", 0)),
        )


# ─── Prediction ───────────────────────────────────────────────────────────────


class PredictionOutput(BaseModel):
    symbol: str
    timeframe: str
    timestamp: datetime
    trade_start_probability: float = 0.0
    trade_stop_probability: float = 0.0
    wait_probability: float = 0.0
    avoid_probability: float = 0.0
    target_hit_probability: float = 0.0
    reversal_probability: float = 0.0
    continuation_probability: float = 0.0
    chop_probability: float = 0.0
    expected_value: float = 0.0
    expected_drawdown: float = 0.0
    model_version: str = "lgbm_v1"
    model_confidence: float = 0.0

    @classmethod
    def from_agent(cls, pred, symbol: str, timeframe: str, ts: datetime) -> "PredictionOutput":
        return cls(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=ts,
            trade_start_probability=pred.target_before_stop_probability,
            trade_stop_probability=1.0 - pred.target_before_stop_probability,
            wait_probability=1.0 if pred.should_wait else 0.0,
            avoid_probability=1.0 if pred.should_avoid else 0.0,
            target_hit_probability=pred.target_before_stop_probability,
            reversal_probability=pred.reversal_probability,
            continuation_probability=pred.continuation_probability,
            expected_value=pred.expected_value,
            expected_drawdown=pred.expected_drawdown,
            model_version=pred.model_version,
            model_confidence=pred.model_confidence,
        )


# ─── Trade Plan ───────────────────────────────────────────────────────────────


class TradePlan(BaseModel):
    symbol: str
    timeframe: str
    timestamp: datetime
    action: TradeAction
    direction: Optional[Direction] = None
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    stop_limit_price: Optional[float] = None
    scale_in_price: Optional[float] = None
    wait_condition: Optional[str] = None
    exit_condition: Optional[str] = None
    mcts_iterations: int = 0
    plan_confidence: float = 0.0
    plan_ev: float = 0.0
    plan_notes: str = ""

    @classmethod
    def from_agent(cls, plan, symbol: str, timeframe: str, ts: datetime) -> "TradePlan":
        from agents.schemas import TradeAction as AgentTradeAction

        action_map = {
            AgentTradeAction.ENTER_LONG: TradeAction.ENTER_LONG,
            AgentTradeAction.ENTER_SHORT: TradeAction.ENTER_SHORT,
            AgentTradeAction.WAIT: TradeAction.WAIT,
            AgentTradeAction.SCALE_IN: TradeAction.SCALE_IN,
            AgentTradeAction.PARTIAL_PROFIT: TradeAction.PARTIAL_EXIT,
            AgentTradeAction.TRAIL_STOP: TradeAction.TRAIL_STOP,
            AgentTradeAction.EXIT: TradeAction.EXIT,
            AgentTradeAction.DO_NOTHING: TradeAction.DO_NOTHING,
        }
        direction = None
        if plan.action == AgentTradeAction.ENTER_LONG:
            direction = Direction.LONG
        elif plan.action == AgentTradeAction.ENTER_SHORT:
            direction = Direction.SHORT

        return cls(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=ts,
            action=action_map.get(plan.action, TradeAction.WAIT),
            direction=direction,
            entry_price=plan.entry_price,
            stop_loss=plan.stop_loss,
            take_profit=plan.take_profit,
            stop_limit_price=plan.stop_limit,
            wait_condition=plan.wait_condition,
            exit_condition=plan.exit_condition,
            plan_notes=plan.mcts_path[0] if plan.mcts_path else "",
        )


# ─── Risk Decision ────────────────────────────────────────────────────────────


class RiskDecision(BaseModel):
    approved: bool
    symbol: str
    timestamp: datetime
    position_size_contracts: int = 0
    max_notional_usd: float = 0.0
    stop_loss_ticks: int = 0
    take_profit_ticks: int = 0
    risk_reward_ratio: float = 0.0
    rejection_reasons: list[str] = Field(default_factory=list)
    daily_loss_remaining_pct: float = 0.0
    drawdown_current_pct: float = 0.0
    consecutive_losses: int = 0
    kill_switch_active: bool = False

    @classmethod
    def from_verdict(cls, verdict, symbol: str, ts: datetime) -> "RiskDecision":
        reasons = list(verdict.checks_failed)
        if verdict.reason and verdict.reason not in reasons:
            reasons.insert(0, verdict.reason)
        return cls(
            approved=verdict.approved,
            symbol=symbol,
            timestamp=ts,
            rejection_reasons=reasons,
            position_size_contracts=int(verdict.max_position_size),
        )


# ─── Execution ────────────────────────────────────────────────────────────────


class OrderResult(BaseModel):
    order_id: str
    symbol: str
    timestamp: datetime
    action: TradeAction
    direction: Optional[Direction] = None
    fill_price: Optional[float] = None
    fill_quantity: int = 0
    status: str = "pending"
    broker: str = "paper"
    error_msg: Optional[str] = None


# ─── Learning ─────────────────────────────────────────────────────────────────


class TradeOutcomeRow(BaseModel):
    trade_id: str
    symbol: str
    timeframe: str
    timestamp_entry: datetime
    timestamp_exit: datetime
    direction: str
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit: float
    pnl_dollars: float
    pnl_ticks: float
    r_multiple: float
    max_favorable_excursion: float
    max_adverse_excursion: float
    exit_reason: str
    signal_rank: int
    model_version: str
    feature_snapshot: dict
    methods_agreed: list[str]
    methods_disagreed: list[str]
    regime_at_entry: str
    ev_at_entry: float
    outcome_label: int


# ─── Audit ────────────────────────────────────────────────────────────────────


class AuditReport(BaseModel):
    symbol: str
    timeframe: str
    timestamp: datetime
    signal_rank: int
    action: str
    approved: bool
    explanation: str
    key_reasons: list[str] = Field(default_factory=list)
    disagreements: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    ev: float = 0.0
    sample_size: int = 0

    @classmethod
    def from_agent(
        cls,
        audit,
        symbol: str,
        timeframe: str,
        ts: datetime,
        signal_rank: int,
        action: str,
        approved: bool,
        fused: Optional[FusedFeatureSet],
        news_explanation: str = "",
    ) -> "AuditReport":
        explanation = audit.summary
        if news_explanation:
            explanation = f"{explanation} {news_explanation}".strip()
        return cls(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=ts,
            signal_rank=signal_rank,
            action=action,
            approved=approved,
            explanation=explanation,
            key_reasons=list(audit.reasons),
            disagreements=list(audit.disagreements),
            confidence=fused.markov_continuation_probability if fused else 0.0,
            ev=fused.strategy_ev if fused else 0.0,
            sample_size=fused.sample_size if fused else 0,
        )


# ─── Adapters ─────────────────────────────────────────────────────────────────


def decision_to_fused(decision) -> Optional[FusedFeatureSet]:
    if not decision.fused_features:
        return None
    return FusedFeatureSet.from_fused_features(decision.fused_features)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
