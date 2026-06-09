# Multi-Agent Trading Intelligence System

## Principle

Every chart is processed by **every method**. Every method produces features. The prediction model scores the full feature set. The **Risk Agent** decides whether action is allowed. The **Learning Agent** logs outcomes for scheduled retraining.

**No risk approval = no trade.**

## Agent Hierarchy

```
Trading Supervisor
├── Market Data Agent          → clean/store candles (no decisions)
├── Chart Reading Agent        → swings, trend, S/R, VWAP
├── Method Analysis Agents     → all theories, every candle
│   ├── Level 369 Agent
│   ├── Fibonacci / Spiral Agent
│   ├── Ancient Number Agent
│   ├── Gann Agent (research modifier only)
│   ├── Elliott Wave Agent (probabilistic)
│   ├── Harmonic Agent
│   ├── Candlestick Agent
│   ├── Fractal Agent
│   ├── Balance Line Agent
│   ├── Momentum / Acceleration Agent
│   ├── Markov State Agent
│   ├── Monte Carlo Agent
│   └── Strategy Math Agent
├── Feature Fusion Agent       → unified feature vector + SignalRank
├── Prediction Agent           → start/stop/wait/avoid (LightGBM path)
├── Trade Planning Agent       → MCTS action proposals
├── Risk Agent                 → veto gate
├── Execution Agent            → paper broker (v1)
├── Learning Agent             → observe/store (no live retrain)
└── Audit Agent                → explainability
```

## Market News Intelligence (`agents/news/`)

Runs on a **60s async loop** (API startup) or sync bootstrap for CLI/tests.

```
MarketNewsAgent
├── NewsIngestionService      → RSS feeds (Reuters, CNBC, MarketWatch)
├── NewsSentimentService      → classify + score
├── NewsSymbolMapper          → MES, ES, NQ, RTY, YM mapping
├── EconomicCalendarService   → blackout windows + size reduction
└── NewsRiskFilterService     → NewsFeatures for ML
```

| Caller | Method |
|--------|--------|
| Feature Fusion | `get_news_features(symbol, technical_direction)` |
| Risk Agent | `is_trading_blocked()`, `get_size_reduction_factor()` |
| Audit Agent | `get_latest_explanation(symbol)` |
| Learning Agent | `get_recent_events(symbol, hours=24)` |

API: `GET /news/status`, `POST /news/refresh`, `GET /news/features/{symbol}`

## Per-Candle Pipeline

```
New candle → Market Data → Chart Reading → All Methods → Feature Fusion
→ Prediction → MCTS Planning → Risk Agent → Execution (if approved)
→ Learning log → Audit report
```

## Safe Learning Loop

```
Observe → Store → Label → Backtest → Retrain → Validate → Paper test → Approve → Deploy
```

Never: Observe → Retrain → Immediately live trade.

## Implementation Notes

- Agents are **Python services**, not chat LLMs
- LLM layer reserved for explanation/research summaries only
- Existing `engines/` modules are wrapped by method agents
- Entry point: `agents/supervisor.py` → `TradingSupervisor.process_candle()`

## API

```bash
POST /signals/analyze?symbol=MES&timeframe=5m&historical_sample_size=1420
GET  /models
POST /models/retrain?force=false
POST /models/{id}/approve
POST /models/{id}/promote?approved_by=admin
```

Returns full `PipelineDecision` + `llm_explanation` when LLM enabled.

## Infrastructure

| Component | Module | Notes |
|-----------|--------|-------|
| TimescaleDB | `data/storage/timescale_store.py` | `docker compose up -d` |
| LightGBM | `ml/models/lightgbm_classifier.py` | Falls back to rules if no model |
| Retrain pipeline | `agents/learning/retrain_pipeline.py` | Daily schedule (RETRAIN_SCHEDULE_DAYS=1), manual promote |
| LLM explainer | `agents/llm_explainer.py` | Explanation only, never executes |

## Stub implementation roadmap

Many modules are scaffolds (docstring-only, passthrough, or hardcoded). **Start with the data layer (Tier 2)** — without wiring bar persistence and feature cache, live bars never reach training.

Full tier list, current vs target status, and wiring checklist: **[stub_implementation_plan.md](./stub_implementation_plan.md)**

| Priority | Tier | Focus |
|----------|------|-------|
| 1 | Tier 2 | `TimeseriesStore`, `FeatureStore`, tick loader/aggregator — wire into watcher |
| 2 | Tier 1 | Reversal/chop/continuation predictors + `FeaturePipeline` |
| 3 | Tier 3–7 | Risk, ML features, validation, MCTS, symbol intelligence |
| 4 | Tier 8–10 | Live execution, Streamlit dashboard, research tooling |
