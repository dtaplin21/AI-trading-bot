"""
ml/training/train_reversal_models.py

Two-phase reversal probability training pipeline.

Phase 1 — Fit level history for ALL symbols first
  Discovers natural price levels per symbol (no round numbers — market-derived)
  Runs CrossSymbolAnalyzer to find what's universally true across all symbols

Phase 2 — Train one model per symbol
  Technical features (RSI, MACD, Bollinger, ATR, volume, time)
  Level history features (nearest level hold rate, touch count, strength)
  Cross-symbol features (universal score, correlated pair confirmation)
  LightGBM classifier → outputs P(reversal) 0.0-1.0

Why two phases:
  Cross-symbol analysis requires ALL trackers to be fitted before
  any single symbol's training begins. You can't score EURUSD's levels
  against a global profile until you've analyzed all 23 symbols.

Run:
  python -m ml.training.train_reversal_models
  python -m ml.training.train_reversal_models --symbols EURUSD --dry-run
  python -m ml.training.train_reversal_models --symbols MES,ES,NQ,MNQ
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import pickle
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from config.symbols import SYMBOLS

warnings.filterwarnings("ignore")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("train_reversal")


# ─── Asset class config ───────────────────────────────────────────────────────


@dataclass
class AssetConfig:
    forward_window: int
    min_move_pct: float
    prior_trend_bars: int
    min_trend_pct: float
    atr_multiplier: float


ASSET_CONFIGS = {
    "futures": AssetConfig(
        forward_window=20,
        min_move_pct=0.20,
        prior_trend_bars=10,
        min_trend_pct=0.30,
        atr_multiplier=1.5,
    ),
    "forex": AssetConfig(
        forward_window=20,
        min_move_pct=0.10,
        prior_trend_bars=10,
        min_trend_pct=0.15,
        atr_multiplier=1.2,
    ),
    "crypto": AssetConfig(
        forward_window=12,
        min_move_pct=0.30,
        prior_trend_bars=8,
        min_trend_pct=0.50,
        atr_multiplier=2.0,
    ),
    "equity": AssetConfig(
        forward_window=16,
        min_move_pct=0.25,
        prior_trend_bars=10,
        min_trend_pct=0.35,
        atr_multiplier=1.5,
    ),
}

SYMBOL_ASSET_CLASS = {sym: spec.asset_class for sym, spec in SYMBOLS.items()}
ALL_SYMBOLS = list(SYMBOL_ASSET_CLASS.keys())


# ─── Database ─────────────────────────────────────────────────────────────────


def load_bars(symbol: str, start: str, end: str) -> pd.DataFrame:
    from data.storage.timeseries_store import TimeseriesStore

    df = TimeseriesStore().read(symbol, "1m", start=start, end=end)
    if df.empty:
        return df

    df_5m = (
        df.resample("5min")
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        .dropna()
    )

    logger.info("%s: %d 1m bars → %d 5m bars", symbol, len(df), len(df_5m))
    return df_5m


# ─── Technical features ───────────────────────────────────────────────────────


def compute_rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def compute_atr(df: pd.DataFrame, period: int) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    return pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(period).mean()


def compute_macd(
    series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    ef = series.ewm(span=fast, adjust=False).mean()
    es = series.ewm(span=slow, adjust=False).mean()
    ml = ef - es
    sl = ml.ewm(span=signal, adjust=False).mean()
    return ml, sl, ml - sl


def compute_bollinger(
    series: pd.Series, period: int = 20, std: float = 2.0
) -> tuple[pd.Series, pd.Series]:
    sma = series.rolling(period).mean()
    sd = series.rolling(period).std()
    upper = sma + std * sd
    lower = sma - std * sd
    pos = (series - lower) / (upper - lower + 1e-10)
    width = (upper - lower) / sma
    return pos, width


def compute_technical_features(df: pd.DataFrame) -> pd.DataFrame:
    feat = pd.DataFrame(index=df.index)
    close = df["close"]

    feat["rsi_7"] = compute_rsi(close, 7)
    feat["rsi_14"] = compute_rsi(close, 14)
    feat["rsi_21"] = compute_rsi(close, 21)

    ml, sl, hist = compute_macd(close)
    feat["macd_line"] = ml
    feat["macd_signal"] = sl
    feat["macd_histogram"] = hist
    feat["macd_cross"] = (ml > sl).astype(int)

    feat["bb_position"], feat["bb_width"] = compute_bollinger(close)
    feat["atr_14"] = compute_atr(df, 14)
    feat["atr_pct"] = feat["atr_14"] / close

    feat["body_size"] = (df["close"] - df["open"]).abs() / (feat["atr_14"] + 1e-10)
    feat["upper_wick"] = (df["high"] - df[["open", "close"]].max(axis=1)) / (
        feat["atr_14"] + 1e-10
    )
    feat["lower_wick"] = (df[["open", "close"]].min(axis=1) - df["low"]) / (
        feat["atr_14"] + 1e-10
    )
    feat["is_bullish"] = (df["close"] > df["open"]).astype(int)

    for n in [5, 10, 20]:
        feat[f"return_{n}"] = close.pct_change(n) * 100
        feat[f"high_{n}"] = df["high"].rolling(n).max()
        feat[f"low_{n}"] = df["low"].rolling(n).min()

    feat["range_pos_20"] = (close - feat["low_20"]) / (
        feat["high_20"] - feat["low_20"] + 1e-10
    )
    feat["ema_20"] = close.ewm(span=20).mean()
    feat["ema_50"] = close.ewm(span=50).mean()
    feat["price_vs_ema20"] = (close - feat["ema_20"]) / (feat["ema_20"] + 1e-10) * 100
    feat["price_vs_ema50"] = (close - feat["ema_50"]) / (feat["ema_50"] + 1e-10) * 100
    feat["ema_cross"] = (feat["ema_20"] > feat["ema_50"]).astype(int)
    feat["momentum"] = close.diff(3)

    feat["rsi_div_bull"] = (
        (close < close.shift(5)) & (feat["rsi_14"] > feat["rsi_14"].shift(5))
    ).astype(int)
    feat["rsi_div_bear"] = (
        (close > close.shift(5)) & (feat["rsi_14"] < feat["rsi_14"].shift(5))
    ).astype(int)

    vol_ma = df["volume"].rolling(20).mean()
    feat["volume_ratio"] = df["volume"] / (vol_ma + 1e-10)
    feat["volume_trend"] = df["volume"].pct_change(5)
    feat["volume_spike"] = (feat["volume_ratio"] > 2.0).astype(int)

    feat["hour"] = df.index.hour
    feat["day_of_week"] = df.index.dayofweek
    feat["london_session"] = ((feat["hour"] >= 7) & (feat["hour"] < 16)).astype(int)
    feat["ny_session"] = ((feat["hour"] >= 13) & (feat["hour"] < 21)).astype(int)
    feat["asia_session"] = ((feat["hour"] >= 0) & (feat["hour"] < 8)).astype(int)
    feat["session_overlap"] = (feat["london_session"] & feat["ny_session"]).astype(int)

    feat["extreme_oversold"] = (feat["rsi_14"] < 25).astype(int)
    feat["oversold"] = (feat["rsi_14"] < 35).astype(int)
    feat["overbought"] = (feat["rsi_14"] > 65).astype(int)
    feat["extreme_overbought"] = (feat["rsi_14"] > 75).astype(int)

    return feat


# ─── Cross-symbol features per candle ────────────────────────────────────────


def compute_cross_symbol_features(
    df: pd.DataFrame,
    symbol: str,
    tracker,
    analyzer,
    all_trackers: dict,
) -> pd.DataFrame:
    """Per-candle cross-symbol features from nearest level + global profile."""
    rows = []
    for price in df["close"]:
        price_f = float(price)

        if tracker.levels:
            distances = [
                abs(l.price - price_f) / (price_f + 1e-10) for l in tracker.levels
            ]
            nearest = tracker.levels[int(np.argmin(distances))]
            hold_rate = nearest.hold_rate
            touch_count = nearest.touch_count
            strength = nearest.strength_score
        else:
            hold_rate = 0.0
            touch_count = 0
            strength = 0.0

        cx = analyzer.get_cross_symbol_features(
            symbol=symbol,
            hold_rate=hold_rate,
            touch_count=int(touch_count),
            strength=strength,
        )
        pair = analyzer.get_correlated_pair_feature(
            symbol=symbol,
            current_price=price_f,
            all_trackers=all_trackers,
        )
        rows.append({**cx, **pair})

    return pd.DataFrame(rows, index=df.index)


# ─── Labeling ─────────────────────────────────────────────────────────────────


def label_reversals(df: pd.DataFrame, cfg: AssetConfig) -> pd.Series:
    close = df["close"]
    n = len(close)
    fw = cfg.forward_window
    pb = cfg.prior_trend_bars
    labels = np.full(n, np.nan)

    for i in range(pb, n - fw):
        prior_high = df["high"].iloc[i - pb : i].max()
        prior_low = df["low"].iloc[i - pb : i].min()
        current = float(close.iloc[i])

        prior_down = (prior_high - current) / (prior_high + 1e-10) * 100
        prior_up = (current - prior_low) / (prior_low + 1e-10) * 100

        future_high = df["high"].iloc[i + 1 : i + fw + 1].max()
        future_low = df["low"].iloc[i + 1 : i + fw + 1].min()

        up_move = (future_high - current) / (current + 1e-10) * 100
        down_move = (current - future_low) / (current + 1e-10) * 100

        if prior_down >= cfg.min_trend_pct:
            labels[i] = 1 if up_move >= cfg.min_move_pct else 0
        elif prior_up >= cfg.min_trend_pct:
            labels[i] = 1 if down_move >= cfg.min_move_pct else 0

    return pd.Series(labels, index=df.index, name="label")


# ─── Model training ───────────────────────────────────────────────────────────


def train_model(X_train, y_train, X_val, y_val):
    import lightgbm as lgb
    from sklearn.metrics import brier_score_loss, roc_auc_score

    dtrain = lgb.Dataset(X_train, label=y_train)
    dval = lgb.Dataset(X_val, label=y_val, reference=dtrain)

    params = {
        "objective": "binary",
        "metric": ["binary_logloss", "auc"],
        "num_leaves": 63,
        "max_depth": 7,
        "learning_rate": 0.05,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "min_child_samples": 50,
        "lambda_l1": 0.1,
        "lambda_l2": 0.1,
        "verbose": -1,
        "n_jobs": -1,
        "seed": 42,
    }

    model = lgb.train(
        params,
        dtrain,
        num_boost_round=1000,
        valid_sets=[dtrain, dval],
        valid_names=["train", "val"],
        callbacks=[
            lgb.early_stopping(50, verbose=False),
            lgb.log_evaluation(100),
        ],
    )

    val_preds = np.asarray(model.predict(X_val)).ravel()
    auc = roc_auc_score(y_val, val_preds)
    brier = brier_score_loss(y_val, val_preds)

    importance = dict(
        zip(
            model.feature_name(),
            model.feature_importance(importance_type="gain"),
        )
    )
    top_features = sorted(importance.items(), key=lambda x: x[1], reverse=True)[:20]

    level_ranks = [
        (r + 1, n, round(float(s), 2))
        for r, (n, s) in enumerate(top_features)
        if n.startswith("level_") or n.startswith("cx_")
    ]

    metrics = {
        "auc": round(auc, 4),
        "brier_score": round(brier, 4),
        "base_rate": round(float(y_val.mean()), 4),
        "n_train": len(y_train),
        "n_val": len(y_val),
        "best_iteration": model.best_iteration,
        "top_20_features": [(f, round(float(s), 2)) for f, s in top_features],
        "level_and_cx_ranks": level_ranks,
    }

    return model, metrics


# ─── Save ─────────────────────────────────────────────────────────────────────


def save_model(
    model,
    tracker,
    analyzer,
    metrics: dict,
    symbol: str,
    feature_cols: list[str],
    cfg: AssetConfig,
    model_dir: Path,
) -> None:
    sym_dir = model_dir / symbol
    sym_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")

    model_path = sym_dir / f"reversal_model_{timestamp}.pkl"
    levels_path = sym_dir / f"levels_{timestamp}.json"

    with open(model_path, "wb") as f:
        pickle.dump(model, f)

    tracker.save(str(levels_path))

    meta = {
        "symbol": symbol,
        "asset_class": SYMBOL_ASSET_CLASS.get(symbol, "unknown"),
        "trained_at": timestamp,
        "model_path": str(model_path),
        "levels_path": str(levels_path),
        "feature_cols": feature_cols,
        "n_features": len(feature_cols),
        "metrics": metrics,
        "config": {
            "forward_window": cfg.forward_window,
            "min_move_pct": cfg.min_move_pct,
            "prior_trend_bars": cfg.prior_trend_bars,
            "min_trend_pct": cfg.min_trend_pct,
        },
        "level_summary": {
            "total_levels": len(tracker.levels),
            "top_10_levels": [
                {
                    "price": round(l.price, 5),
                    "touches": l.touch_count,
                    "holds": l.hold_count,
                    "breaks": l.break_count,
                    "hold_rate": round(l.hold_rate * 100, 1),
                    "strength": round(l.strength_score, 3),
                    "classification": analyzer.profile.classify_level(
                        l.hold_rate, l.touch_count
                    )
                    if analyzer.profile
                    else "unknown",
                }
                for l in tracker.levels[:10]
            ],
        },
        "cross_symbol": {
            "strong_threshold": analyzer.profile.strong_hold_rate_threshold
            if analyzer.profile
            else None,
            "global_mean_hold": analyzer.profile.mean_hold_rate
            if analyzer.profile
            else None,
            "n_symbols_analyzed": analyzer.profile.n_symbols
            if analyzer.profile
            else 0,
        },
    }

    (sym_dir / f"meta_{timestamp}.json").write_text(json.dumps(meta, indent=2))
    (sym_dir / "latest.json").write_text(json.dumps(meta, indent=2))

    logger.info(
        "%s: ✓ saved | AUC=%.4f | Brier=%.4f | Base=%.1f%% | "
        "%d levels | %d features",
        symbol,
        metrics["auc"],
        metrics["brier_score"],
        metrics["base_rate"] * 100,
        len(tracker.levels),
        len(feature_cols),
    )

    if metrics["level_and_cx_ranks"]:
        logger.info(
            "%s: level/cx features in top 20 → %s",
            symbol,
            ", ".join(f"{n}(#{r})" for r, n, _ in metrics["level_and_cx_ranks"][:5]),
        )


# ─── Phase 1 — fit all level trackers ─────────────────────────────────────────


def fit_all_trackers(symbols: list[str], bars: dict[str, pd.DataFrame], model_dir: Path) -> dict:
    """Fit LevelHistoryTracker for every symbol. Returns {symbol: tracker}."""
    from ml.features.level_history import LevelHistoryTracker

    trackers = {}
    for symbol in symbols:
        df = bars.get(symbol)
        if df is None or df.empty:
            logger.warning("%s: no bars — skipping level fit", symbol)
            continue

        asset_class = SYMBOL_ASSET_CLASS.get(symbol, "equity")
        tracker = LevelHistoryTracker(symbol=symbol, asset_class=asset_class)
        tracker.fit(df)

        levels_dir = model_dir / symbol
        levels_dir.mkdir(parents=True, exist_ok=True)
        tracker.save(str(levels_dir / "levels_latest.json"))

        trackers[symbol] = tracker

    logger.info("Phase 1 complete — fitted %d level trackers", len(trackers))
    return trackers


# ─── Phase 2 — cross-symbol analysis ──────────────────────────────────────────


def run_cross_symbol_analysis(trackers: dict, model_dir: Path):
    """Run CrossSymbolAnalyzer across all fitted trackers."""
    from ml.features.cross_symbol_analysis import CrossSymbolAnalyzer

    analyzer = CrossSymbolAnalyzer()
    analyzer.fit(trackers)
    analyzer.print_summary()
    analyzer.save(str(model_dir / "cross_symbol_profile.json"))
    return analyzer


# ─── Phase 3 — train one model per symbol ─────────────────────────────────────


def train_symbol(
    symbol: str,
    df: pd.DataFrame,
    tracker,
    analyzer,
    all_trackers: dict,
    val_start: str,
    model_dir: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    asset_class = SYMBOL_ASSET_CLASS.get(symbol, "equity")
    cfg = ASSET_CONFIGS[asset_class]

    logger.info("─" * 65)
    logger.info("Training %s (%s)", symbol, asset_class)

    if df.empty or len(df) < 1000:
        return {"symbol": symbol, "status": "skipped", "reason": "insufficient_data"}

    if dry_run:
        tracker.print_top_levels(15)
        return {
            "symbol": symbol,
            "status": "dry_run",
            "n_bars": len(df),
            "n_levels": len(tracker.levels),
            "top_level": {
                "price": round(tracker.levels[0].price, 5) if tracker.levels else None,
                "touches": tracker.levels[0].touch_count if tracker.levels else 0,
                "hold_rate": round(tracker.levels[0].hold_rate * 100, 1)
                if tracker.levels
                else 0,
                "universal_class": analyzer.profile.classify_level(
                    tracker.levels[0].hold_rate,
                    tracker.levels[0].touch_count,
                )
                if tracker.levels and analyzer.profile
                else "—",
            },
        }

    logger.info("%s: computing technical features...", symbol)
    tech_feat = compute_technical_features(df)

    logger.info("%s: computing level history features...", symbol)
    level_feat = tracker.get_features_series(df)

    logger.info("%s: computing cross-symbol features...", symbol)
    cx_feat = compute_cross_symbol_features(df, symbol, tracker, analyzer, all_trackers)

    logger.info("%s: labeling reversals...", symbol)
    labels = label_reversals(df, cfg)

    data = pd.concat([tech_feat, level_feat, cx_feat, labels], axis=1).dropna()

    if len(data) < 500:
        return {"symbol": symbol, "status": "skipped", "reason": "insufficient_labels"}

    base_rate = data["label"].mean()
    feature_cols = [c for c in data.columns if c != "label"]

    logger.info(
        "%s: %d samples | %.1f%% reversals | %d features "
        "(%d level, %d cx, %d technical)",
        symbol,
        len(data),
        base_rate * 100,
        len(feature_cols),
        sum(1 for c in feature_cols if c.startswith("level_")),
        sum(1 for c in feature_cols if c.startswith("cx_")),
        sum(1 for c in feature_cols if not c.startswith(("level_", "cx_"))),
    )

    val_ts = pd.Timestamp(val_start, tz="UTC")
    train_data = data[data.index < val_ts]
    val_data = data[data.index >= val_ts]

    if len(train_data) < 300 or len(val_data) < 100:
        return {"symbol": symbol, "status": "skipped", "reason": "split_too_small"}

    X_train, y_train = train_data[feature_cols], train_data["label"]
    X_val, y_val = val_data[feature_cols], val_data["label"]

    logger.info(
        "%s: training LightGBM (%d train / %d val)...",
        symbol,
        len(y_train),
        len(y_val),
    )
    model, metrics = train_model(X_train, y_train, X_val, y_val)

    if metrics["auc"] < 0.55:
        logger.warning("%s: AUC %.4f < 0.55 — not saved", symbol, metrics["auc"])
        return {"symbol": symbol, "status": "failed_gate", **metrics}

    save_model(
        model, tracker, analyzer, metrics, symbol, feature_cols, cfg, model_dir
    )

    return {"symbol": symbol, "status": "success", **metrics}


# ─── Entry point ─────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train per-symbol reversal probability models with cross-symbol analysis"
    )
    parser.add_argument("--symbols", default=",".join(ALL_SYMBOLS))
    parser.add_argument("--start", default="2025-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--val-start", default="2025-11-01")
    parser.add_argument("--model-dir", default="models/reversal")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show top levels per symbol — no training",
    )
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    model_dir = Path(args.model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 65)
    logger.info("Reversal Model Training — %d symbols", len(symbols))
    logger.info(
        "Train: %s → %s | Val: %s → %s",
        args.start,
        args.val_start,
        args.val_start,
        args.end,
    )
    logger.info("=" * 65)

    logger.info("PHASE 1: Loading bars and fitting level trackers...")
    bars: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        try:
            bars[symbol] = load_bars(symbol, args.start, args.end)
        except Exception as e:
            logger.error("%s: failed to load bars: %s", symbol, e)
            bars[symbol] = pd.DataFrame()

    trackers = fit_all_trackers(symbols, bars, model_dir)

    logger.info("PHASE 2: Cross-symbol analysis...")
    analyzer = run_cross_symbol_analysis(trackers, model_dir)

    logger.info("PHASE 3: Training models...")
    results: list[dict[str, Any]] = []
    for symbol in symbols:
        tracker = trackers.get(symbol)
        df = bars.get(symbol, pd.DataFrame())

        if tracker is None:
            results.append(
                {"symbol": symbol, "status": "skipped", "reason": "no_tracker"}
            )
            continue

        try:
            result = train_symbol(
                symbol=symbol,
                df=df,
                tracker=tracker,
                analyzer=analyzer,
                all_trackers=trackers,
                val_start=args.val_start,
                model_dir=model_dir,
                dry_run=args.dry_run,
            )
            results.append(result)
        except Exception as e:
            logger.error("%s: training failed: %s", symbol, e, exc_info=True)
            results.append({"symbol": symbol, "status": "error", "error": str(e)})

    n_success = sum(1 for r in results if r["status"] == "success")
    print(f"\n{'=' * 82}")
    print(f"  Reversal Training Complete — {n_success}/{len(symbols)} models saved")
    print(f"{'=' * 82}")
    print(
        f"  {'Symbol':<10} {'Status':<14} {'AUC':>7} {'Brier':>7} "
        f"{'Base%':>7} {'Levels':>8} {'Feats':>6} {'Train':>9}"
    )
    print(f"  {'-' * 78}")
    for r in sorted(results, key=lambda x: x.get("auc", 0), reverse=True):
        icon = (
            "✓"
            if r["status"] == "success"
            else "✗"
            if r["status"] in ("error", "failed_gate")
            else "·"
        )
        auc = f"{r['auc']:.4f}" if "auc" in r else "—"
        brier = f"{r['brier_score']:.4f}" if "brier_score" in r else "—"
        base = f"{r['base_rate'] * 100:.1f}%" if "base_rate" in r else "—"
        levels = str(r.get("n_levels", "—"))
        feats = str(r.get("n_features", "—"))
        samples = f"{r.get('n_train', 0):,}" if "n_train" in r else "—"
        print(
            f"  {icon} {r['symbol']:<10} {r['status']:<14} {auc:>7} "
            f"{brier:>7} {base:>7} {levels:>8} {feats:>6} {samples:>9}"
        )
    print(f"{'=' * 82}\n")

    (model_dir / "training_results.json").write_text(
        json.dumps(results, indent=2, default=str)
    )
    logger.info("Done. Results → %s/training_results.json", model_dir)


if __name__ == "__main__":
    main()
