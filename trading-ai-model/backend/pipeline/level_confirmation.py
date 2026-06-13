"""Level-first confirmation — slim method layer that confirms DB level trades."""

from __future__ import annotations

import os
from dataclasses import dataclass

from agents.schemas import MethodOutput
from pipeline.confluence_report import ConfluenceReport
from pipeline.level_method_fusion import level_direction, method_agreement_score, min_method_agreement
from pipeline.level_setup import LevelSetup

# Registry keys (config/agents.yaml)
CONFIRM_METHOD_IDS: tuple[str, ...] = (
    "method_candlestick",
    "method_momentum",
    "method_markov",
    "method_monte_carlo",
    "method_harmonic",
    "method_elliott",
    "method_fractal",
)

# MethodOutput.method names with optional proximity filter at the touch
PROXIMITY_GATED_METHODS: frozenset[str] = frozenset(
    {"harmonic", "elliott_wave", "fractal"}
)

CORE_BAR_METHODS: frozenset[str] = frozenset({"candlestick", "momentum"})


def confirm_proximity_pct() -> float:
    return float(
        os.getenv(
            "LEVEL_CONFIRM_PROXIMITY_PCT",
            os.getenv("LEVEL_GATE_TOLERANCE_PCT", "0.15"),
        )
    )


def require_bar_confirm() -> bool:
    return os.getenv("LEVEL_REQUIRE_BAR_CONFIRM", "false").lower() in ("true", "1", "yes")


def regime_veto_confidence() -> float:
    return float(os.getenv("LEVEL_REGIME_VETO_CONFIDENCE", "0.65"))


def price_near_level(level_setup: LevelSetup, price: float) -> bool:
    if level_setup.level_price <= 0:
        return False
    dist_pct = abs(price - level_setup.level_price) / level_setup.level_price * 100.0
    return dist_pct <= confirm_proximity_pct()


def filter_confirm_method_outputs(
    outputs: list[MethodOutput],
    level_setup: LevelSetup,
    current_price: float,
) -> list[MethodOutput]:
    """Drop proximity-gated methods when price is not at the DB level."""
    if price_near_level(level_setup, current_price):
        return outputs
    kept: list[MethodOutput] = []
    for output in outputs:
        if output.method in PROXIMITY_GATED_METHODS:
            continue
        kept.append(output)
    return kept


def _vote_name(vote) -> str:
    return getattr(vote, "method_name", None) or getattr(vote, "method", "")


def regime_allows_level_reversal(confluence: ConfluenceReport, level_setup: LevelSetup) -> bool:
    """Veto fade when markov strongly favors continuation against the level side."""
    want = level_direction(level_setup)
    threshold = regime_veto_confidence()
    for vote in confluence.votes:
        if _vote_name(vote) != "markov_state":
            continue
        if vote.direction == 0:
            continue
        if vote.direction != want and vote.confidence >= threshold:
            return False
    return True


def core_bar_confirms(confluence: ConfluenceReport, level_setup: LevelSetup) -> bool:
    """At least one core bar method agrees with the level entry side."""
    want = level_direction(level_setup)
    for vote in confluence.votes:
        if _vote_name(vote) not in CORE_BAR_METHODS:
            continue
        if vote.direction == want and vote.confidence >= 0.35:
            return True
    return False


@dataclass
class LevelConfirmationResult:
    passed: bool
    agreement: float
    reason: str = ""


def evaluate_level_confirmation(
    confluence: ConfluenceReport,
    level_setup: LevelSetup,
) -> LevelConfirmationResult:
    agreement = method_agreement_score(confluence, level_setup)
    if agreement < min_method_agreement():
        return LevelConfirmationResult(
            passed=False,
            agreement=agreement,
            reason=f"method_agreement_{agreement:.2f}_below_{min_method_agreement():.2f}",
        )
    if not regime_allows_level_reversal(confluence, level_setup):
        return LevelConfirmationResult(
            passed=False,
            agreement=agreement,
            reason="regime_veto_markov_continuation",
        )
    if require_bar_confirm() and not core_bar_confirms(confluence, level_setup):
        return LevelConfirmationResult(
            passed=False,
            agreement=agreement,
            reason="missing_core_bar_confirm",
        )
    return LevelConfirmationResult(passed=True, agreement=agreement)
