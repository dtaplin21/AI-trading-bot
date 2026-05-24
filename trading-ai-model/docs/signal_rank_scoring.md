# SignalRank Scoring

SignalRank (0–100) is a weighted sum of normalized layer confirmations plus optional Gann modifier.

## Default Weights

| Layer | Weight |
|-------|--------|
| Candlestick | 0.15 |
| Harmonic | 0.15 |
| Elliott | 0.10 |
| Fibonacci | 0.10 |
| Number Zone | 0.10 |
| Markov | 0.12 |
| ML | 0.10 |
| EV | 0.08 |
| Fractal | 0.07 |
| Gann | ± modifier only |

## Status Thresholds

| Rank | Status |
|------|--------|
| ≥ 75 + risk approved | `paper_trade_candidate` |
| ≥ 50 + risk approved | `watch` |
| < 50 or risk rejected | `rejected` |

Weights are configured in `config/model_weights.py`.
