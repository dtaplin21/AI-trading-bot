"""Method analysis agents — one agent per theory."""

from agents.method_agents.ancient_number_agent import AncientNumberAgent
from agents.method_agents.balance_line_agent import BalanceLineAgent
from agents.method_agents.candlestick_agent import CandlestickAgent
from agents.method_agents.elliott_wave_agent import ElliottWaveAgent
from agents.method_agents.fibonacci_agent import FibonacciAgent
from agents.method_agents.fractal_agent import FractalAgent
from agents.method_agents.gann_agent import GannAgent
from agents.method_agents.harmonic_agent import HarmonicAgent
from agents.method_agents.level_369_agent import Level369Agent
from agents.method_agents.markov_agent import MarkovAgent
from agents.method_agents.momentum_agent import MomentumAgent
from agents.method_agents.monte_carlo_agent import MonteCarloMethodAgent
from agents.method_agents.strategy_math_agent import StrategyMathAgent

ALL_METHOD_AGENTS = [
    Level369Agent(),
    FibonacciAgent(),
    AncientNumberAgent(),
    GannAgent(),
    ElliottWaveAgent(),
    HarmonicAgent(),
    CandlestickAgent(),
    FractalAgent(),
    BalanceLineAgent(),
    MomentumAgent(),
    MarkovAgent(),
    MonteCarloMethodAgent(),
    StrategyMathAgent(),
]

REQUIRED_METHODS = {agent.method_name for agent in ALL_METHOD_AGENTS}


def get_method_agents_from_registry(symbol: str = "") -> list:
    """
    Return method agents filtered by agents.yaml enabled state.
    Falls back to ALL_METHOD_AGENTS if registry fails.
    """
    try:
        from agents.registry import get_agent_registry

        reg = get_agent_registry(symbol=symbol)
        agents = reg.get_method_agents()
        if agents:
            return agents
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning(
            "Registry unavailable, using ALL_METHOD_AGENTS: %s", e
        )
    return ALL_METHOD_AGENTS


def get_confirm_method_agents_from_registry(symbol: str = "") -> list:
    """
    Return level-confirmation method agents (candlestick, momentum, markov, etc.).
    Falls back to a static confirm subset if registry fails.
    """
    try:
        from agents.registry import get_agent_registry

        reg = get_agent_registry(symbol=symbol)
        agents = reg.get_confirm_method_agents()
        if agents:
            return agents
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning(
            "Registry unavailable for confirm agents: %s", e
        )
    from agents.registry import CONFIRM_METHOD_IDS

    by_name = {a.method_name: a for a in ALL_METHOD_AGENTS}
    id_to_method = {
        "method_candlestick": "candlestick",
        "method_momentum": "momentum",
        "method_markov": "markov_state",
        "method_monte_carlo": "monte_carlo",
        "method_harmonic": "harmonic",
        "method_elliott": "elliott_wave",
        "method_fractal": "fractal",
    }
    fallback = []
    for aid in CONFIRM_METHOD_IDS:
        method = id_to_method.get(aid)
        if method and method in by_name:
            fallback.append(by_name[method])
    return fallback or ALL_METHOD_AGENTS[:6]
