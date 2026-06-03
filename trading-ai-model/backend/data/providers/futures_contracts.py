"""
CME futures contract windows for Polygon historical backfill.

Continuous tickers (C:MES) often return empty results; backfill uses explicit
quarterly/monthly contract codes (e.g. MESH25, MESM25) clipped to roll windows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from config.symbols import FUTURES_SYMBOLS, get_symbol_or_none
from data.providers.polygon_backfill import parse_date

# Approximate roll boundaries for 2025 (clip to job start/end).
_QUARTERLY_2025: list[tuple[str, str, str]] = [
    ("H25", "2025-01-01", "2025-03-14"),
    ("M25", "2025-03-15", "2025-06-14"),
    ("U25", "2025-06-15", "2025-09-14"),
    ("Z25", "2025-09-15", "2025-12-31"),
]

# GC uses Feb/Apr/Jun/Aug/Dec (G,J,M,Q,V,Z) — 2025 active windows (Apr contract = J25).
_GC_2025: list[tuple[str, str, str]] = [
    ("J25", "2025-01-01", "2025-04-14"),
    ("M25", "2025-04-15", "2025-06-14"),
    ("Q25", "2025-06-15", "2025-08-14"),
    ("Z25", "2025-08-15", "2025-12-31"),
]

_CL_MONTH_CODES_2025 = ("F", "G", "H", "J", "K", "M", "N", "Q", "U", "V", "X", "Z")
_CL_MONTH_ENDS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)

# logical symbol → list of (suffix, start, end) for a calendar year
_FUTURES_SCHEDULE_2025: dict[str, list[tuple[str, str, str]]] = {
    "MES": [(f"MES{s}", a, b) for s, a, b in _QUARTERLY_2025],
    "ES": [(f"ES{s}", a, b) for s, a, b in _QUARTERLY_2025],
    "NQ": [(f"NQ{s}", a, b) for s, a, b in _QUARTERLY_2025],
    "MNQ": [(f"MNQ{s}", a, b) for s, a, b in _QUARTERLY_2025],
    "RTY": [(f"RTY{s}", a, b) for s, a, b in _QUARTERLY_2025],
    "ZB": [(f"ZB{s}", a, b) for s, a, b in _QUARTERLY_2025],
    "GC": [(f"GC{s}", a, b) for s, a, b in _GC_2025],
    "CL": [
        (f"CL{code}25", f"2025-{m:02d}-01", f"2025-{m:02d}-{_CL_MONTH_ENDS[m - 1]:02d}")
        for m, code in enumerate(_CL_MONTH_CODES_2025, start=1)
    ],
}

SUPPORTED_FUTURES_BACKFILL_YEARS: frozenset[int] = frozenset({2025})


@dataclass(frozen=True)
class FuturesContractWindow:
    """One tradable contract and its active window within the backfill job."""

    logical_symbol: str
    contract_code: str
    polygon_ticker: str
    start: datetime
    end: datetime


def contract_to_polygon_ticker(contract_code: str) -> str:
    """Polygon aggregate ticker for a specific futures contract (not C:continuous)."""
    return contract_code.upper()


def uses_futures_contract_roll(symbol: str, year: int) -> bool:
    sym = symbol.upper()
    return (
        year in SUPPORTED_FUTURES_BACKFILL_YEARS
        and sym in FUTURES_SYMBOLS
        and sym in _schedule_for_year(year)
    )


def _schedule_for_year(year: int) -> dict[str, list[tuple[str, str, str]]]:
    if year == 2025:
        return _FUTURES_SCHEDULE_2025
    return {}


def get_contract_windows(
    symbol: str,
    year: int,
    job_start: datetime,
    job_end: datetime,
) -> list[FuturesContractWindow]:
    """
    Return contract windows for a futures symbol, clipped to [job_start, job_end].
    Empty if symbol is not futures or year has no schedule.
    """
    sym = symbol.upper()
    if get_symbol_or_none(sym) is None or sym not in FUTURES_SYMBOLS:
        return []

    raw = _schedule_for_year(year).get(sym, [])
    if not raw:
        return []

    job_start = job_start if job_start.tzinfo else job_start.replace(tzinfo=timezone.utc)
    job_end = job_end if job_end.tzinfo else job_end.replace(tzinfo=timezone.utc)

    windows: list[FuturesContractWindow] = []
    for contract_code, start_s, end_s in raw:
        w_start = parse_date(start_s)
        w_end = parse_date(end_s)
        w_end = w_end.replace(hour=23, minute=59, second=59)
        clip_start = max(w_start, job_start)
        clip_end = min(w_end, job_end)
        if clip_start > clip_end:
            continue
        windows.append(
            FuturesContractWindow(
                logical_symbol=sym,
                contract_code=contract_code,
                polygon_ticker=contract_to_polygon_ticker(contract_code),
                start=clip_start,
                end=clip_end,
            )
        )
    return windows


def infer_backfill_year(job_start: str, job_end: str) -> int:
    """Use job end year for contract calendar (e.g. 2025-01-01 → 2025-12-31 → 2025)."""
    return parse_date(job_end).year
