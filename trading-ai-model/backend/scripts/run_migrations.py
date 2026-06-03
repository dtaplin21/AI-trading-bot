#!/usr/bin/env python3
"""CLI: apply pending SQL migrations. Also invoked automatically from main.py."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data.storage.migrate import run_migrations


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    count = run_migrations()
    print(f"Applied {count} new migration file(s)")


if __name__ == "__main__":
    main()
