"""Always-on chart watcher — feeds TradingPipelineSupervisor on each completed bar."""

from chart_watcher.bar_assembler import (
    BarAssembler,
    DEFAULT_TIMEFRAMES,
    INTRADAY_TIMEFRAMES,
    MultiSymbolAssembler,
    TF_MINUTES,
    timeframe_to_seconds,
)
from chart_watcher.chart_watch_runner import ChartWatchRunner
from chart_watcher.session_scheduler import (
    CME_ALL,
    CME_ENERGY,
    CME_EQUITY_INDEX,
    CME_FX,
    CME_METALS,
    CME_TREASURIES,
    CRYPTO_SYMBOLS,
    SessionScheduler,
    WatcherMode,
)

__all__ = [
    "BarAssembler",
    "ChartWatchRunner",
    "DEFAULT_TIMEFRAMES",
    "INTRADAY_TIMEFRAMES",
    "MultiSymbolAssembler",
    "TF_MINUTES",
    "CME_ALL",
    "CME_ENERGY",
    "CME_EQUITY_INDEX",
    "CME_FX",
    "CME_METALS",
    "CME_TREASURIES",
    "CRYPTO_SYMBOLS",
    "SessionScheduler",
    "WatcherMode",
    "timeframe_to_seconds",
]
