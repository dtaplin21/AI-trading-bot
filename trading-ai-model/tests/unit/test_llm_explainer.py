"""Tests for LLM explainer template fallback."""

from datetime import datetime, timezone

from agents.llm_explainer import LLMExplainer
from agents.schemas import AuditReport, PipelineDecision, PredictionOutput


def test_template_explanation_without_api_key(monkeypatch):
    monkeypatch.setenv("LLM_ENABLED", "false")
    monkeypatch.setenv("LLM_API_KEY", "")
    from config.settings import get_settings

    get_settings.cache_clear()

    explainer = LLMExplainer()
    assert explainer.enabled is False

    audit = AuditReport(
        summary="The system marked a long candidate because:",
        reasons=["bullish rejection candle formed", "risk agent approved trade"],
    )
    decision = PipelineDecision(
        symbol="MES",
        timeframe="5m",
        timestamp=datetime.now(timezone.utc),
        prediction=PredictionOutput(model_confidence=0.72, model_version="rule_fallback"),
        signal_rank=84,
        status="paper_trade_candidate",
    )
    text = explainer.explain(decision, audit)
    assert "long candidate" in text
    assert "bullish rejection" in text
    assert "Model confidence" in text
