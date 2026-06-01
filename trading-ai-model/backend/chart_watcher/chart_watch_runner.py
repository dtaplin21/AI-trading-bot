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
  WATCHER_BAR_INTERVAL   seconds between candle polls in live mode (default 60)
  WATCHER_LOG_BARS       true|false — log every bar received (default false)
"""
from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import AsyncIterator, Optional, Union

import pandas as pd

from agents.news.db_news_reader import DbNewsReader
from agents.news.market_news_agent import MarketNewsAgent
from chart_watcher.bar_assembler import MultiSymbolAssembler
from chart_watcher.session_scheduler import SessionScheduler, WatcherMode
from config.agent_config import WATCHED_SYMBOLS, WATCHED_TIMEFRAMES
from pipeline.feature_fusion_news_patch import NewsAgentProtocol
from pipeline.schemas import OHLCV
from pipeline.trading_supervisor import TradingPipelineSupervisor

logger = logging.getLogger(__name__)

_sym_env = os.getenv("WATCHER_SYMBOLS", ",".join(WATCHED_SYMBOLS))
_tf_env = os.getenv("WATCHER_TIMEFRAMES", ",".join(WATCHED_TIMEFRAMES))
SYMBOLS = [s.strip().upper() for s in _sym_env.split(",") if s.strip()]
TIMEFRAMES = [t.strip() for t in _tf_env.split(",") if t.strip()]

LOG_BARS = os.getenv("WATCHER_LOG_BARS", "false").lower() == "true"
BAR_INTERVAL = int(os.getenv("WATCHER_BAR_INTERVAL", "60"))
REPLAY_SPEED = float(os.getenv("WATCHER_REPLAY_SPEED", "100.0"))
DATA_PATH = Path(os.getenv("WATCHER_DATA_PATH", "data/ohlcv"))
MIN_OHLCV_BARS = 20


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

        for sym in SYMBOLS:
            self._supervisors[sym] = TradingPipelineSupervisor(
                symbol=sym,
                timeframe=TIMEFRAMES[0],
                news_agent=self._news,
                paper_mode=True,
            )

        await self._start_news()

        logger.info(
            "ChartWatchRunner: STARTING | mode=%s | %d symbols | kill_switch=%s",
            self._mode.value,
            len(SYMBOLS),
            os.getenv("RISK_KILL_SWITCH", "false"),
        )

        try:
            if self._mode == WatcherMode.LIVE:
                await self._run_live()
            elif self._mode == WatcherMode.REPLAY:
                await self._run_replay()
            else:
                await self._run_paper()
        finally:
            self._running = False
            await self._stop_news()
            await self._assembler.flush_all()
            logger.info(
                "ChartWatchRunner: stopped | bars_processed=%s",
                self._bars_processed,
            )

    async def stop(self) -> None:
        self._running = False
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

    async def _process_symbol_file(self, symbol: str) -> None:
        csv_path = DATA_PATH / f"{symbol}_1m.csv"
        jsonl_path = DATA_PATH / f"{symbol}_1m.jsonl"

        if csv_path.exists():
            async for bar in self._read_csv(symbol, csv_path):
                await self._route_bar(bar)
        elif jsonl_path.exists():
            async for bar in self._read_jsonl(symbol, jsonl_path):
                await self._route_bar(bar)
        else:
            logger.warning(
                "ChartWatchRunner[%s]: no data file found at %s",
                symbol,
                DATA_PATH,
            )
            await self._generate_dummy_bars(symbol)

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

    async def _generate_dummy_bars(self, symbol: str) -> None:
        import random

        logger.warning(
            "ChartWatchRunner[%s]: generating 500 synthetic bars for dev/test",
            symbol,
        )
        base_prices = {
            "MES": 5400.0,
            "ES": 5400.0,
            "NQ": 19200.0,
            "MNQ": 19200.0,
            "CL": 78.50,
            "GC": 2350.0,
            "ZB": 115.0,
            "RTY": 2100.0,
        }
        price = base_prices.get(symbol, 5000.0)
        ts = datetime(2025, 1, 6, 14, 30, tzinfo=timezone.utc)

        for _ in range(500):
            if not self._running:
                return
            change = random.gauss(0, price * 0.001)
            open_ = price
            close_ = price + change
            high_ = max(open_, close_) + abs(random.gauss(0, price * 0.0005))
            low_ = min(open_, close_) - abs(random.gauss(0, price * 0.0005))
            vol = random.randint(100, 2000)
            price = close_

            bar = OHLCV(
                symbol=symbol,
                timeframe="1m",
                timestamp=ts,
                open=round(open_, 2),
                high=round(high_, 2),
                low=round(low_, 2),
                close=round(close_, 2),
                volume=float(vol),
            )
            await self._route_bar(bar)
            ts += timedelta(minutes=1)
            await asyncio.sleep(0)

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

        all_bars: list[OHLCV] = []
        for sym in SYMBOLS:
            bars = await self._load_all_bars(sym, start_dt, end_dt)
            all_bars.extend(bars)

        all_bars.sort(key=lambda b: b.timestamp)
        logger.info("ChartWatchRunner: replay loaded %d bars total", len(all_bars))

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
        async for bar in self._symbol_bar_source(symbol):
            if start <= bar.timestamp <= end:
                bars.append(bar)
        return bars

    async def _symbol_bar_source(self, symbol: str) -> AsyncIterator[OHLCV]:
        csv_path = DATA_PATH / f"{symbol}_1m.csv"
        jsonl_path = DATA_PATH / f"{symbol}_1m.jsonl"
        if csv_path.exists():
            async for bar in self._read_csv(symbol, csv_path):
                yield bar
        elif jsonl_path.exists():
            async for bar in self._read_jsonl(symbol, jsonl_path):
                yield bar

    async def _run_live(self) -> None:
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

        sup = self._supervisors.get(bar.symbol)
        asm = self._assembler.get(bar.symbol)
        if not sup or not asm:
            logger.warning("ChartWatchRunner: no supervisor for %s", bar.symbol)
            return

        history = asm.get_history(bar.timeframe)
        ohlcv = self._history_to_dataframe(history)

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

    def status(self) -> dict:
        return {
            "running": self._running,
            "mode": self._mode.value,
            "symbols": SYMBOLS,
            "timeframes": TIMEFRAMES,
            "bars_processed": self._bars_processed,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "supervisors": len(self._supervisors),
            "kill_switch": os.getenv("RISK_KILL_SWITCH", "false"),
        }
