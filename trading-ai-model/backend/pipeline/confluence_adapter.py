"""
pipeline/confluence_adapter.py

Maps PipelineContext (agent-layer outputs) into typed pipeline schemas
for ConfluenceAgent.analyze().
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from agents.news.news_schemas import NewsFeatures
from agents.pipeline_context import PipelineContext
from agents.schemas import MethodOutput
from pipeline.schemas import (
    Agent369Output,
    AgentStatus,
    AncientNumberOutput,
    BalanceLineOutput,
    CandlestickOutput,
    ChartStructure,
    Direction,
    ElliottWaveOutput,
    FibonacciOutput,
    FractalOutput,
    GannOutput,
    HarmonicOutput,
    MarkovOutput,
    MarketRegime,
    MethodOutput as PipelineMethodOutput,
    MomentumOutput,
    MonteCarloOutput,
    StrategyMathOutput,
)


def _status(output: MethodOutput) -> AgentStatus:
    if output.skipped:
        return AgentStatus.SKIPPED
    return AgentStatus.OK


def _f(output: MethodOutput, key: str, default: Any = None) -> Any:
    return output.features.get(key, default)


def _output_map(ctx: PipelineContext) -> dict[str, MethodOutput]:
    return {o.method: o for o in ctx.method_outputs}


def _infer_regime(trend: str) -> MarketRegime:
    mapping = {
        "up": MarketRegime.TREND_UP,
        "down": MarketRegime.TREND_DOWN,
        "range": MarketRegime.RANGING,
    }
    return mapping.get(trend, MarketRegime.CHOP)


def _infer_direction(trend: str) -> Direction:
    if trend == "up":
        return Direction.LONG
    if trend == "down":
        return Direction.SHORT
    return Direction.FLAT


def chart_from_context(ctx: PipelineContext) -> ChartStructure:
    chart = ctx.chart
    trend = chart.trend_direction if chart else "unknown"
    ts = ctx.timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ChartStructure(
        symbol=ctx.symbol,
        timeframe=ctx.timeframe,
        timestamp=ts,
        trend_direction=_infer_direction(trend),
        regime=_infer_regime(trend),
        support_levels=list(chart.support_levels) if chart else [],
        resistance_levels=list(chart.resistance_levels) if chart else [],
        session_high=chart.session_high if chart else None,
        session_low=chart.session_low if chart else None,
        higher_highs=bool(chart.higher_highs) if chart else False,
        higher_lows=bool(chart.higher_lows) if chart else False,
        lower_highs=bool(chart.lower_highs) if chart else False,
        lower_lows=bool(chart.lower_lows) if chart else False,
        atr_14=float(ctx.ohlcv["high"].sub(ctx.ohlcv["low"]).tail(14).mean())
        if ctx.ohlcv is not None and len(ctx.ohlcv)
        else 0.0,
    )


def _base(output: Optional[MethodOutput]) -> PipelineMethodOutput:
    if not output:
        return PipelineMethodOutput(method="unknown", status=AgentStatus.SKIPPED)
    return PipelineMethodOutput(
        method=output.method,
        status=_status(output),
        confidence=output.confidence,
        error_msg=output.skip_reason,
    )


def _base_dict(output: MethodOutput) -> dict:
    data = _base(output).model_dump()
    data.pop("method", None)
    return data


def candlestick_from(o: Optional[MethodOutput]) -> Optional[CandlestickOutput]:
    if not o or o.skipped:
        return None
    bullish = bool(_f(o, "bullish_rejection_candle"))
    direction = 1 if bullish else (-1 if _f(o, "wick_rejection_score", 0) > 0.6 and not bullish else 0)
    close_loc = float(_f(o, "close_location", 0.5) or 0.5)
    if direction == 0:
        direction = 1 if close_loc > 0.55 else (-1 if close_loc < 0.45 else 0)
    return CandlestickOutput(
        **_base_dict(o),
        pattern=_f(o, "pattern"),
        body_to_range_ratio=float(_f(o, "body_to_range_ratio", 0) or 0),
        wick_rejection_score=float(_f(o, "wick_rejection_score", 0) or 0),
        open_close_direction=direction,
        reversal_probability=float(_f(o, "reversal_probability", o.confidence) or o.confidence),
        momentum_score=float(_f(o, "momentum_score", 0) or 0),
        confirmation=bool(_f(o, "confirmation")),
    )


def fibonacci_from(o: Optional[MethodOutput]) -> Optional[FibonacciOutput]:
    if not o or o.skipped:
        return None
    return FibonacciOutput(
        **_base_dict(o),
        method="fibonacci_spiral",
        nearest_level=_f(o, "nearest_level"),
        distance_ticks=_f(o, "distance_ticks"),
        reversal_zone_active=bool(_f(o, "reversal_zone_active") or _f(o, "near_618_fib")),
    )


def harmonic_from(o: Optional[MethodOutput]) -> Optional[HarmonicOutput]:
    if not o or o.skipped:
        return None
    completion = float(o.confidence)
    return HarmonicOutput(
        **_base_dict(o),
        pattern_type=_f(o, "pattern"),
        direction=1 if completion > 0.55 else (-1 if completion < 0.45 else 0),
        completion_score=completion,
        completion_zone=_f(o, "completion_zone"),
        reversal_zone_active=bool(_f(o, "completion_zone") or completion >= 0.55),
    )


def elliott_from(o: Optional[MethodOutput]) -> Optional[ElliottWaveOutput]:
    if not o or o.skipped:
        return None
    state = str(_f(o, "elliott_state", "unknown"))
    return ElliottWaveOutput(
        **_base_dict(o),
        primary_state=state,
        primary_confidence=o.confidence if _f(o, "can_influence_signal_rank", True) else o.confidence * 0.5,
        wave_3_prob=float(_f(o, "wave_3_probability", 0) or 0),
        wave_5_prob=float(_f(o, "wave_5_probability", 0) or 0),
        abc_correction=bool(_f(o, "abc_correction_probability", 0)),
    )


def gann_from(o: Optional[MethodOutput]) -> Optional[GannOutput]:
    if not o or o.skipped:
        return None
    return GannOutput(
        **_base_dict(o),
        angle_1x1_distance=_f(o, "angle_1x1_distance"),
        fan_support_active=bool(_f(o, "fan_support_active")),
        module_status="research_only",
    )


def agent_369_from(o: Optional[MethodOutput]) -> Optional[Agent369Output]:
    if not o or o.skipped:
        return None
    return Agent369Output(
        **_base_dict(o),
        method="agent_369",
        nearest_369_level=_f(o, "nearest_369_level") or _f(o, "nearest_level"),
        distance_ticks=_f(o, "distance_ticks"),
        level_active=bool(_f(o, "level_active") or _f(o, "near_369_level") or _f(o, "reversal_zone_active")),
        level_type=_f(o, "level_type"),
    )


def fractal_from(o: Optional[MethodOutput]) -> Optional[FractalOutput]:
    if not o or o.skipped:
        return None
    return FractalOutput(
        **_base_dict(o),
        fractal_high_confirmed=bool(_f(o, "fractal_high_confirmed") or _f(o, "fractal_down_confirmed")),
        fractal_low_confirmed=bool(_f(o, "fractal_low_confirmed") or _f(o, "fractal_up_confirmed")),
        fractal_strength=float(_f(o, "fractal_strength", o.confidence) or o.confidence),
    )


def markov_from(o: Optional[MethodOutput]) -> Optional[MarkovOutput]:
    if not o or o.skipped:
        return None
    cont = float(_f(o, "markov_continuation_probability", 0.5) or 0.5)
    state = str(_f(o, "current_state", "unknown"))
    bullish = cont if "up" in state or "bull" in state else 1 - cont
    bearish = 1 - bullish
    return MarkovOutput(
        **_base_dict(o),
        method="markov_state",
        current_state=state,
        next_state=str(_f(o, "next_state", "unknown")),
        transition_probability=float(_f(o, "transition_probability", cont) or cont),
        bullish_continuation_prob=bullish,
        bearish_continuation_prob=bearish,
    )


def momentum_from(o: Optional[MethodOutput]) -> Optional[MomentumOutput]:
    if not o or o.skipped:
        return None
    score = float(_f(o, "momentum_score", 0) or 0)
    direction = int(_f(o, "momentum_direction", 0) or 0)
    if direction == 0:
        direction = 1 if score > 0.55 else (-1 if score < 0.45 else 0)
    return MomentumOutput(
        **_base_dict(o),
        momentum_score=score,
        acceleration_score=float(_f(o, "acceleration_score", 0) or 0),
        momentum_direction=direction,
        volume_shift_score=float(_f(o, "volume_shift_score", 0) or 0),
    )


def strategy_from(o: Optional[MethodOutput]) -> Optional[StrategyMathOutput]:
    if not o or o.skipped:
        return None
    return StrategyMathOutput(
        **_base_dict(o),
        expected_value=float(_f(o, "strategy_ev", 0) or 0),
        risk_of_ruin=float(_f(o, "risk_of_ruin", 0) or 0),
        win_rate=float(_f(o, "win_rate", 0) or 0),
        sample_size=int(_f(o, "historical_sample_size", 0) or 0),
    )


def monte_carlo_from(o: Optional[MethodOutput]) -> Optional[MonteCarloOutput]:
    if not o or o.skipped:
        return None
    prob = float(_f(o, "prob_positive_path", o.confidence) or o.confidence)
    return MonteCarloOutput(
        **_base_dict(o),
        target_hit_prob=prob,
        stop_hit_prob=max(0.0, 1 - prob),
        breakeven_prob=0.5,
    )


def balance_from(o: Optional[MethodOutput]) -> Optional[BalanceLineOutput]:
    if not o or o.skipped:
        return None
    balance_price = _f(o, "balance_price") or _f(o, "balance_line")
    if balance_price is None:
        return None
    at_balance = bool(_f(o, "at_balance_line"))
    above_balance = bool(_f(o, "above_balance"))
    distance = float(_f(o, "distance_to_balance", 0) or 0)
    return BalanceLineOutput(
        **_base_dict(o),
        balance_price=float(balance_price),
        above_balance=above_balance,
        at_balance=at_balance,
        distance_to_balance=distance,
    )


def ancient_number_from(o: Optional[MethodOutput]) -> Optional[AncientNumberOutput]:
    if not o or o.skipped:
        return None
    cycles = _f(o, "active_cycles") or []
    if not isinstance(cycles, list):
        cycles = [str(cycles)] if cycles else []
    number_zone = _f(o, "number_zone")
    level_active = bool(cycles) or number_zone is not None
    mirror = _f(o, "mirror_target")
    return AncientNumberOutput(
        **_base_dict(o),
        number_zone=str(number_zone) if number_zone else None,
        mirror_target=float(mirror) if mirror is not None else None,
        active_cycles=[str(c) for c in cycles],
        level_active=level_active,
    )


def prepare_confluence_inputs(
    ctx: PipelineContext,
    news: NewsFeatures,
) -> dict:
    """Build kwargs for ConfluenceAgent.analyze() from pipeline context."""
    outputs = _output_map(ctx)
    return {
        "chart": chart_from_context(ctx),
        "news": news,
        "candle": candlestick_from(outputs.get("candlestick")),
        "fibonacci": fibonacci_from(outputs.get("fibonacci_spiral")),
        "harmonic": harmonic_from(outputs.get("harmonic")),
        "elliott": elliott_from(outputs.get("elliott_wave")),
        "gann": gann_from(outputs.get("gann")),
        "agent_369": agent_369_from(outputs.get("level_369")),
        "fractal": fractal_from(outputs.get("fractal")),
        "markov": markov_from(outputs.get("markov_state")),
        "momentum": momentum_from(outputs.get("momentum")),
        "strategy": strategy_from(outputs.get("strategy_math")),
        "monte_carlo": monte_carlo_from(outputs.get("monte_carlo")),
        "balance": balance_from(outputs.get("balance_line")),
        "ancient_number": ancient_number_from(outputs.get("ancient_number")),
    }
