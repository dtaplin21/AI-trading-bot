# Trading AI Model

Multi-layer futures trading signal engine with **SignalRank** scoring (0–100), probabilistic wave analysis, harmonic pattern detection, and a non-negotiable risk gate.

## Architecture

Eleven analytical layers feed into SignalRank:

| Layer | Module | Role |
|-------|--------|------|
| 1 | Price Action | Candlestick psychology, wick analysis |
| 2 | Geometry | Fibonacci, harmonics, Gann (research) |
| 3 | Wave | Probabilistic Elliott Wave |
| 4 | Number Theory | 369 levels, biblical cycles |
| 5 | Market State | Markov regime detection |
| 6 | Strategy Math | EV, Sharpe, R-multiples |
| 7 | Monte Carlo | Scenario simulation |
| 8 | ML | Signal classifiers |
| 9 | MCTS | Trade planning |
| 10 | Signal Engine | SignalRank aggregation |
| 11 | Risk | Approval / rejection gate |

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

## Constraint Rules

- **Gann**: research-only; modifies SignalRank ± only; 300+ samples + random baseline required
- **Elliott Wave**: always probabilistic; 0.60+ confidence to influence SignalRank
- **Harmonics**: 2–5% ratio tolerance; 300+ samples for production weight; must beat random baseline

## Project Layout

See `docs/architecture.md` for the full layer diagram and data flow.

## Status

Scaffold phase — core engines, SignalRank, risk gate, API stubs, and unit tests are in place. Live trading and ML training pipelines are stubbed for future phases.
