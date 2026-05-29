#!/usr/bin/env python3
"""Entry point for the 24/7 chart watcher."""

from __future__ import annotations

import asyncio
import logging
import sys

from chart_watcher.chart_watch_runner import ChartWatchRunner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    stream=sys.stdout,
)


async def _main() -> None:
    runner = ChartWatchRunner()
    await runner.start()


if __name__ == "__main__":
    asyncio.run(_main())
