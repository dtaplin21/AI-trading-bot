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
