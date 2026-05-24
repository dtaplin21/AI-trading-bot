# Architecture

## Data Flow

```
Market Data → Processors → Engine Layers (1–9) → Signal Builder → SignalRank → Risk Engine → Paper/Live
```

## Layer Priority

| Priority | Layers |
|----------|--------|
| Highest | Price Action, Harmonics, ML, SignalRank, Risk |
| High | Number Theory, Market State, Strategy Math, Monte Carlo, MCTS |
| Medium | Elliott Wave (probabilistic only) |
| Low | Gann Geometry (experimental) |

## Key Modules

- `signal_engine/signal_rank_service.py` — weighted 0–100 score
- `signal_engine/signal_builder.py` — orchestrates layer outputs
- `risk/risk_engine.py` — final approval gate
- `validation/random_baseline_generator.py` — geometric edge validation

## API

FastAPI app at `api/main.py`:

- `GET /health`
- `GET /signals`, `POST /signals/analyze`
- `POST /backtest`
- `GET /state/{symbol}`
