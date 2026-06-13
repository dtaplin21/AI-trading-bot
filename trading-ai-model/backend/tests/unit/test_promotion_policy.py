"""Tests for ml/promotion/promotion_policy.py gates."""

import json
from pathlib import Path
from typing import TypedDict

import pytest

from ml.promotion.promotion_policy import PromotionPolicy


class _PassingMetrics(TypedDict):
    n_samples: int
    holdout_brier: float
    production_brier: float
    holdout_auc: float
    positive_rate: float


@pytest.fixture
def policy(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_AUDIT_LOG", str(tmp_path / "promotions.jsonl"))
    monkeypatch.setenv("MODEL_AUTO_PROMOTE", "false")
    monkeypatch.setenv("MODEL_MIN_SAMPLES", "200")
    return PromotionPolicy()


def _passing_metrics() -> _PassingMetrics:
    return _PassingMetrics(
        n_samples=250,
        holdout_brier=0.20,
        production_brier=0.23,
        holdout_auc=0.60,
        positive_rate=0.48,
    )


def test_all_gates_pass_pending_manual(policy):
    decision = policy.evaluate(model_id="m1", **_passing_metrics())
    assert all(g.passed for g in decision.gate_results)
    assert decision.approved is False
    assert decision.promoted_by == "pending_manual"


def test_auto_promote_when_enabled(policy, monkeypatch):
    monkeypatch.setenv("MODEL_AUTO_PROMOTE", "true")
    p = PromotionPolicy()
    decision = p.evaluate(model_id="m2", **_passing_metrics())
    assert decision.approved is True
    assert decision.promoted_by == "auto_metrics"


def test_rejects_insufficient_samples(policy):
    decision = policy.evaluate(
        model_id="m3",
        n_samples=50,
        holdout_brier=0.20,
        production_brier=0.23,
        holdout_auc=0.60,
        positive_rate=0.48,
    )
    assert decision.approved is False
    assert decision.promoted_by == "rejected"
    assert not decision.gate_results[0].passed


def test_rejects_low_holdout_auc(policy):
    metrics = _passing_metrics()
    metrics["holdout_auc"] = 0.40
    decision = policy.evaluate(model_id="m4", **metrics)
    assert not any(g.gate_name == "holdout_auc_accuracy" and g.passed for g in decision.gate_results)


def test_manual_approve_writes_audit(policy, tmp_path):
    decision = policy.manual_approve("m5", "admin", notes="reviewed")
    assert decision.approved is True
    assert "manual:admin" in decision.promoted_by
    lines = (tmp_path / "promotions.jsonl").read_text().strip().split("\n")
    record = json.loads(lines[-1])
    assert record["model_id"] == "m5"


def test_audit_history(policy, tmp_path):
    policy.evaluate(model_id="a", **_passing_metrics())
    policy.evaluate(model_id="b", **_passing_metrics())
    history = policy.get_audit_history(last_n=5)
    assert len(history) == 2
    assert history[0]["model_id"] == "b"
