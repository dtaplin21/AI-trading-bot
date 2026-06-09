# Stub Implementation Plan

Ordered by impact on the live trading goal.

**Goal:** 23-symbol AI trading system that identifies high-probability reversal setups, paper trades them, accumulates outcomes, and improves over time.

**Audit date:** 2026-05-31  
**Legend:** `STUB` = placeholder / noop · `PARTIAL` = code exists but not wired · `DONE` = implemented and used in production path

---

## Implementation order (start here)

> **Start with Tier 2 — Data layer.** Without it, every bar the watcher collects can be silently discarded and never reaches the model or training pipeline.

Even when `TimescaleStore` works (backfill, import, replay), the thinner facade modules (`TimeseriesStore`, `FeatureStore`, tick pipeline) must be **wired** into `ChartWatchRunner` and `TradingPipelineSupervisor` or live bars never persist and features are recomputed every candle with no cache.

Recommended sequence:

1. **Tier 2** — wire data read/write + feature cache + optional tick path  
2. **Tier 1** — real prediction models + feature pipeline (train/serve parity)  
3. **Tier 3** — risk controls  
4. **Tier 4** — ML feature extractors  
5. **Tier 5–7** — validation, MCTS support, symbol intelligence  
6. **Tier 8–10** — live execution, dashboard, research tooling  

---

## Current vs target snapshot

| Tier | Target | Current (repo) |
|------|--------|----------------|
| 1 | LightGBM + chop + continuation + feature pipeline | **STUB** — predictors return constants; `feature_pipeline.py` passthrough |
| 2 | Timescale read/write, feature cache, tick stream, tick→bars | **PARTIAL** — modules implemented; **not wired** into watcher/supervisor |
| 3 | Correlation checker + trainer | **STUB** |
| 4 | 8 ML feature extractors | **STUB** — all `return layer_output` |
| 5–10 | See tiers below | **STUB** — docstring-only or hardcoded |

**Production path today** uses `TimescaleStore` directly (not `TimeseriesStore`), `TradingPipelineSupervisor` (not stub predictors), and `LightGBMSignalClassifier` with rule fallback.

---

## TIER 1 — Live pipeline blockers

*System cannot make meaningful ML-driven trade decisions without these.*

| File | Was | Target | Status | Why it matters |
|------|-----|--------|--------|----------------|
| `ml/models/reversal_predictor.py` | `return 0.5` | LightGBM inference | **STUB** | Core P(reversal) — every trade decision starts here |
| `ml/models/chop_detector.py` | `return 0.0` | Choppiness Index + ADX | **STUB** | Prevents trading in sideways markets |
| `ml/models/continuation_predictor.py` | `return 0.5` | EMA + RSI + volume | **STUB** | Distinguishes trend continuation from reversal setups |
| `ml/features/feature_pipeline.py` | passthrough | Full feature computation | **STUB** | Train/serve consistency — same features in training and live |

**Note:** Live worker currently uses inline `_run_prediction` in `pipeline/trading_supervisor.py` and `ml/models/lightgbm_classifier.py`. Tier 1 unifies the dedicated predictor modules with the same feature vector used in `train_reversal_models.py`.

---

## TIER 2 — Data layer (priority)

*System reads/writes no data through the facade layer without these. Bars and features can be lost.*

| File | Was | Target | Status | Pipeline connection |
|------|-----|--------|--------|---------------------|
| `data/storage/timeseries_store.py` | `write()→pass`, `read()→[]` | Facade over `TimescaleStore` | **PARTIAL** | Implemented; **ChartWatchRunner still uses `TimescaleStore` directly**; live bars not auto-persisted on each completed bar |
| `data/storage/feature_store.py` | `get()→None`, `set()→pass` | In-memory TTL feature cache | **PARTIAL** | Implemented; **not called from `TradingPipelineSupervisor`** |
| `data/loaders/tick_data_loader.py` | `yield from ()` | Polygon REST/WebSocket tick stream | **PARTIAL** | Implemented; **not used by `ChartWatchRunner`** (live mode polls prev bar via `PolygonBrokerAdapter`) |
| `data/processors/tick_aggregator.py` | `return []` | OHLCV assembly from ticks | **PARTIAL** | Implemented; **not wired** to `BarAssembler.on_tick()` |

### What already works (bypassing Tier 2 facades)

| Module | Role |
|--------|------|
| `data/storage/timescale_store.py` | **DONE** — backfill, CSV import, replay, observations, news |
| `agents/market_data_agent.py` | **DONE** — upserts OHLCV on API/CLI path |
| `chart_watcher/chart_watch_runner.py` | **DONE** — live poll + replay; **does not persist live 1m bars to DB** |
| `ml/features/level_history.py` | **DONE** — support/resistance discovery for training |
| `ml/training/train_reversal_models.py` | **DONE** — two-phase level + LightGBM training |

### Tier 2 wiring checklist (when implementing)

- [ ] `ChartWatchRunner._on_bar_complete` → `TimeseriesStore.write()` for `1m` bars  
- [ ] `TradingPipelineSupervisor.on_new_bar` → `get_feature_store().set()` after fusion  
- [ ] Optional tick mode: `TickDataLoader.stream` → `BarAssembler.on_tick` → same persist path  
- [ ] Training reads via `TimeseriesStore.read_df()` for consistency with live writes  

### Env vars (Tier 2)

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | — | Postgres/Timescale (required for persistence) |
| `FEATURE_CACHE_TTL_SECONDS` | `300` | Feature cache expiry |
| `FEATURE_CACHE_MAX_ENTRIES` | `2000` | Max cached feature sets |
| `TICK_POLL_INTERVAL_SECONDS` | `1.0` | REST tick poll interval |
| `TICK_STREAM_MODE` | `rest` | `rest` or `websocket` |

---

## TIER 3 — Risk layer

| File | Was | Target | Status | Pipeline connection |
|------|-----|--------|--------|---------------------|
| `risk/correlation_checker.py` | hardcoded `0.5` | Real correlation matrix | **STUB** | Prevents holding ES + MES + NQ simultaneously |
| `ml/training/trainer.py` | `Trainer.train()` is `pass` | Wraps `train_reversal_models` | **STUB** | Called by model auto-promoter after enough paper trades |

**Note:** `risk/risk_engine.py` is **DONE** and used in production. Tier 3 fills gaps around correlation and unified trainer entry.

---

## TIER 4 — ML feature extractors

All eight files return `layer_output` unchanged. Each represents a distinct pattern method that should feed Confluence and LightGBM.

| File | Method | Adds to model |
|------|--------|---------------|
| `ml/features/candlestick_features.py` | Doji, hammer, engulfing | Candle pattern recognition |
| `ml/features/fibonacci_features.py` | Fib retracement levels | Price target levels |
| `ml/features/fractal_features.py` | Williams fractal detection | Local swing identification |
| `ml/features/gann_features.py` | Gann square/angle levels | Time+price geometric levels |
| `ml/features/harmonic_features.py` | ABCD, Bat, Gartley | Complex reversal patterns |
| `ml/features/elliott_features.py` | Wave count estimation | Trend structure context |
| `ml/features/markov_features.py` | State transition probabilities | Market regime detection |
| `ml/features/number_theory_features.py` | 3/6/9 price levels | Number theory zones |

**Status:** all **STUB**. Method agents in `agents/method_agents/` already compute rich outputs; Tier 4 bridges those into `feature_vector.py` for ML training parity.

---

## TIER 5 — Validation

*Ensures models are trustworthy before promotion.*

| File | Was | Target | Status |
|------|-----|--------|--------|
| `ml/evaluation/backtest_evaluator.py` | docstring only | Full backtest runner | **STUB** |
| `ml/evaluation/monte_carlo_validator.py` | docstring only | Monte Carlo simulation | **STUB** |
| `validation/walk_forward_tester.py` | docstring only | Walk-forward optimization | **STUB** |
| `validation/edge_validator.py` | docstring only | Statistical edge verification | **STUB** |
| `validation/backtest_engine.py` | docstring only | P&L simulation with slippage | **STUB** |

**Partial:** `ml/promotion/promotion_policy.py`, `validation/method_isolation/` — used in retrain path.

---

## TIER 6 — MCTS support

`TradePlanningAgent` uses MCTS/Expectimax/Beam. These internal nodes are stubs.

| File | Was | Target | MCTS role | Status |
|------|-----|--------|-----------|--------|
| `mcts/tree_node.py` | docstring only | Node with state/action/value | Search tree node | **STUB** |
| `mcts/state_evaluator.py` | docstring only | State scoring function | Position quality | **STUB** |
| `mcts/policy_network.py` | docstring only | Prior over actions | Search guidance | **STUB** |

**Done:** `mcts/mcts_planner.py`, `mcts/beam_search_planner.py`, `mcts/trade_planning_agent.py`.

---

## TIER 7 — Symbol intelligence

| File | Was | Target | Status |
|------|-----|--------|--------|
| `engines/symbol_intelligence/session_analyzer.py` | `return "RTH"` | Real session detection | **STUB** |
| `engines/symbol_intelligence/liquidity_profiler.py` | hardcoded windows | Volume profile analysis | **STUB** |
| `engines/symbol_intelligence/symbol_profile_service.py` | empty profile | Per-symbol characteristics | **STUB** |
| `engines/wave/fractal_service.py` | hardcoded false | Real fractal detection | **STUB** |
| `engines/wave/impulse_correction_classifier.py` | `return "unknown"` | Wave structure classifier | **STUB** |
| `engines/market_state/hidden_state_detector.py` | `return "unknown"` | HMM regime detection | **STUB** |
| `engines/market_state/markov_chain_service.py` | `return "range", 0.5` | State transition matrix | **STUB** |

**Done:** `chart_watcher/session_scheduler.py` for session open/closed; method agents use real engines under `engines/`.

---

## TIER 8 — Live execution (paper → live)

| File | Was | Target | Status |
|------|-----|--------|--------|
| `live/execution_monitor.py` | docstring only | Order fill tracking | **STUB** |
| `live/order_router.py` | docstring only | Broker order routing | **STUB** |

**Done:** `live/broker_adapter.py` (market data), `paper_trading/`, `live/coinbase_executor.py`, `live/oanda_executor.py`.

---

## TIER 9 — Dashboard (Streamlit visibility)

All seven files are docstring-only placeholders.

| File | Shows |
|------|-------|
| `dashboard/pages/live_signals.py` | Current signals across 23 symbols |
| `dashboard/pages/backtest_results.py` | Backtest P&L curves |
| `dashboard/pages/research_lab.py` | Model training results |
| `dashboard/components/signal_table.py` | Active trade signals table |
| `dashboard/components/risk_panel.py` | Exposure and drawdown |
| `dashboard/components/pattern_chart.py` | Candlestick chart with overlays |

**Done:** React frontend (`frontend/`) — trades dashboard, system status, news toggle.

---

## TIER 10 — Research (future, not required for trading)

| File | Purpose | Status |
|------|---------|--------|
| `research/experiment_tracker.py` | MLflow-style experiment logging | **STUB** |
| `research/pattern_discovery.py` | Automated pattern mining | **STUB** |
| `research/gann_research_runner.py` | Gann analysis research | **STUB** |
| `research/number_theory_lab.py` | Number theory experiments | **STUB** |
| `ml/evaluation/pattern_edge_scorer.py` | Per-pattern edge scoring | **STUB** |
| `ml/evaluation/large_number_validator.py` | Large-sample statistical tests | **STUB** |
| `ml/training/augmentation.py` | Training data augmentation | **STUB** |
| `ml/training/data_splitter.py` | Advanced train/val splitting | **STUB** |
| `ml/training/loss_functions.py` | Custom loss functions | **STUB** |

---

## Stub inventory summary

| Category | Count | Notes |
|----------|------:|-------|
| Functional stubs (noop / hardcoded / passthrough) | ~59 | See tiers 1–8 |
| Package `__init__.py` markers (1-line) | 32 | Normal for Python packages |
| Short non-stub files (<10 lines, real logic) | ~38 | e.g. `engines/strategy_math/ev_calculator.py` |
| **Tier 2 partial (code exists, unwired)** | 4 | Priority wiring target |

---

## Related documentation

- [architecture.md](./architecture.md) — agent hierarchy and production path  
- [backtesting_methodology.md](./backtesting_methodology.md) — validation approach  
- [signal_rank_scoring.md](./signal_rank_scoring.md) — SignalRank 0–100  
- Level / cross-symbol training: `ml/features/level_history.py`, `ml/features/cross_symbol_analysis.py`, `ml/training/train_reversal_models.py`  

---

## Chat / analytics layer (planned, not in tiers above)

Natural-language queries over bar and level data (e.g. “TSLA strongest resistance over 2 years, how many touches?”) require:

1. `GET /analytics/{symbol}/levels` — wraps `LevelHistoryTracker`  
2. Frontend `ChatPanel` — structured results + optional LLM formatting  
3. Tool-calling agent — LLM routes to deterministic analytics, never invents numbers  

See conversation design: analytics service → API → chat UI → ML export via `train_reversal_models`.
