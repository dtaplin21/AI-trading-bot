"""Import replay-compatible OHLCV CSV files into TimescaleDB."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from data.storage.timescale_store import TimescaleStore

logger = logging.getLogger(__name__)

OHLCV_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")
OHLCV_CSV_STEM_RE = re.compile(r"^(.+)_(1m|5m|15m|1h|1d)$", re.IGNORECASE)
DEFAULT_BATCH_ROWS = 10_000


def parse_ohlcv_csv_stem(stem: str) -> tuple[str, str] | None:
    """Parse ``MES_1m`` → (MES, 1m). Returns None if the name is not OHLCV CSV format."""
    match = OHLCV_CSV_STEM_RE.match(stem)
    if not match:
        return None
    return match.group(1).upper(), match.group(2).lower()


def discover_ohlcv_csvs(
    data_dir: Path,
    *,
    symbols: set[str] | None = None,
    timeframe: str | None = None,
) -> list[tuple[Path, str, str]]:
    """Return sorted (path, symbol, timeframe) for CSV files under data_dir."""
    found: list[tuple[Path, str, str]] = []
    if not data_dir.is_dir():
        return found

    sym_filter = {s.upper() for s in symbols} if symbols else None
    tf_filter = timeframe.strip().lower() if timeframe else None

    for path in sorted(data_dir.glob("*.csv")):
        parsed = parse_ohlcv_csv_stem(path.stem)
        if parsed is None:
            continue
        symbol, tf = parsed
        if sym_filter is not None and symbol not in sym_filter:
            continue
        if tf_filter is not None and tf != tf_filter:
            continue
        found.append((path, symbol, tf))
    return found


def load_ohlcv_csv(path: Path) -> pd.DataFrame:
    """Load ``{SYMBOL}_{tf}.csv`` into a UTC-indexed OHLCV DataFrame."""
    df = pd.read_csv(path)
    if df.empty:
        return pd.DataFrame({col: pd.Series(dtype=float) for col in OHLCV_COLUMNS})

    ts_col = None
    for candidate in ("timestamp", "time", "date"):
        if candidate in df.columns:
            ts_col = candidate
            break
    if ts_col is None:
        ts_col = str(df.columns[0])

    df[ts_col] = pd.to_datetime(df[ts_col], utc=True)
    df = df.set_index(ts_col).sort_index()
    missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path.name}: missing columns {missing}")

    out = df.loc[:, list(OHLCV_COLUMNS)].astype(float)
    out = out.loc[~out.index.duplicated(keep="last")]
    return out


def import_ohlcv_csv(
    store: TimescaleStore | None,
    path: Path,
    symbol: str,
    timeframe: str,
    *,
    batch_rows: int = DEFAULT_BATCH_ROWS,
    dry_run: bool = False,
) -> int:
    """Upsert one CSV into ohlcv_candles. Returns rows written (or would-be written)."""
    df = load_ohlcv_csv(path)
    if df.empty:
        logger.warning("%s: empty file — skipping", path.name)
        return 0

    total = len(df)
    if dry_run or store is None:
        logger.info(
            "%s: dry-run — would upsert %d rows for %s %s",
            path.name,
            total,
            symbol,
            timeframe,
        )
        return total

    written = 0
    batch_rows = max(1, batch_rows)
    for start in range(0, total, batch_rows):
        chunk = df.iloc[start : start + batch_rows]
        written += store.upsert_ohlcv(symbol, timeframe, chunk)
    logger.info(
        "%s: upserted %d/%d rows for %s %s",
        path.name,
        written,
        total,
        symbol,
        timeframe,
    )
    return written
