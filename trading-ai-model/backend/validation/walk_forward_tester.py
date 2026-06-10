"""validation/walk_forward_tester.py"""
from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd


class WalkForwardTester:
    """
    Tests model stability across multiple time windows.
    If AUC degrades significantly in later windows = overfitting.
    """

    def __init__(self, n_splits: int = 5, min_auc: float = 0.52):
        self.n_splits = n_splits
        self.min_auc = min_auc

    def test(self, X: pd.DataFrame, y: pd.Series, model: Any) -> dict:
        """
        Walk-forward test: train on earlier data, test on later data.
        Repeat n_splits times advancing the window each time.
        """
        from sklearn.metrics import roc_auc_score

        n = len(X)
        split_size = n // (self.n_splits + 1)

        results: list[dict] = []
        for i in range(self.n_splits):
            train_end = split_size * (i + 1)
            test_end = min(train_end + split_size, n)

            X_train = X.iloc[:train_end]
            y_train = y.iloc[:train_end]
            X_test = X.iloc[train_end:test_end]
            y_test = y.iloc[train_end:test_end]

            if len(X_test) < 50:
                continue

            if hasattr(model, "fit"):
                model.fit(X_train.values, y_train.values)
            preds = model.predict(X_test.values)
            auc = roc_auc_score(y_test, preds)
            results.append(
                {
                    "fold": i + 1,
                    "train_size": len(X_train),
                    "test_size": len(X_test),
                    "auc": round(float(auc), 4),
                    "passes_gate": auc >= self.min_auc,
                }
            )

        if not results:
            return {"passed": False, "reason": "insufficient data"}

        aucs = [r["auc"] for r in results]
        passed = all(r["passes_gate"] for r in results)
        stable = (max(aucs) - min(aucs)) < 0.10

        return {
            "passed": passed and stable,
            "mean_auc": round(float(np.mean(aucs)), 4),
            "min_auc": round(float(min(aucs)), 4),
            "max_auc": round(float(max(aucs)), 4),
            "stability": round(float(max(aucs) - min(aucs)), 4),
            "is_stable": stable,
            "folds": results,
            "reason": "stable" if stable else "degrading AUC across folds",
        }
