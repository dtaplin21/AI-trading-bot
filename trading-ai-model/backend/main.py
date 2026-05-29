"""
main.py — updated entry point

Modes:
  python main.py --mode dev      ← FastAPI + Vite dashboard (recommended for UI)
  python main.py --mode watch    ← 24/7 chart watcher
  python main.py --mode api      ← FastAPI server only
  python main.py --mode replay   ← historical backtest
  python main.py --mode research ← method isolation testing
  python main.py --mode live     ← live trading (broker adapter required)

Env vars (.env):
  WATCHER_MODE       live | replay | paper
  WATCHER_SYMBOLS    MES,NQ,CL,GC
  WATCHER_TIMEFRAMES 1m,5m,15m,1h
  ANTHROPIC_API_KEY  enables LLM news + audit
  RISK_KILL_SWITCH   true = halt all trading immediately
"""
from __future__ import annotations

import argparse
import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import structlog
from dotenv import load_dotenv

_BACKEND_ROOT = Path(__file__).resolve().parent
_MONOREPO_ROOT = _BACKEND_ROOT.parent
_FRONTEND_ROOT = _MONOREPO_ROOT / "frontend"

load_dotenv(_BACKEND_ROOT / ".env")
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="ISO"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ]
)
logger = structlog.get_logger()


async def start_watcher() -> None:
    from agents.news.market_news_agent import MarketNewsAgent
    from chart_watcher.chart_watch_runner import ChartWatchRunner

    news_agent = None
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("FINNHUB_API_KEY"):
        news_agent = MarketNewsAgent(
            use_llm=bool(os.getenv("ANTHROPIC_API_KEY")),
            polling_interval=int(os.getenv("NEWS_POLLING_INTERVAL", "60")),
        )

    runner = ChartWatchRunner(news_agent=news_agent)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def handle_shutdown(sig, frame) -> None:
        stop_event.set()
        loop.create_task(runner.stop())

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    print(f"\n{'=' * 55}")
    print("  Trading AI — Chart Watcher")
    print(f"  Mode:    {os.getenv('WATCHER_MODE', 'paper').upper()}")
    print(f"  Symbols: {os.getenv('WATCHER_SYMBOLS', 'MES,NQ')}")
    print(f"  TF:      {os.getenv('WATCHER_TIMEFRAMES', '1m,5m,15m,1h')}")
    print(f"  API key: {'✓ set' if os.getenv('ANTHROPIC_API_KEY') else '✗ not set'}")
    print(f"  Kill sw: {os.getenv('RISK_KILL_SWITCH', 'false')}")
    print(f"{'=' * 55}\n")
    print("  Every trade is a probability over a series.\n")

    await runner.start()


async def start_api() -> None:
    import uvicorn

    config = uvicorn.Config(
        "api.main:app",
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_PORT", "8000")),
        reload=os.getenv("API_RELOAD", "false").lower() == "true",
        log_level="info",
    )
    await uvicorn.Server(config).serve()


async def start_replay() -> None:
    os.environ["WATCHER_MODE"] = "replay"
    await start_watcher()


async def start_research() -> None:
    from validation.method_isolation.method_isolation_validator import MethodEdgeRegistry

    registry = MethodEdgeRegistry()
    print("\nMETHOD ISOLATION RESEARCH MODE")
    print(registry.print_status_table())


def start_dev() -> None:
    """Run FastAPI (port 8000) and Vite (port 5173) together for local dashboard dev."""
    if not (_FRONTEND_ROOT / "package.json").exists():
        print(f"Frontend not found at {_FRONTEND_ROOT}", file=sys.stderr)
        sys.exit(1)
    if not (_FRONTEND_ROOT / "node_modules").exists():
        print("Frontend dependencies missing. Run:", file=sys.stderr)
        print(f"  cd {_FRONTEND_ROOT} && npm install", file=sys.stderr)
        sys.exit(1)

    api_port = os.getenv("API_PORT", "8000")
    procs: list[subprocess.Popen] = []

    def terminate_all() -> None:
        for proc in procs:
            if proc.poll() is None:
                proc.terminate()
        for proc in procs:
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()

    def on_signal(sig, frame) -> None:
        terminate_all()
        sys.exit(0)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    vite = subprocess.Popen(
        ["npm", "run", "dev"],
        cwd=str(_FRONTEND_ROOT),
    )
    procs.append(vite)

    # Brief pause so Vite binds before the UI's first poll
    time.sleep(1.5)

    api = subprocess.Popen(
        [sys.executable, str(_BACKEND_ROOT / "main.py"), "--mode", "api"],
        cwd=str(_BACKEND_ROOT),
    )
    procs.append(api)

    print(f"\n{'=' * 55}")
    print("  Trading AI — Dev (API + Dashboard)")
    print(f"  API:       http://127.0.0.1:{api_port}")
    print(f"  Dashboard: http://localhost:5173")
    print(f"  Docs:      http://127.0.0.1:{api_port}/docs")
    print(f"{'=' * 55}\n")
    print("  Press Ctrl+C to stop both servers.\n")

    try:
        while True:
            for proc in procs:
                code = proc.poll()
                if code is not None:
                    name = "vite" if proc is vite else "api"
                    print(f"\n{name} exited with code {code}. Shutting down dev stack.")
                    terminate_all()
                    sys.exit(code if code != 0 else 0)
            time.sleep(0.5)
    except KeyboardInterrupt:
        terminate_all()


def main() -> None:
    parser = argparse.ArgumentParser(description="Trading AI Model")
    parser.add_argument(
        "--mode",
        choices=["dev", "watch", "api", "replay", "research", "live"],
        default="dev",
    )
    args = parser.parse_args()

    if args.mode == "dev":
        start_dev()
        return

    if args.mode == "live":
        confirm = input("\n⚠️  LIVE MODE. Type 'I understand the risk': ")
        if confirm != "I understand the risk":
            sys.exit(0)
        os.environ["WATCHER_MODE"] = "live"

    print(f"\nTrading AI — {args.mode.upper()}")

    runners = {
        "watch": start_watcher,
        "api": start_api,
        "replay": start_replay,
        "research": start_research,
        "live": start_watcher,
    }
    asyncio.run(runners[args.mode]())


if __name__ == "__main__":
    main()
