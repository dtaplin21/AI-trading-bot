# Trading AI Model

Multi-layer futures trading signal engine with **SignalRank** scoring (0–100), probabilistic wave analysis, harmonic pattern detection, and a non-negotiable risk gate.

## Architecture

Eleven analytical layers feed into SignalRank, orchestrated by the **Trading Supervisor** multi-agent pipeline (`agents/supervisor.py`). See `docs/architecture.md` for the full agent hierarchy.

## Quick Start

### Backend (Python)

```bash
cd trading-ai-model
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
pytest
uvicorn api.main:app --reload
```

### Frontend (React)

```bash
cd trading-ai-model/frontend
npm install
npm run dev
```

Open http://localhost:5173 — the dashboard fetches trades from `GET /trades` (proxied to the API on port 8000).

Run both terminals together for live API data; the UI falls back to mock trades if the API is down.

### TimescaleDB

```bash
docker compose up -d
cp .env.example .env   # DATABASE_URL=postgresql://trading:trading@localhost:5432/trading_ai
pip install -e ".[storage]"
```

Candles are stored automatically by the Market Data Agent on each pipeline run.

### Model retraining (weekly, manual promotion)

```bash
pip install -e ".[ml]"
python scripts/run_scheduled_retrain.py
# Then via API:
# POST /models/{id}/approve
# POST /models/{id}/promote?approved_by=your_name
```

### LLM explanations (optional)

Set `LLM_ENABLED=true` and `LLM_API_KEY=...` in `.env`. The LLM only explains decisions — it never executes trades.

## Constraint Rules

- **Gann**: research-only; modifies SignalRank ± only; 300+ samples + random baseline required
- **Elliott Wave**: always probabilistic; 0.60+ confidence to influence SignalRank
- **Harmonics**: 2–5% ratio tolerance; 300+ samples for production weight; must beat random baseline

## Project Layout

See `docs/architecture.md` for the full layer diagram and data flow.

## Status

Scaffold phase — core engines, SignalRank, risk gate, API stubs, and unit tests are in place. Live trading and ML training pipelines are stubbed for future phases.
