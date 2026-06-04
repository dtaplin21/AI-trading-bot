#!/usr/bin/env python3
"""
Resumable Polygon OHLCV backfill into TimescaleDB (optional CSV export).

Progress is saved to data/backfill_checkpoint.json after each chunk.
Re-run the same command to continue after Ctrl+C, rate limits, or crashes.

Usage (from backend/):
  python scripts/backfill_polygon.py --timeframe 1m --start 2025-01-01 --end 2025-12-31
  python scripts/backfill_polygon.py --status
  python scripts/backfill_polygon.py --reset --timeframe 1m --start 2025-01-01 --end 2025-12-31

Env:
  POLYGON_API_KEY          required
  DATABASE_URL             required for DB upsert (unless --skip-db)
  BACKFILL_CHECKPOINT      default data/backfill_checkpoint.json
  BACKFILL_RATE_SLEEP      seconds on 429 (default 65)
  BACKFILL_CHUNK_DAYS      default 30
  BACKFILL_FUTURES_YEAR    contract calendar year (default: year of --end)
  WATCHER_SYMBOLS          default symbol list

Futures (MES, ES, …) use quarterly/monthly contract codes (MESH25, …) instead of C:MES.
Re-run futures only (keep EURUSD etc.): --redo-futures
Full restart: --reset
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

load_dotenv(_BACKEND / ".env")

from config.env_resolve import is_env_placeholder, resolve_env
from config.symbols import FUTURES_SYMBOLS
from config.watchlist import watcher_symbols_from_env
from data.providers.backfill_checkpoint import CheckpointManager
from data.providers.futures_contracts import (
    get_contract_windows,
    infer_backfill_year,
    uses_futures_contract_roll,
)
from data.providers.polygon_backfill import (
    PolygonBackfillClient,
    export_ohlcv_csv,
    iter_date_chunks,
    parse_date,
)
from data.storage.timescale_store import TimescaleStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("backfill_polygon")

DEFAULT_CHECKPOINT = _BACKEND / "data" / "backfill_checkpoint.json"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Resumable Polygon OHLCV backfill")
    p.add_argument(
        "--symbols",
        default=os.getenv("BACKFILL_SYMBOLS", os.getenv("WATCHER_SYMBOLS", "")),
        help="Comma-separated symbols (default: WATCHER_SYMBOLS)",
    )
    p.add_argument(
        "--start",
        default=os.getenv("BACKFILL_START", os.getenv("WATCHER_REPLAY_START", "2025-01-01")),
    )
    p.add_argument(
        "--end",
        default=os.getenv("BACKFILL_END", os.getenv("WATCHER_REPLAY_END", "2025-12-31")),
    )
    p.add_argument(
        "--timeframe",
        default=os.getenv("BACKFILL_TIMEFRAME", os.getenv("WATCHER_REPLAY_TIMEFRAME", "1m")),
    )
    p.add_argument(
        "--export-csv",
        action="store_true",
        default=os.getenv("BACKFILL_EXPORT_CSV", "false").lower() in ("true", "1", "yes"),
        help="Also write data/ohlcv/{SYMBOL}_{tf}.csv",
    )
    p.add_argument(
        "--chunk-days",
        type=int,
        default=int(os.getenv("BACKFILL_CHUNK_DAYS", "30")),
    )
    p.add_argument(
        "--data-path",
        default=os.getenv("WATCHER_DATA_PATH", "data/ohlcv"),
    )
    p.add_argument("--skip-db", action="store_true", help="CSV export only")
    p.add_argument(
        "--checkpoint",
        default=os.getenv("BACKFILL_CHECKPOINT", str(DEFAULT_CHECKPOINT)),
        help="Checkpoint JSON path",
    )
    p.add_argument("--reset", action="store_true", help="Ignore checkpoint and start fresh")
    p.add_argument(
        "--redo-futures",
        action="store_true",
        help="Reset futures symbols to pending (keeps forex/crypto/equity progress)",
    )
    p.add_argument(
        "--redo-futures-zero-only",
        action="store_true",
        help="With --redo-futures: only reset futures done with 0 bars_saved",
    )
    p.add_argument("--status", action="store_true", help="Show progress and exit")
    p.add_argument(
        "--futures-year",
        type=int,
        default=int(os.getenv("BACKFILL_FUTURES_YEAR", "0")),
        help="Futures contract calendar year (0 = infer from --end)",
    )
    return p.parse_args()


def _process_chunk(
    sym: str,
    *,
    client: PolygonBackfillClient,
    store: TimescaleStore | None,
    checkpoint: CheckpointManager,
    timeframe: str,
    chunk_start,
    chunk_end,
    polygon_ticker: str,
    contract_code: str | None,
    export_csv: bool,
    data_path: Path,
    sym_bars: int,
) -> tuple[bool, int]:
    chunk_end_str = chunk_end.strftime("%Y-%m-%d")
    label = f"{sym}/{contract_code}" if contract_code else sym
    try:
        df = client.fetch_chunk(
            sym,
            timeframe,
            chunk_start,
            chunk_end,
            ticker=polygon_ticker,
        )
    except Exception as exc:
        logger.exception(
            "%s: chunk %s → %s failed: %s",
            label,
            chunk_start.date(),
            chunk_end.date(),
            exc,
        )
        return False, sym_bars

    saved = 0
    if not df.empty:
        if store is not None:
            saved = store.upsert_ohlcv(sym, timeframe, df)
        if export_csv:
            csv_path = data_path / f"{sym}_{timeframe}.csv"
            export_ohlcv_csv(df, str(csv_path), append=csv_path.exists())
        sym_bars += saved if store else len(df)
        logger.info(
            "  %s: %s → %s | %d bars | symbol_total=%d",
            label,
            chunk_start.date(),
            chunk_end.date(),
            len(df),
            sym_bars,
        )
    else:
        detail = client.last_chunk_diagnostic or "see PolygonBackfill log above"
        logger.warning(
            "  %s: no bars %s → %s | %s",
            label,
            chunk_start.date(),
            chunk_end.date(),
            detail,
        )

    checkpoint.mark_chunk_done(
        sym,
        chunk_end_str,
        saved if store else len(df),
        last_contract=contract_code,
    )
    return True, sym_bars


def _run_futures_symbol(
    sym: str,
    *,
    client: PolygonBackfillClient,
    store: TimescaleStore | None,
    checkpoint: CheckpointManager,
    timeframe: str,
    job_start: datetime,
    job_end: datetime,
    futures_year: int,
    chunk_days: int,
    export_csv: bool,
    data_path: Path,
) -> tuple[bool, int]:
    resume, last_contract = checkpoint.get_resume_context(sym)
    if resume is None:
        logger.info("%s already complete — skipping", sym)
        return True, 0

    windows = get_contract_windows(sym, futures_year, job_start, job_end)
    if not windows:
        logger.warning("%s: no futures contract schedule for %s", sym, futures_year)
        return False, 0

    logger.info(
        "Backfilling %s using %d futures contracts (%s) from %s",
        sym,
        len(windows),
        futures_year,
        resume,
    )

    sym_bars = 0
    past_resume_contract = last_contract is None
    resume_dt = parse_date(resume) if resume else None
    for window in windows:
        if not past_resume_contract:
            if window.contract_code != last_contract:
                continue
            past_resume_contract = True

        contract_start = window.start
        if resume_dt is not None:
            contract_start = max(window.start, resume_dt)
            if contract_start > window.end:
                continue
            resume_dt = None

        for chunk_start, chunk_end in iter_date_chunks(
            contract_start, window.end, chunk_days
        ):
            ok, sym_bars = _process_chunk(
                sym,
                client=client,
                store=store,
                checkpoint=checkpoint,
                timeframe=timeframe,
                chunk_start=chunk_start,
                chunk_end=chunk_end,
                polygon_ticker=window.polygon_ticker,
                contract_code=window.contract_code,
                export_csv=export_csv,
                data_path=data_path,
                sym_bars=sym_bars,
            )
            if not ok:
                return False, sym_bars

    checkpoint.mark_symbol_done(sym)
    if sym_bars == 0:
        logger.warning(
            "%s: finished with 0 bars — run with --reset if this followed a C:MES backfill",
            sym,
        )
    return True, sym_bars


def _run_symbol(
    sym: str,
    *,
    client: PolygonBackfillClient,
    store: TimescaleStore | None,
    checkpoint: CheckpointManager,
    timeframe: str,
    job_start: datetime,
    job_end: datetime,
    end: str,
    futures_year: int,
    chunk_days: int,
    export_csv: bool,
    data_path: Path,
) -> tuple[bool, int]:
    """Backfill one symbol from checkpoint resume point. Returns (ok, bars_added)."""
    if uses_futures_contract_roll(sym, futures_year):
        return _run_futures_symbol(
            sym,
            client=client,
            store=store,
            checkpoint=checkpoint,
            timeframe=timeframe,
            job_start=job_start,
            job_end=job_end,
            futures_year=futures_year,
            chunk_days=chunk_days,
            export_csv=export_csv,
            data_path=data_path,
        )

    resume = checkpoint.get_resume_date(sym)
    if resume is None:
        logger.info("%s already complete — skipping", sym)
        return True, 0

    start_dt = parse_date(resume)
    end_dt = parse_date(end)
    ticker = client.resolve_ticker(sym)
    logger.info("Backfilling %s (%s) from %s → %s", sym, ticker, resume, end)

    sym_bars = 0
    for chunk_start, chunk_end in iter_date_chunks(start_dt, end_dt, chunk_days):
        ok, sym_bars = _process_chunk(
            sym,
            client=client,
            store=store,
            checkpoint=checkpoint,
            timeframe=timeframe,
            chunk_start=chunk_start,
            chunk_end=chunk_end,
            polygon_ticker=ticker,
            contract_code=None,
            export_csv=export_csv,
            data_path=data_path,
            sym_bars=sym_bars,
        )
        if not ok:
            return False, sym_bars

    checkpoint.mark_symbol_done(sym)
    return True, sym_bars


def main() -> int:
    args = _parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        symbols = watcher_symbols_from_env()

    checkpoint_path = Path(args.checkpoint)
    if not checkpoint_path.is_absolute():
        checkpoint_path = _BACKEND / checkpoint_path

    checkpoint = CheckpointManager(
        checkpoint_path,
        timeframe=args.timeframe,
        start=args.start,
        end=args.end,
        symbols=symbols,
    )

    if args.reset:
        checkpoint.reset()
    else:
        checkpoint.load()

    if args.redo_futures:
        reset_list = checkpoint.reset_futures_for_rebackfill(
            FUTURES_SYMBOLS,
            only_zero_bars=args.redo_futures_zero_only,
        )
        if not reset_list:
            logger.warning(
                "No futures symbols were reset "
                "(use --redo-futures without --redo-futures-zero-only to force all futures)"
            )

    if args.status:
        checkpoint.print_status()
        return 0

    polygon_key = resolve_env("POLYGON_API_KEY")
    if not polygon_key:
        logger.error("POLYGON_API_KEY is not set in backend/.env")
        return 1
    if is_env_placeholder(polygon_key):
        logger.error(
            "POLYGON_API_KEY is a shell placeholder (<your key>). Run: unset POLYGON_API_KEY"
        )
        return 1

    job_start = parse_date(args.start)
    job_end = parse_date(args.end)
    if job_end < job_start:
        logger.error("end %s is before start %s", args.end, args.start)
        return 1

    futures_year = args.futures_year or infer_backfill_year(args.start, args.end)

    client = PolygonBackfillClient(api_key=polygon_key)
    store: TimescaleStore | None = None
    if not args.skip_db:
        store = TimescaleStore()
        if not store.available:
            logger.error("DATABASE_URL unavailable — use --skip-db for CSV only")
            return 1
        logger.info("TimescaleDB connected — upserting to ohlcv_candles")

    data_path = Path(args.data_path)
    if not data_path.is_absolute():
        data_path = _BACKEND / data_path

    logger.info(
        "Backfill %s → %s | timeframe=%s | %d symbols | futures_year=%s | checkpoint=%s",
        args.start,
        args.end,
        args.timeframe,
        len(symbols),
        futures_year,
        checkpoint_path,
    )

    total_bars = 0
    failed: list[str] = []
    ok_count = 0

    try:
        for sym in symbols:
            success, bars = _run_symbol(
                sym,
                client=client,
                store=store,
                checkpoint=checkpoint,
                timeframe=args.timeframe,
                job_start=job_start,
                job_end=job_end,
                end=args.end,
                futures_year=futures_year,
                chunk_days=args.chunk_days,
                export_csv=args.export_csv,
                data_path=data_path,
            )
            total_bars += bars
            if success:
                ok_count += 1
            else:
                failed.append(sym)
    except KeyboardInterrupt:
        logger.info(
            "Interrupted — progress saved to %s. Re-run the same command to continue.",
            checkpoint_path,
        )
        checkpoint.print_status()
        return 130

    logger.info(
        "Backfill complete | symbols=%d ok=%d failed=%d total_bars=%d",
        len(symbols),
        ok_count,
        len(failed),
        total_bars,
    )
    if failed:
        logger.warning("Failed (resume will retry): %s", ", ".join(failed))
    checkpoint.print_status()
    return 1 if failed and ok_count == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
