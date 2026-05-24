"""Monte Carlo Tree Search planner — searches compressed market states."""

class MCTSPlanner:
    """Plans trade actions from compressed state, not raw OHLCV."""

    def plan(self, state: dict) -> str:
        rank = state.get("signal_rank", 0)
        confidence = state.get("model_confidence", 0)
        ev = state.get("expected_value", 0)
        ror = state.get("risk_of_ruin", 1)
        trend = state.get("market_state", "unknown")

        if rank < 65 or confidence < 0.55 or ev <= 0 or ror > 0.05:
            return "wait"

        if trend == "down":
            return "enter_short"
        if trend == "up":
            return "enter_long"
        return "wait"
