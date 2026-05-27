"""LLM explanation layer — human-readable reasoning only, never execution."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from agents.schemas import AuditReport, PipelineDecision
from config.settings import get_settings

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
    """Optional LLM layer for explainability. Disabled without API key."""

    def __init__(self):
        settings = get_settings()
        self.enabled = settings.llm_enabled and bool(settings.llm_api_key)
        self.api_key = settings.llm_api_key
        self.model = settings.llm_model
        self.base_url = settings.llm_base_url.rstrip("/")

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

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Explain this trading pipeline decision:\n{user_content}"},
            ],
            "max_tokens": 350,
            "temperature": 0.3,
        }

        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
        return body["choices"][0]["message"]["content"].strip()
