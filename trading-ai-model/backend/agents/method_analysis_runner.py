"""Runs all method analysis agents — every method, every candle."""

from agents.base import BaseAgent
from agents.method_agents import ALL_METHOD_AGENTS, REQUIRED_METHODS
from agents.pipeline_context import PipelineContext


class MethodAnalysisRunner(BaseAgent):
    name = "method_analysis"

    def __init__(self, agents=None):
        self.agents = agents or ALL_METHOD_AGENTS

    def run(self, ctx: PipelineContext) -> PipelineContext:
        ctx.method_outputs = []
        for agent in self.agents:
            ctx = agent.run(ctx)

        ran = {o.method for o in ctx.method_outputs if not o.skipped}
        ctx.metadata["methods_required"] = len(REQUIRED_METHODS)
        ctx.metadata["methods_ran"] = len(ran)
        ctx.metadata["all_methods_ran"] = REQUIRED_METHODS.issubset(
            {o.method for o in ctx.method_outputs}
        )
        return ctx
