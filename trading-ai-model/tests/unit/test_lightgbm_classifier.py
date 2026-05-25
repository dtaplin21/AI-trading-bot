"""Tests for LightGBM classifier fallback."""

from ml.models.lightgbm_classifier import LightGBMSignalClassifier


def test_rule_fallback_when_no_model():
    clf = LightGBMSignalClassifier(model_path="/nonexistent/model.txt")
    assert clf.is_loaded is False
    out = clf.predict({"signal_rank": 80, "strategy_ev": 10, "markov_continuation_probability": 0.6})
    assert "signal_probability" in out
    assert out["model_type"] == "rules"


def test_high_rank_features_produce_higher_probability():
    clf = LightGBMSignalClassifier(model_path="/nonexistent/model.txt")
    high = clf.predict({"signal_rank": 90, "strategy_ev": 15, "markov_continuation_probability": 0.7})
    low = clf.predict({"signal_rank": 30, "strategy_ev": -5, "markov_continuation_probability": 0.3})
    assert high["signal_probability"] > low["signal_probability"]
