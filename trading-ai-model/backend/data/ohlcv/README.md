# OHLCV Data Directory

Place your candle data files here in this format:

  `{SYMBOL}_1m.csv`   or   `{SYMBOL}_1m.jsonl`

CSV format (headers required):

```
timestamp,open,high,low,close,volume
2025-01-06T14:30:00Z,5410.25,5418.50,5408.00,5415.75,1240
```

JSONL format (one JSON object per line):

```json
{"timestamp":"2025-01-06T14:30:00Z","open":5410.25,"high":5418.50,"low":5408.00,"close":5415.75,"volume":1240}
```

Accepted timestamp formats:

- ISO 8601: `2025-01-06T14:30:00Z`
- Unix epoch: `1736173800`

Symbols: MES, NQ, CL, GC, ZB, RTY  
(add any symbol matching `WATCHER_SYMBOLS` in `.env`)

**Replay / paper data priority:**

1. `{SYMBOL}_1m.csv` or `{SYMBOL}_1m.jsonl` in this folder  
2. TimescaleDB `ohlcv_candles` (requires `DATABASE_URL`; set `WATCHER_REPLAY_TIMEFRAME`, default `1m`)

Default path: `data/ohlcv` (override with `WATCHER_DATA_PATH` in `.env`).

Generate files with Polygon backfill (CSV-only, no DB):

```bash
python scripts/backfill_polygon.py --skip-db --timeframe 1m --start 2025-01-01 --end 2025-12-31 --chunk-days 10
```

Upload local CSVs to TimescaleDB later (e.g. Render `DATABASE_URL`):

```bash
python scripts/import_ohlcv_csv.py --timeframe 1m
python scripts/import_ohlcv_csv.py --symbols MES,ES,BTCUSD --timeframe 1m --dry-run
```
