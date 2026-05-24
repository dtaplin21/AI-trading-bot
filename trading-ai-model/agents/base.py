"""Base agent interface — services, not chat LLMs."""

from abc import ABC, abstractmethod

from agents.pipeline_context import PipelineContext


class BaseAgent(ABC):
    name: str = "base"

    @abstractmethod
    def run(self, ctx: PipelineContext) -> PipelineContext:
        ...
