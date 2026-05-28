"""LLM explanation layer — human-readable reasoning only, never execution."""

from __future__ import annotations

import json
import logging

from agents.schemas import AuditReport, PipelineDecision
from llm.anthropic_client import AnthropicClient, get_anthropic_client

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an audit assistant for a multi-agent futures trading system.
Explain the signal decision in plain English for a human trader.
Rules:
- NEVER recommend placing, modifying, or canceling orders
- NEVER override the risk engine
- Only explain what the deterministic system already decided
- Reference the method confluence and risk verdict
- Keep response under 200 words"""


class LLMExplainer:
    """Optional Anthropic layer for explainability. Disabled without API key."""

    def __init__(self, client: AnthropicClient | None = None) -> None:
        self._client = client or get_anthropic_client()

    @property
    def enabled(self) -> bool:
        return self._client.is_configured

    def explain(self, decision: PipelineDecision, audit: AuditReport) -> str:
        template = self._template_explanation(decision, audit)
        if not self.enabled:
            return template
        try:
            return self._call_llm(decision, audit, template)
        except Exception as exc:
            logger.warning("LLM explanation failed: %s", exc)
            return template + "\n\n[LLM enhancement unavailable — showing template explanation]"

    def _template_explanation(self, decision: PipelineDecision, audit: AuditReport) -> str:
        lines = [audit.summary, ""]
        lines.extend(f"• {r}" for r in audit.reasons)
        if audit.disagreements:
            lines.append("")
            lines.append("Method concerns:")
            lines.extend(f"• {d}" for d in audit.disagreements)
        if decision.risk:
            lines.append("")
            lines.append(
                f"Risk verdict: {'APPROVED' if decision.risk.approved else 'REJECTED'}"
                + (f" ({decision.risk.reason})" if decision.risk.reason else "")
            )
        if decision.prediction:
            lines.append(
                f"Model confidence: {decision.prediction.model_confidence:.0%} "
                f"({decision.prediction.model_version})"
            )
        return "\n".join(lines)

    def _call_llm(self, decision: PipelineDecision, audit: AuditReport, fallback: str) -> str:
        user_content = json.dumps(
            {
                "symbol": decision.symbol,
                "timeframe": decision.timeframe,
                "status": decision.status,
                "signal_rank": decision.signal_rank,
                "audit_summary": audit.summary,
                "audit_reasons": audit.reasons,
                "method_agreement": audit.method_agreement,
                "disagreements": audit.disagreements,
                "risk_approved": decision.risk.approved if decision.risk else False,
                "prediction": decision.prediction.model_dump() if decision.prediction else None,
            },
            default=str,
        )
        return self._client.complete_sync(
            system=SYSTEM_PROMPT,
            user=f"Explain this trading pipeline decision:\n{user_content}",
            max_tokens=350,
            temperature=0.3,
        )
