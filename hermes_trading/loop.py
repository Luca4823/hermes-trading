"""24/7 async trading loop. Pulls data, evaluates strategy, logs paper trades."""
from __future__ import annotations
import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import numpy as np
import yaml

from hermes_trading.adapters import price as price_adapter
from hermes_trading.adapters import onchain as onchain_adapter
from hermes_trading.adapters import news as news_adapter
from hermes_trading.adapters import macro as macro_adapter

STATE_DIR = Path(os.getenv("STATE_DIR", "/app/state"))
TRADES_FILE = STATE_DIR / "trades.jsonl"
HEARTBEAT_FILE = STATE_DIR / "heartbeat.json"
STRATEGY_FILE = STATE_DIR / "strategy.yaml"

LOOP_INTERVAL_S = 60
MAX_RETRIES = 3
CIRCUIT_BREAK_THRESHOLD = 5


def _load_strategy() -> Dict[str, Any]:
    with open(STRATEGY_FILE) as f:
        return yaml.safe_load(f)


def _compute_rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes[-(period + 1):])
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = gains.mean()
    avg_loss = losses.mean()
    if avg_loss < 1e-9:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


async def _fetch_with_retry(adapter, asset: str, name: str) -> Dict[str, Any] | None:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return await adapter.fetch(asset)
        except Exception as e:
            wait = 2 ** attempt
            print(f"[{name}] attempt {attempt}/{MAX_RETRIES} failed: {e}. Retrying in {wait}s.")
            if attempt < MAX_RETRIES:
                await asyncio.sleep(wait)
    print(f"[{name}] all retries exhausted — skipping.")
    return None


def _evaluate_entry(strategy: Dict[str, Any], price_data: Dict[str, Any]) -> bool:
    entry = strategy.get("entry", {})
    indicator = entry.get("indicator", "rsi")
    threshold = float(entry.get("threshold", 30))
    direction = entry.get("direction", "long")

    if indicator == "rsi":
        closes = price_data.get("closes_1h", [])
        rsi = _compute_rsi(closes)
        if direction == "long":
            return rsi < threshold
        else:
            return rsi > threshold
    return False


def _paper_trade(strategy: Dict[str, Any], price_data: Dict[str, Any]) -> Dict[str, Any]:
    entry_price = price_data["last"]
    stop_loss_pct = float(strategy.get("stop_loss_pct", 2.0)) / 100
    position_size_r = float(strategy.get("position_size_r", 0.5))

    # Simulate exit: small random walk over next "bar"
    rng = np.random.default_rng()
    exit_pct = rng.normal(loc=0.001, scale=0.01)  # slight positive drift in paper mode
    pnl_pct = exit_pct * position_size_r
    pnl_pct = max(pnl_pct, -stop_loss_pct * position_size_r)  # stop loss floor

    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "asset": price_data["asset"],
        "strategy_version": strategy.get("version", "01"),
        "entry_price": entry_price,
        "stop_loss_pct": stop_loss_pct * 100,
        "position_size_r": position_size_r,
        "pnl_pct": round(pnl_pct, 6),
        "closed": True,
    }


def _log_trade(trade: Dict[str, Any]) -> None:
    TRADES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TRADES_FILE, "a") as f:
        f.write(json.dumps(trade) + "\n")


def _write_heartbeat(status: str, consecutive_failures: int) -> None:
    data = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "consecutive_failures": consecutive_failures,
    }
    HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HEARTBEAT_FILE, "w") as f:
        json.dump(data, f)


async def run_loop(asset: str) -> None:
    print(f"Booting hermes-trading worker — asset={asset} mode=paper")
    consecutive_failures = 0

    while True:
        loop_start = time.monotonic()
        try:
            strategy = _load_strategy()

            price_data, onchain_data, news_data, macro_data = await asyncio.gather(
                _fetch_with_retry(price_adapter, asset, "price"),
                _fetch_with_retry(onchain_adapter, asset, "onchain"),
                _fetch_with_retry(news_adapter, asset, "news"),
                _fetch_with_retry(macro_adapter, asset, "macro"),
            )

            if price_data is None:
                raise RuntimeError("Price adapter failed — cannot evaluate strategy.")

            consecutive_failures = 0

            if _evaluate_entry(strategy, price_data):
                trade = _paper_trade(strategy, price_data)
                _log_trade(trade)
                print(f"[trade] {trade['asset']} pnl={trade['pnl_pct']:+.4%} v{trade['strategy_version']}")
            else:
                print(f"[loop] no entry — last={price_data['last']} — watching")

            _write_heartbeat("ok", 0)

        except Exception as e:
            consecutive_failures += 1
            print(f"[loop] error ({consecutive_failures}/{CIRCUIT_BREAK_THRESHOLD}): {e}")
            _write_heartbeat("error", consecutive_failures)

            if consecutive_failures >= CIRCUIT_BREAK_THRESHOLD:
                print("[loop] CIRCUIT BREAKER: 5 consecutive failures — halting loop.")
                raise SystemExit(1)

        elapsed = time.monotonic() - loop_start
        sleep_for = max(0, LOOP_INTERVAL_S - elapsed)
        await asyncio.sleep(sleep_for)
