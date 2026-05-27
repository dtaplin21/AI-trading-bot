"""Confluence Agent runner — thin wrapper; brain lives in pipeline/confluence_agent.py."""

from agents.base import BaseAgent
from agents.news_runtime import get_news_agent
from agents.pipeline_context import PipelineContext
from pipeline.confluence_adapter import prepare_confluence_inputs
from pipeline.confluence_agent import ConfluenceAgent
from pipeline.feature_fusion_news_patch import fetch_news_features, resolve_technical_direction


class ConfluenceAgentRunner(BaseAgent):
    """
    Runs the world-state confluence brain after all method agents complete.
    Downstream agents read ctx.confluence — not raw method outputs.
    """

    name = "confluence"

    def __init__(self, news_agent=None, registry=None) -> None:
        self._news = news_agent
        self._agent = ConfluenceAgent(method_registry=registry)

    @property
    def news(self):
        return self._news or get_news_agent()

    def run(self, ctx: PipelineContext) -> PipelineContext:
        tech_dir = resolve_technical_direction(ctx)
        news = fetch_news_features(self.news, ctx.symbol, tech_dir, ctx.timestamp)
        ctx.metadata["news_features"] = news.model_dump()

        inputs = prepare_confluence_inputs(ctx, news)
        report = self._agent.analyze(**inputs)
        ctx.confluence = report
        ctx.metadata["confluence_report"] = report.model_dump()
        return ctx
