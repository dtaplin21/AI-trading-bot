#!/usr/bin/env python3
"""Cron entrypoint: python scripts/run_scheduled_retrain.py

Schedule daily (default RETRAIN_SCHEDULE_DAYS=1), e.g. cron: 0 2 * * *
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.learning.retrain_pipeline import RetrainPipeline


def main():
    pipeline = RetrainPipeline()
    result = pipeline.run_scheduled_retrain()
    print(json.dumps(result, indent=2, default=str))
    if result.get("status") == "retrained" and not result.get("promoted"):
        print("\nCandidate registered. Manual promotion required unless MODEL_AUTO_PROMOTE=true.")
        print(f"  model_id={result.get('model_id')}")
        print("  POST /models/{id}/promote?approved_by=your_name")


if __name__ == "__main__":
    main()
