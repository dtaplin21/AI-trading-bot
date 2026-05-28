"""
pipeline/probability_gate.py

Unified probability gate — all four conditions must pass before planning.

Target thresholds (architecture diagram):
  P(success) >= 0.62
  EV         >= +$5
  sample     >= 300
  rank       >= 70

Defaults fall back to TRADING_PHILOSOPHY; override via env for production.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from config.agent_config import TRADING_PHILOSOPHY


@dataclass
class GateResult:
    passed: bool
    failures: list[str] = field(default_factory=list)


class ProbabilityGate:
    """Single enforcement point for Layer-5 probability gates."""

    def __init__(self) -> None:
        self.p_min = float(
            os.getenv("PROBABILITY_GATE_P_MIN", str(TRADING_PHILOSOPHY["probability_minimum"]))
        )
        self.ev_min = float(os.getenv("PROBABILITY_GATE_EV_MIN", "0.0"))
        self.sample_min = int(
            os.getenv("PROBABILITY_GATE_SAMPLE_MIN", str(TRADING_PHILOSOPHY["sample_size_minimum"]))
        )
        self.rank_min = int(
            os.getenv("PROBABILITY_GATE_RANK_MIN", str(TRADING_PHILOSOPHY["signal_rank_minimum"]))
        )

    def check(
        self,
        p_success: float,
        ev_dollars: float,
        sample_size: int,
        signal_rank: int,
        context: str = "",
    ) -> GateResult:
        failures: list[str] = []
        prefix = f"{context}: " if context else ""

        if p_success < self.p_min:
            failures.append(f"{prefix}P(success) {p_success:.2f} < {self.p_min:.2f}")
        if ev_dollars < self.ev_min:
            failures.append(f"{prefix}EV ${ev_dollars:.2f} < ${self.ev_min:.2f}")
        if sample_size < self.sample_min:
            failures.append(f"{prefix}sample size {sample_size} < {self.sample_min}")
        if signal_rank < self.rank_min:
            failures.append(f"{prefix}signal rank {signal_rank} < {self.rank_min}")

        return GateResult(passed=len(failures) == 0, failures=failures)
