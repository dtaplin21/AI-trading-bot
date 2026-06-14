"""
chart_watcher/chart_watch_runner.py

The always-on 24/7 chart watcher.
The missing layer between market data and TradingPipelineSupervisor.

Three modes (set WATCHER_MODE in .env):
  live   — connects to live broker WebSocket, processes bars in real time
  replay — walks a historical date range bar by bar (anti-look-ahead enforced)
  paper  — reads from CSV/JSONL files for backtesting and dev

Env vars:
  WATCHER_MODE           live | replay | paper  (default: paper)
  WATCHER_SYMBOLS        comma-separated, e.g. MES,NQ,CL,GC
  WATCHER_TIMEFRAMES     comma-separated, e.g. 1m,5m,15m,1h
  WATCHER_REPLAY_START   ISO date for replay start, e.g. 2025-01-01
  WATCHER_REPLAY_END     ISO date for replay end,   e.g. 2025-12-31
  WATCHER_REPLAY_SPEED   float multiplier, 1.0=realtime, 100=fast (replay only)
  WATCHER_DATA_PATH      path to CSV/JSONL files for paper/replay mode
  WATCHER_REPLAY_TIMEFRAME  bar timeframe for DB replay (default 1m)
  WATCHER_REPLAY_DB_LIMIT   max bars per symbol from Timescale (default 500000)
  WATCHER_BAR_INTERVAL   seconds between candle polls in live mode (default 60)
  TICK_STREAM_MODE       broker | websocket | rest — live data source (default broker)
  WATCHER_LOG_BARS       true|false — log every bar received (default false)
"""
from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import AsyncIterator, Optional, Union, cast

import pandas as pd

from agents.news.db_news_reader import DbNewsReader
from agents.news.market_news_agent import MarketNewsAgent
from chart_watcher.bar_assembler import MultiSymbolAssembler
from chart_watcher.session_scheduler import SessionScheduler, WatcherMode
from config.watchlist import watcher_symbols_from_env, watcher_timeframes_from_env
from pipeline.feature_fusion_news_patch import NewsAgentProtocol
from pipeline.schemas import OHLCV
from data.storage.timescale_store import TimescaleStore
from data.storage.timeseries_store import TimeseriesStore
from pipeline.trading_supervisor import TradingPipelineSupervisor
from risk.kill_switch_runtime import is_kill_switch_active

logger = logging.getLogger(__name__)

SYMBOLS = watcher_symbols_from_env()
TIMEFRAMES = watcher_timeframes_from_env()

LOG_BARS = os.getenv("WATCHER_LOG_BARS", "false").lower() == "true"
BAR_INTERVAL = int(os.getenv("WATCHER_BAR_INTERVAL", "60"))
REPLAY_SPEED = float(os.getenv("WATCHER_REPLAY_SPEED", "100.0"))
DATA_PATH = Path(os.getenv("WATCHER_DATA_PATH", "data/ohlcv"))
REPLAY_TIMEFRAME = os.getenv("WATCHER_REPLAY_TIMEFRAME", "1m")
REPLAY_DB_LIMIT = int(os.getenv("WATCHER_REPLAY_DB_LIMIT", "500000"))
MIN_OHLCV_BARS = 20


def _dataframe_float(value: object, default: float = 0.0) -> float:
    """Coerce a DataFrame cell to float (handles None/NaN for type checkers)."""
    if value is None:
        return default
    if isinstance(value, float) and pd.isna(value):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return float(value)
    return default


def _coerce_index_timestamp(ts_val: object) -> datetime | None:
    """Convert a DataFrame index value to UTC datetime; skip NaT/invalid."""
    if ts_val is pd.NaT:
        return None
    if isinstance(ts_val, pd.Timestamp):
        dt = ts_val.to_pydatetime()
    elif isinstance(ts_val, datetime):
        dt = ts_val
    else:
        try:
            parsed = pd.Timestamp(str(ts_val))
        except (ValueError, TypeError):
            return None
        if parsed is pd.NaT:
            return None
        dt = parsed.to_pydatetime()
    if dt is pd.NaT or not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return cast(datetime, dt)


def _parse_bar_timestamp(ts_raw: object) -> datetime:
    """Parse ISO or unix epoch from CSV/JSONL timestamp fields."""
    if ts_raw is None:
        raise ValueError("missing timestamp")
    if isinstance(ts_raw, (int, float)):
        return datetime.fromtimestamp(float(ts_raw), tz=timezone.utc)
    text = str(ts_raw).strip()
    if not text:
        raise ValueError("empty timestamp")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.fromtimestamp(float(text), tz=timezone.utc)


NewsHandle = Union[NewsAgentProtocol, DbNewsReader, MarketNewsAgent]


class ChartWatchRunner:
    """
    The always-on loop. One instance runs the entire system.

    Start it with:
        runner = ChartWatchRunner()
        await runner.start()
    """

    def __init__(self, news_agent: Optional[NewsHandle] = None) -> None:
        self._mode = WatcherMode(os.getenv("WATCHER_MODE", "paper").lower())
        self._scheduler = SessionScheduler()
        self._news = news_agent
        self._running = False

        self._supervisors: dict[str, TradingPipelineSupervisor] = {}
        self._assembler = MultiSymbolAssembler(
            symbols=SYMBOLS,
            timeframes=TIMEFRAMES,
            on_bar_complete=self._on_bar_complete,
        )

        self._bars_processed: dict[str, int] = {s: 0 for s in SYMBOLS}
        self._started_at: Optional[datetime] = None
        self._last_live_bar_ts: dict[str, datetime] = {}
        self._broker_adapter = None
        self._timescale: TimescaleStore | None = None
        self._timeseries: TimeseriesStore | None = None
        self._tick_loaders: list = []
        self._tick_aggregator = None
        self._symbol_last_bar: dict[str, str] = {}
        self._last_heartbeat_write: float = 0.0

        logger.info(
            "ChartWatchRunner: mode=%s | symbols=%s | timeframes=%s",
            self._mode.value,
            SYMBOLS,
            TIMEFRAMES,
        )

    async def start(self) -> None:
        """Start the watcher. Blocks until stopped."""
        self._running = True
        self._started_at = datetime.now(tz=timezone.utc)

        paper_mode = self._mode != WatcherMode.LIVE
        for sym in SYMBOLS:
            self._supervisors[sym] = TradingPipelineSupervisor(
                symbol=sym,
                timeframe=TIMEFRAMES[0],
                news_agent=self._news,
                paper_mode=paper_mode,
            )

        await self._start_news()

        logger.info(
            "ChartWatchRunner: STARTING | mode=%s | %d symbols | kill_switch=%s",
            self._mode.value,
            len(SYMBOLS),
            is_kill_switch_active(),
        )
        self._publish_watcher_status(force=True)

        try:
            if self._mode == WatcherMode.LIVE:
                await self._run_live()
            elif self._mode == WatcherMode.REPLAY:
                await self._run_replay()
            else:
                await self._run_paper()
        finally:
            self._running = False
            self._publish_watcher_status(running=False, force=True)
            await self._stop_news()
            await self._assembler.flush_all()
            logger.info(
                "ChartWatchRunner: stopped | bars_processed=%s",
                self._bars_processed,
            )

    async def stop(self) -> None:
        self._running = False
        for loader in self._tick_loaders:
            loader.stop()
        await self._stop_news()
        logger.info("ChartWatchRunner: stop requested")

    async def _start_news(self) -> None:
        if isinstance(self._news, DbNewsReader):
            await self._news.start_refresh()
            logger.info("ChartWatchRunner: DbNewsReader refresh started (no ingestion)")
        elif isinstance(self._news, MarketNewsAgent):
            self._news.start_background()
            logger.info("ChartWatchRunner: MarketNewsAgent background started (local mode)")

    async def _stop_news(self) -> None:
        if isinstance(self._news, (DbNewsReader, MarketNewsAgent)):
            await self._news.stop()

    async def _run_paper(self) -> None:
        logger.info("ChartWatchRunner: PAPER mode | data_path=%s", DATA_PATH)
        tasks = [self._process_symbol_file(sym) for sym in SYMBOLS]
        await asyncio.gather(*tasks, return_exceptions=True)

    def _store(self) -> TimescaleStore:
        if self._timescale is None:
            self._timescale = TimescaleStore()
        return self._timescale

    def _series_store(self) -> TimeseriesStore:
        if self._timeseries is None:
            self._timeseries = TimeseriesStore()
        return self._timeseries

    def _persist_bar(self, bar: OHLCV) -> None:
        """Upsert completed bar to ohlcv_candles (1m source bars only)."""
        if bar.timeframe != "1m":
            return
        store = self._series_store()
        if not store.available:
            return
        ts = bar.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        try:
            store.write_bar(
                symbol=bar.symbol,
                timeframe="1m",
                time=ts,
                open_=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
            )
        except Exception as exc:
            logger.warning(
                "ChartWatchRunner[%s]: failed to persist bar: %s",
                bar.symbol,
                exc,
            )

    async def _process_symbol_file(self, symbol: str) -> None:
        count = 0
        async for bar in self._symbol_bar_source(symbol):
            await self._route_bar(bar)
            count += 1
        if count == 0:
            self._log_missing_data(symbol, context="paper")

    def _log_missing_data(self, symbol: str, *, context: str) -> None:
        logger.error(
            "ChartWatchRunner[%s]: no OHLCV for %s mode — "
            "add %s/%s_1m.csv|.jsonl or ingest bars into Timescale (timeframe=%s).",
            symbol,
            context,
            DATA_PATH,
            symbol,
            REPLAY_TIMEFRAME,
        )

    async def _read_csv(self, symbol: str, path: Path) -> AsyncIterator[OHLCV]:
        with open(path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if not self._running:
                    return
                try:
                    ts_raw = row.get("timestamp") or row.get("time") or row.get("date")
                    ts = _parse_bar_timestamp(ts_raw)

                    yield OHLCV(
                        symbol=symbol,
                        timeframe="1m",
                        timestamp=ts,
                        open=float(row.get("open", 0)),
                        high=float(row.get("high", 0)),
                        low=float(row.get("low", 0)),
                        close=float(row.get("close", 0)),
                        volume=float(row.get("volume", 0)),
                    )
                    await asyncio.sleep(0)
                except (KeyError, ValueError) as e:
                    logger.debug("ChartWatchRunner CSV parse error: %s | row=%s", e, row)

    async def _read_jsonl(self, symbol: str, path: Path) -> AsyncIterator[OHLCV]:
        with open(path, "r") as f:
            for line in f:
                if not self._running:
                    return
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    ts_raw = d.get("timestamp") or d.get("time") or d.get("t")
                    ts = _parse_bar_timestamp(ts_raw)

                    yield OHLCV(
                        symbol=symbol,
                        timeframe="1m",
                        timestamp=ts,
                        open=float(d.get("open", d.get("o", 0))),
                        high=float(d.get("high", d.get("h", 0))),
                        low=float(d.get("low", d.get("l", 0))),
                        close=float(d.get("close", d.get("c", 0))),
                        volume=float(d.get("volume", d.get("v", 0))),
                    )
                    await asyncio.sleep(0)
                except (json.JSONDecodeError, KeyError, ValueError) as e:
                    logger.debug("ChartWatchRunner JSONL parse error: %s", e)

    async def _run_replay(self) -> None:
        start_str = os.getenv("WATCHER_REPLAY_START", "2025-01-01")
        end_str = os.getenv("WATCHER_REPLAY_END", "2025-12-31")
        start_dt = datetime.fromisoformat(start_str).replace(tzinfo=timezone.utc)
        end_dt = datetime.fromisoformat(end_str).replace(tzinfo=timezone.utc)

        logger.info(
            "ChartWatchRunner: REPLAY %s → %s | speed=%.0fx",
            start_str,
            end_str,
            REPLAY_SPEED,
        )

        logger.info(
            "ChartWatchRunner: REPLAY sources | data_path=%s | db=%s | timeframe=%s",
            DATA_PATH,
            self._store().available,
            REPLAY_TIMEFRAME,
        )

        all_bars: list[OHLCV] = []
        for sym in SYMBOLS:
            bars = await self._load_all_bars(sym, start_dt, end_dt)
            if not bars:
                self._log_missing_data(sym, context="replay")
            all_bars.extend(bars)

        all_bars.sort(key=lambda b: b.timestamp)
        logger.info("ChartWatchRunner: replay loaded %d bars total", len(all_bars))

        if not all_bars:
            logger.error(
                "ChartWatchRunner: replay aborted — 0 bars in [%s, %s]. "
                "Provide files under %s or OHLCV in Timescale.",
                start_str,
                end_str,
                DATA_PATH,
            )
            return

        for bar in all_bars:
            if not self._running:
                break
            await self._route_bar(bar)
            if REPLAY_SPEED < 1000:
                delay = 60.0 / REPLAY_SPEED
                await asyncio.sleep(max(0.0, delay))
            else:
                await asyncio.sleep(0)

        await self._assembler.flush_all()
        logger.info("ChartWatchRunner: replay complete")

    async def _load_all_bars(
        self, symbol: str, start: datetime, end: datetime
    ) -> list[OHLCV]:
        bars: list[OHLCV] = []
        async for bar in self._symbol_bar_source(symbol, start=start, end=end):
            ts = bar.timestamp if bar.timestamp.tzinfo else bar.timestamp.replace(tzinfo=timezone.utc)
            if start <= ts <= end:
                bars.append(bar)
        if bars:
            logger.info("ChartWatchRunner[%s]: replay window has %d bars", symbol, len(bars))
        return bars

    def _bars_from_dataframe(self, symbol: str, df: pd.DataFrame) -> list[OHLCV]:
        if df.empty:
            return []
        bars: list[OHLCV] = []
        has_volume = "volume" in df.columns
        for i in range(len(df)):
            row = df.iloc[i]
            t = _coerce_index_timestamp(df.index[i])
            if t is None:
                continue
            bars.append(
                OHLCV(
                    symbol=symbol,
                    timeframe=REPLAY_TIMEFRAME,
                    timestamp=t,
                    open=_dataframe_float(row["open"]),
                    high=_dataframe_float(row["high"]),
                    low=_dataframe_float(row["low"]),
                    close=_dataframe_float(row["close"]),
                    volume=_dataframe_float(row["volume"]) if has_volume else 0.0,
                ),
            )
        return bars

    async def _iter_db_bars(
        self,
        symbol: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> AsyncIterator[OHLCV]:
        store = self._store()
        if not store.available:
            return

        if start is not None and end is not None:
            df = store.load_ohlcv_range(
                symbol,
                REPLAY_TIMEFRAME,
                start,
                end,
                limit=REPLAY_DB_LIMIT,
            )
            source = f"Timescale range [{start.isoformat()}, {end.isoformat()}]"
        else:
            df = store.load_ohlcv(
                symbol,
                REPLAY_TIMEFRAME,
                limit=min(REPLAY_DB_LIMIT, 50_000),
            )
            source = "Timescale (recent)"

        if df.empty:
            return

        logger.info(
            "ChartWatchRunner[%s]: %d bars from %s (timeframe=%s)",
            symbol,
            len(df),
            source,
            REPLAY_TIMEFRAME,
        )
        for bar in self._bars_from_dataframe(symbol, df):
            yield bar
            await asyncio.sleep(0)

    async def _symbol_bar_source(
        self,
        symbol: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> AsyncIterator[OHLCV]:
        """CSV → JSONL → TimescaleDB."""
        csv_path = DATA_PATH / f"{symbol}_1m.csv"
        jsonl_path = DATA_PATH / f"{symbol}_1m.jsonl"
        if csv_path.exists():
            logger.info("ChartWatchRunner[%s]: loading %s", symbol, csv_path)
            async for bar in self._read_csv(symbol, csv_path):
                yield bar
            return
        if jsonl_path.exists():
            logger.info("ChartWatchRunner[%s]: loading %s", symbol, jsonl_path)
            async for bar in self._read_jsonl(symbol, jsonl_path):
                yield bar
            return

        async for bar in self._iter_db_bars(symbol, start=start, end=end):
            yield bar

    async def _run_live(self) -> None:
        tick_mode = os.getenv("TICK_STREAM_MODE", "broker").strip().lower()
        if tick_mode in ("websocket", "rest"):
            await self._run_live_ticks(tick_mode)
            return

        from live.broker_adapter import get_broker_adapter

        broker = os.getenv("BROKER", "none").lower()
        self._broker_adapter = get_broker_adapter(broker)
        logger.info(
            "ChartWatchRunner: LIVE mode | broker=%s | interval=%ds",
            broker,
            BAR_INTERVAL,
        )

        for sym in SYMBOLS:
            if not self._scheduler.is_trading(sym):
                wait = self._scheduler.seconds_until_open(sym)
                logger.info(
                    "ChartWatchRunner[%s]: market closed | next open %s",
                    sym,
                    self._scheduler.next_session_label(sym),
                )
                if wait > 0:
                    await asyncio.sleep(min(wait, 300))

        while self._running:
            tasks = []
            for sym in SYMBOLS:
                if self._scheduler.is_trading(sym):
                    tasks.append(self._poll_symbol(sym, broker))

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

            await asyncio.sleep(BAR_INTERVAL)

    async def _run_live_ticks(self, tick_mode: str) -> None:
        """Live mode via Polygon tick stream → TickAggregator → pipeline."""
        from data.loaders.tick_data_loader import loaders_for_symbols
        from data.processors.tick_aggregator import MultiSymbolAggregator, bar_dict_to_ohlcv

        self._tick_aggregator = MultiSymbolAggregator(timeframes=TIMEFRAMES)
        self._tick_loaders = loaders_for_symbols(SYMBOLS)
        if not self._tick_loaders:
            logger.error("ChartWatchRunner: no tick loaders for symbols=%s", SYMBOLS)
            return

        logger.info(
            "ChartWatchRunner: LIVE tick mode | source=%s | loaders=%d",
            tick_mode,
            len(self._tick_loaders),
        )

        tick_agg = self._tick_aggregator

        async def consume(loader) -> None:
            async for tick in loader.stream():
                if not self._running:
                    break
                sym = tick.symbol.upper()
                if sym not in self._assembler.symbols():
                    continue
                if not self._scheduler.is_trading(sym):
                    continue
                if tick_agg is None:
                    continue
                completed = tick_agg.update(sym, tick.price, tick.size, tick.timestamp)
                for bar_dict in completed:
                    bar = bar_dict_to_ohlcv(bar_dict)
                    asm = self._assembler.get(sym)
                    if asm:
                        asm.record_completed(bar)
                    await self._on_bar_complete(bar)
                    self._bars_processed[sym] = self._bars_processed.get(sym, 0) + 1

        tasks = [asyncio.create_task(consume(loader)) for loader in self._tick_loaders]
        try:
            await asyncio.gather(*tasks)
        finally:
            for loader in self._tick_loaders:
                loader.stop()

    async def _poll_symbol(self, symbol: str, broker: str) -> None:
        bar = await self._fetch_live_candle(symbol, broker)
        if not bar:
            return
        last_ts = self._last_live_bar_ts.get(symbol)
        bar_ts = bar.timestamp if bar.timestamp.tzinfo else bar.timestamp.replace(tzinfo=timezone.utc)
        if last_ts and bar_ts <= last_ts:
            return
        self._last_live_bar_ts[symbol] = bar_ts
        await self._route_bar(bar)

    async def _fetch_live_candle(self, symbol: str, broker: str) -> Optional[OHLCV]:
        adapter = self._broker_adapter
        if adapter is None:
            from live.broker_adapter import get_broker_adapter

            adapter = get_broker_adapter(broker)
            self._broker_adapter = adapter
        try:
            return await adapter.fetch_latest_bar(symbol, "1m")
        except Exception as exc:
            logger.error(
                "ChartWatchRunner[%s]: broker adapter failed (%s): %s",
                symbol,
                broker,
                exc,
                exc_info=True,
            )
            return None

    async def _route_bar(self, bar: OHLCV) -> None:
        asm = self._assembler.get(bar.symbol)
        if asm:
            await asm.on_candle(bar)
        self._bars_processed[bar.symbol] = self._bars_processed.get(bar.symbol, 0) + 1

    def _history_to_dataframe(self, bars: list[OHLCV]) -> pd.DataFrame:
        if not bars:
            return pd.DataFrame()
        rows = [
            {
                "timestamp": b.timestamp,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
            }
            for b in bars
        ]
        df = pd.DataFrame(rows)
        df = df.set_index("timestamp").sort_index()
        return df

    async def _on_bar_complete(self, bar: OHLCV) -> None:
        try:
            from risk.kill_switch_actions import maybe_flatten_on_kill_active

            await maybe_flatten_on_kill_active()
        except Exception as exc:
            logger.error("ChartWatchRunner: kill switch flatten failed: %s", exc, exc_info=True)

        if LOG_BARS:
            logger.debug(
                "BAR [%s %s] O=%.2f H=%.2f L=%.2f C=%.2f V=%.0f",
                bar.symbol,
                bar.timeframe,
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                bar.volume,
            )

        self._persist_bar(bar)

        sup = self._supervisors.get(bar.symbol)
        asm = self._assembler.get(bar.symbol)
        if not sup or not asm:
            logger.warning("ChartWatchRunner: no supervisor for %s", bar.symbol)
            return

        history = asm.get_history(bar.timeframe)
        ohlcv = self._history_to_dataframe(history)

        if len(ohlcv) >= MIN_OHLCV_BARS:
            try:
                from config.symbols import get_symbol_or_none
                from ml.features.level_intelligence import get_system

                spec = get_symbol_or_none(bar.symbol)
                asset_class = spec.asset_class if spec else "equity"
                get_system(bar.symbol, asset_class).process_bar(ohlcv)
            except Exception as exc:
                logger.debug("LevelIntelligence process_bar [%s]: %s", bar.symbol, exc)

        try:
            result = await sup.on_new_bar(bar, ohlcv=ohlcv if len(ohlcv) >= MIN_OHLCV_BARS else None)
            if result.errors:
                logger.error(
                    "Pipeline error [%s %s]: %s",
                    bar.symbol,
                    bar.timeframe,
                    result.errors[0],
                )
            elif result.executed:
                logger.info(
                    "TRADE EXECUTED [%s %s]: rank=%d approved=%s",
                    bar.symbol,
                    bar.timeframe,
                    result.fused.signal_rank if result.fused else 0,
                    result.risk.approved if result.risk else False,
                )
        except Exception as e:
            logger.error(
                "ChartWatchRunner: pipeline exception [%s]: %s",
                bar.symbol,
                e,
                exc_info=True,
            )

        ts = bar.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        self._symbol_last_bar[bar.symbol.upper()] = ts.isoformat()
        self._publish_watcher_status()

    def _publish_watcher_status(
        self,
        *,
        running: bool | None = None,
        force: bool = False,
    ) -> None:
        now = time.monotonic()
        min_sec = float(os.getenv("WATCHER_HEARTBEAT_WRITE_SEC", "15"))
        if not force and (now - self._last_heartbeat_write) < min_sec:
            return
        self._last_heartbeat_write = now

        from chart_watcher.watcher_runtime import publish_watcher_status

        is_running = self._running if running is None else running
        publish_watcher_status(
            {
                "running": is_running,
                "mode": self._mode.value,
                "started_at": self._started_at.isoformat() if self._started_at else None,
                "symbols": list(SYMBOLS),
                "timeframes": list(TIMEFRAMES),
                "bars_processed": dict(self._bars_processed),
                "symbol_last_bar": dict(self._symbol_last_bar),
                "kill_switch": is_kill_switch_active(),
            }
        )

    def status(self) -> dict:
        return {
            "running": self._running,
            "mode": self._mode.value,
            "symbols": SYMBOLS,
            "timeframes": TIMEFRAMES,
            "bars_processed": self._bars_processed,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "supervisors": len(self._supervisors),
            "kill_switch": is_kill_switch_active(),
        }
