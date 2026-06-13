# Trading AI Model — Backend

Multi-layer futures trading signal engine with **SignalRank** scoring (0–100), probabilistic wave analysis, harmonic pattern detection, and a non-negotiable risk gate.

## Architecture

Eleven analytical layers feed into SignalRank, orchestrated by the **Trading Supervisor** multi-agent pipeline (`agents/supervisor.py`). See `docs/architecture.md` for the full agent hierarchy.

**Stub roadmap:** ~90 scaffold modules remain. Implementation order and current status are documented in [`docs/stub_implementation_plan.md`](docs/stub_implementation_plan.md). **Start with Tier 2 (data layer)** — wire bar persistence and feature cache before expanding ML stubs.

## Quick Start

```bash
cd backend
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
pytest
uvicorn api.main:app --reload
```

### Polygon historical backfill (before replay)

Replay reads bars from **TimescaleDB** or `data/ohlcv/*.csv` — it does not call Polygon. Backfill first:

```bash
cd backend
source .venv/bin/activate
# CSV-only (fast, no remote Postgres write stalls):
python scripts/backfill_polygon.py --skip-db --timeframe 1m --start 2025-01-01 --end 2025-12-31 --chunk-days 10
# Upload CSVs to DB later (set DATABASE_URL to Render when ready):
python scripts/import_ohlcv_csv.py --timeframe 1m
# Or DB upsert during backfill: omit --skip-db (requires DATABASE_URL)
```

Replay prefers `data/ohlcv/{SYMBOL}_1m.csv` when present (`WATCHER_DATA_PATH`). Then set `WATCHER_MODE=replay` on the worker and redeploy.

Options: `--symbols MES,BTCUSD --start 2025-01-01 --end 2025-12-31 --timeframe 1m`

### TimescaleDB

```bash
docker compose up -d
cp .env.example .env   # DATABASE_URL=postgresql://trading:trading@localhost:5432/trading_ai
pip install -e ".[storage]"
```

On first start (and each deploy with new SQL files), `main.py` runs pending
`db/migrations/*.sql` automatically (`schema_migrations` tracks applied files).
Manual run: `python scripts/run_migrations.py`.

Candles are stored automatically by the Market Data Agent on each pipeline run.

### Model retraining (daily schedule, manual promotion)

```bash
pip install -e ".[ml]"
python scripts/run_scheduled_retrain.py   # cron: daily, e.g. 0 2 * * *
# Then via API:
# POST /models/{id}/approve
# POST /models/{id}/promote?approved_by=your_name
```

### LLM explanations (optional)

Set `LLM_ENABLED=true` and `ANTHROPIC_API_KEY=...` in `.env`. Anthropic is used for news sentiment and audit explanations only — it never executes trades.

### Coinbase (crypto execution)

Paper trading is the default. Coinbase Advanced Trade is wired for **crypto only** (BTCUSD, ETHUSD, etc.) with dollar risk caps.

1. Copy risk/Coinbase vars from `gi.example` into `.env`.
2. Create a [CDP API key](https://docs.cdp.coinbase.com/advanced-trade/docs/getting-started) with **View + Trade** only.
3. Keep `PAPER_TRADING_ENABLED=true` while testing.
4. When ready for live crypto on your primary account:

```bash
PAPER_TRADING_ENABLED=false
COINBASE_LIVE_ENABLED=true
ENABLED_BROKERS=coinbase
COINBASE_API_KEY=organizations/.../apiKeys/...
COINBASE_API_SECRET="-----BEGIN EC PRIVATE KEY-----\n...\n-----END EC PRIVATE KEY-----\n"
RISK_ACCOUNT_CAP_USD=500
RISK_MAX_DAILY_LOSS_USD=30
```

Live orders require all four gates: paper off, `COINBASE_LIVE_ENABLED`, credentials, and `coinbase` in `ENABLED_BROKERS`.

### OANDA (forex execution)

OANDA v20 is wired for **forex pairs only** (EURUSD, GBPUSD, USDJPY, etc.). The dashboard shows OANDA under **Trading platforms**; live readiness appears as `oanda_live_ready` on `GET /dashboard`.

1. Copy OANDA vars from `gi.example` into `.env` (accepts `OANDA_API_KEY` or `ONDA_API_KEY`).
2. Create an API token in OANDA → **Manage API Access**.
3. Set `OANDA_ACCOUNT_ID` (or leave empty to auto-pick the first account on first order).
4. Keep `OANDA_PRACTICE=true` until you are ready for the live fxTrade endpoint.

```bash
PAPER_TRADING_ENABLED=false
OANDA_LIVE_ENABLED=true
ENABLED_BROKERS=oanda
OANDA_API_KEY=your-token
OANDA_ACCOUNT_ID=101-001-1234567-001
OANDA_PRACTICE=true
```

Live forex orders require: paper off, `OANDA_LIVE_ENABLED`, API key, and `oanda` in `ENABLED_BROKERS`. With both Coinbase and OANDA enabled, execution routes by symbol (crypto → Coinbase, forex → OANDA).

### MCP server (Cursor / agent tooling)

The `trading-agents` MCP server exposes registry admin, level intelligence, and risk tools. Configure it in **Cursor → Settings → MCP** (workspace file: `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "trading-agents": {
      "command": "/absolute/path/to/backend/.venv/bin/python",
      "args": ["-m", "trading_mcp.trading_server"],
      "cwd": "/absolute/path/to/backend",
      "env": {
        "PYTHONPATH": "/absolute/path/to/backend",
        "AGENT_CONFIG_PATH": "/absolute/path/to/backend/config/agents.yaml",
        "DATABASE_URL": "postgresql://...",
        "DATABASE_SSL_DISABLE": "true",
        "PAPER_MODE": "true",
        "RISK_KILL_SWITCH": "false"
      }
    }
  }
}
```

Copy `DATABASE_URL` from `backend/.env`. Restart the MCP server after config changes.

**Tools:** `list_agents`, `set_agent_config`, `reload_config`, `get_level_watchlist`, `check_level_gate`, `get_recent_touches`, `get_risk_summary`, `set_kill_switch`, `get_pipeline_status`.

**Resources:** `file:///agents.yaml` — read-only view of the agent manifest.

**Remote agents (`transport: mcp`):** In `config/agents.yaml`, set `transport: mcp` and `mcp_server` to a JSON stdio launch spec. The remote server must expose tool `run_method` (or override via `config.mcp_tool`) returning `MethodOutput` JSON. The pipeline registry instantiates `RemoteAgentProxy` instead of a local class.

Per-agent env overrides: `AGENT_{AGENT_ID}_ENABLED`, `AGENT_{AGENT_ID}_TIMEOUT_MS` (e.g. `AGENT_METHOD_GANN_ENABLED=false`).

## Constraint Rules

- **Gann**: research-only; modifies SignalRank ± only; 300+ samples + random baseline required
- **Elliott Wave**: always probabilistic; 0.60+ confidence to influence SignalRank
- **Harmonics**: 2–5% ratio tolerance; 300+ samples for production weight; must beat random baseline

## Project Layout

See `docs/architecture.md` for the full layer diagram and data flow.

## Status

Scaffold phase — core engines, SignalRank, risk gate, API stubs, and unit tests are in place. Live trading and ML training pipelines are stubbed for future phases.
