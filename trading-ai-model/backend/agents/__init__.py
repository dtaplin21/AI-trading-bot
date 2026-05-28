"""Multi-agent trading intelligence system."""

__all__ = ["TradingSupervisor"]


def __getattr__(name: str):
    if name == "TradingSupervisor":
        from agents.supervisor import TradingSupervisor

        return TradingSupervisor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
