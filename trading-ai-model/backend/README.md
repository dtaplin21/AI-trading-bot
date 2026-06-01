# Trading AI Model — Backend

Multi-layer futures trading signal engine with **SignalRank** scoring (0–100), probabilistic wave analysis, harmonic pattern detection, and a non-negotiable risk gate.

## Architecture

Eleven analytical layers feed into SignalRank, orchestrated by the **Trading Supervisor** multi-agent pipeline (`agents/supervisor.py`). See `docs/architecture.md` for the full agent hierarchy.

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

### TimescaleDB

```bash
docker compose up -d
cp .env.example .env   # DATABASE_URL=postgresql://trading:trading@localhost:5432/trading_ai
pip install -e ".[storage]"
```

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

## Constraint Rules

- **Gann**: research-only; modifies SignalRank ± only; 300+ samples + random baseline required
- **Elliott Wave**: always probabilistic; 0.60+ confidence to influence SignalRank
- **Harmonics**: 2–5% ratio tolerance; 300+ samples for production weight; must beat random baseline

## Project Layout

See `docs/architecture.md` for the full layer diagram and data flow.

## Status

Scaffold phase — core engines, SignalRank, risk gate, API stubs, and unit tests are in place. Live trading and ML training pipelines are stubbed for future phases.
