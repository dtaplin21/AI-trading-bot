"""Agent weights and trading philosophy constants for confluence scoring."""

METHOD_WEIGHTS: dict[str, float] = {
    "candlestick": 0.15,
    "fibonacci": 0.10,
    "harmonic": 0.15,
    "elliott_wave": 0.10,
    "gann": 0.02,
    "agent_369": 0.10,
    "fractal": 0.07,
    "markov": 0.12,
    "momentum": 0.10,
    "strategy_math": 0.08,
    "monte_carlo": 0.06,
    "balance_line": 0.05,
    "ancient_number": 0.05,
}

TRADING_PHILOSOPHY: dict[str, float | int | str] = {
    "confluence_minimum_methods": 3,
    "max_conflict_score": 0.45,
    "mark_douglas_principle": (
        "Every prediction is a guess. We count agreements and score probabilities — "
        "never output a trade command from confluence alone."
    ),
}

# Methods approved for voting by default (gann excluded until research validates)
DEFAULT_PROVEN_METHODS: frozenset[str] = frozenset(
    m for m in METHOD_WEIGHTS if m != "gann"
)

# Map runtime method agent names → confluence vote names
METHOD_NAME_ALIASES: dict[str, str] = {
    "fibonacci_spiral": "fibonacci",
    "level_369": "agent_369",
    "markov_state": "markov",
}
