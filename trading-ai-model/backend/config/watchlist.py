"""Charts the pipeline actively watches (symbol + timeframe pairs)."""

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class WatchedChart:
    symbol: str
    timeframe: str
    label: str = ""


# Primary futures charts monitored by the trading supervisor
DEFAULT_WATCHLIST: tuple[WatchedChart, ...] = (
    WatchedChart("MES", "5m", "Micro E-mini S&P 500"),
    WatchedChart("ES", "5m", "E-mini S&P 500"),
    WatchedChart("NQ", "5m", "E-mini Nasdaq"),
    WatchedChart("MNQ", "5m", "Micro E-mini Nasdaq"),
)


def parse_watchlist(raw: str) -> list[WatchedChart]:
    """Parse 'MES:5m,ES:5m' env string into WatchedChart rows."""
    if not raw.strip():
        return list(DEFAULT_WATCHLIST)
    charts: list[WatchedChart] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            sym, tf = part.split(":", 1)
        else:
            sym, tf = part, "5m"
        charts.append(WatchedChart(sym.upper(), tf.strip(), ""))
    return charts or list(DEFAULT_WATCHLIST)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
