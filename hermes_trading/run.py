"""Entrypoint for hermes-trading worker."""
from __future__ import annotations
import argparse
import asyncio
from pathlib import Path
import yaml

GOAL_FILE = Path("/app/state/goal.yaml")


def _load_asset(override: str | None) -> str:
    if override:
        return override
    if GOAL_FILE.exists():
        with open(GOAL_FILE) as f:
            goal = yaml.safe_load(f)
        return goal.get("asset", "BTC/USDT")
    return "BTC/USDT"


def main() -> None:
    parser = argparse.ArgumentParser(description="hermes-trading worker")
    parser.add_argument("--asset", default=None, help="Override asset from goal.yaml")
    args = parser.parse_args()

    asset = _load_asset(args.asset)

    from hermes_trading.loop import run_loop
    asyncio.run(run_loop(asset))


if __name__ == "__main__":
    main()
