"""
CME futures contract windows for Polygon historical backfill.

Continuous tickers (C:MES) often return empty results; backfill uses explicit
quarterly/monthly contract codes (e.g. MESH25 internally) mapped to Massive
futures tickers (MESH5) on GET /futures/v1/aggs/{ticker}.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from config.symbols import FUTURES_SYMBOLS, get_symbol_or_none
from data.providers.polygon_backfill import parse_date

# Approximate roll boundaries (clip to job start/end).
_QUARTERLY_2024: list[tuple[str, str, str]] = [
    ("H24", "2024-01-01", "2024-03-14"),
    ("M24", "2024-03-15", "2024-06-14"),
    ("U24", "2024-06-15", "2024-09-14"),
    ("Z24", "2024-09-15", "2024-12-31"),
]

_QUARTERLY_2025: list[tuple[str, str, str]] = [
    ("H25", "2025-01-01", "2025-03-14"),
    ("M25", "2025-03-15", "2025-06-14"),
    ("U25", "2025-06-15", "2025-09-14"),
    ("Z25", "2025-09-15", "2025-12-31"),
]

_GC_2024: list[tuple[str, str, str]] = [
    ("J24", "2024-01-01", "2024-04-14"),
    ("M24", "2024-04-15", "2024-06-14"),
    ("Q24", "2024-06-15", "2024-08-14"),
    ("Z24", "2024-08-15", "2024-12-31"),
]

_GC_2025: list[tuple[str, str, str]] = [
    ("J25", "2025-01-01", "2025-04-14"),
    ("M25", "2025-04-15", "2025-06-14"),
    ("Q25", "2025-06-15", "2025-08-14"),
    ("Z25", "2025-08-15", "2025-12-31"),
]

_CL_MONTH_CODES = ("F", "G", "H", "J", "K", "M", "N", "Q", "U", "V", "X", "Z")
_CL_MONTH_ENDS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _quarterly_schedule(prefix: str, year: int) -> list[tuple[str, str, str]]:
    quarters = _QUARTERLY_2024 if year == 2024 else _QUARTERLY_2025
    yy = str(year)[-2:]
    return [(f"{prefix}{s}", a, b) for s, a, b in quarters]


def _gc_schedule(year: int) -> list[tuple[str, str, str]]:
    raw = _GC_2024 if year == 2024 else _GC_2025
    return [(f"GC{s}", a, b) for s, a, b in raw]


def _cl_schedule(year: int) -> list[tuple[str, str, str]]:
    yy = str(year)[-2:]
    return [
        (f"CL{code}{yy}", f"{year}-{m:02d}-01", f"{year}-{m:02d}-{_CL_MONTH_ENDS[m - 1]:02d}")
        for m, code in enumerate(_CL_MONTH_CODES, start=1)
    ]


def _schedule_for_year(year: int) -> dict[str, list[tuple[str, str, str]]]:
    if year not in SUPPORTED_FUTURES_BACKFILL_YEARS:
        return {}
    return {
        "MES": _quarterly_schedule("MES", year),
        "ES": _quarterly_schedule("ES", year),
        "NQ": _quarterly_schedule("NQ", year),
        "MNQ": _quarterly_schedule("MNQ", year),
        "RTY": _quarterly_schedule("RTY", year),
        "ZB": _quarterly_schedule("ZB", year),
        "GC": _gc_schedule(year),
        "CL": _cl_schedule(year),
    }


SUPPORTED_FUTURES_BACKFILL_YEARS: frozenset[int] = frozenset({2024, 2025})


@dataclass(frozen=True)
class FuturesContractWindow:
    """One tradable contract and its active window within the backfill job."""

    logical_symbol: str
    contract_code: str
    polygon_ticker: str
    start: datetime
    end: datetime


def contract_to_polygon_ticker(contract_code: str) -> str:
    """
    Massive futures aggs ticker for a specific contract (not C:continuous).

    Internal codes use a two-digit year suffix (MESH25); the futures API expects
    a single digit (MESH5). Example from docs: GCJ5 for April 2025 gold.
    """
    code = contract_code.upper()
    if len(code) >= 3 and code[-2:].isdigit():
        return f"{code[:-2]}{code[-1]}"
    return code


def job_years(job_start: datetime, job_end: datetime) -> list[int]:
    start_y = job_start.year
    end_y = job_end.year
    return [y for y in range(start_y, end_y + 1) if y in SUPPORTED_FUTURES_BACKFILL_YEARS]


def uses_futures_contract_roll(symbol: str, year: int) -> bool:
    sym = symbol.upper()
    return (
        year in SUPPORTED_FUTURES_BACKFILL_YEARS
        and sym in FUTURES_SYMBOLS
        and sym in _schedule_for_year(year)
    )


def uses_futures_contract_roll_for_job(symbol: str, job_start: datetime, job_end: datetime) -> bool:
    return any(uses_futures_contract_roll(symbol, year) for year in job_years(job_start, job_end))


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


def get_contract_windows_for_job(
    symbol: str,
    job_start: datetime,
    job_end: datetime,
) -> list[FuturesContractWindow]:
    """All contract windows across years in [job_start, job_end], sorted by start."""
    windows: list[FuturesContractWindow] = []
    for year in job_years(job_start, job_end):
        windows.extend(get_contract_windows(symbol, year, job_start, job_end))
    windows.sort(key=lambda w: w.start)
    return windows


def infer_backfill_year(job_start: str, job_end: str) -> int:
    """Use job end year for contract calendar (e.g. 2025-01-01 → 2025-12-31 → 2025)."""
    return parse_date(job_end).year
