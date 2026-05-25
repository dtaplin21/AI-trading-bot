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
| Retrain pipeline | `agents/learning/retrain_pipeline.py` | Weekly schedule, manual promote |
| LLM explainer | `agents/llm_explainer.py` | Explanation only, never executes |
