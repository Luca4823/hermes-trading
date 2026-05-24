"""Reflection cycle for hermes-trading.

Two modes:
  --fallback   Deterministic rule-based reflection (Phase 5, no Hermes needed)
  --hermes     Production: calls Hermes subprocess to reason over trades (Phase 7)
"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml

STATE_DIR = Path(os.getenv("STATE_DIR", "/app/state"))
STRATEGY_FILE = STATE_DIR / "strategy.yaml"
TRADES_FILE = STATE_DIR / "trades.jsonl"
HYPOTHESES_FILE = STATE_DIR / "hypotheses.jsonl"
HISTORY_DIR = STATE_DIR / "history"
GOAL_FILE = STATE_DIR / "goal.yaml"


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _save_yaml(path: Path, data: dict) -> None:
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def _load_trades(n: int = 25) -> list:
    if not TRADES_FILE.exists():
        return []
    lines = TRADES_FILE.read_text().strip().splitlines()
    return [json.loads(l) for l in lines[-n:]]


def _bump_version(strategy: dict) -> str:
    v = strategy.get("version", "01")
    try:
        new_v = str(int(v) + 1).zfill(2)
    except ValueError:
        new_v = "02"
    return new_v


def _archive_strategy(strategy: dict) -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    v = strategy.get("version", "01")
    dest = HISTORY_DIR / f"v{v.zfill(4)}.yaml"
    _save_yaml(dest, strategy)


def _append_hypothesis(hypothesis: dict) -> None:
    HYPOTHESES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HYPOTHESES_FILE, "a") as f:
        f.write(json.dumps(hypothesis) + "\n")


# ---------------------------------------------------------------------------
# Fallback (deterministic) reflection
# ---------------------------------------------------------------------------

def reflect_fallback() -> None:
    strategy = _load_yaml(STRATEGY_FILE)
    goal = _load_yaml(GOAL_FILE)
    trades = _load_trades(25)

    if not trades:
        print("[reflect/fallback] No trades yet — nothing to reflect on.")
        return

    from hermes_trading.score import score as compute_score
    current_score = compute_score(trades, goal)

    total_return = sum(t.get("pnl_pct", 0.0) for t in trades)
    target = goal.get("target_return_30d", 0.05)
    max_dd = goal.get("max_drawdown", 0.08)

    # Compute actual drawdown
    peak = equity = max_observed_dd = 0.0
    for t in trades:
        equity += t.get("pnl_pct", 0.0)
        peak = max(peak, equity)
        dd = (peak - equity) / (peak + 1e-9)
        max_observed_dd = max(max_observed_dd, dd)

    old_version = strategy.get("version", "01")
    _archive_strategy(strategy)

    changed_var = None
    old_value = None
    new_value = None

    if total_return < target:
        # Loosen entry threshold to catch more trades
        old_value = strategy["entry"]["threshold"]
        strategy["entry"]["threshold"] = old_value + 2
        new_value = strategy["entry"]["threshold"]
        changed_var = "entry.threshold"
        reasoning = (
            f"Realised return {total_return:.4%} < target {target:.4%}. "
            f"Loosening entry.threshold from {old_value} → {new_value} to increase trade frequency."
        )
    elif max_observed_dd > max_dd:
        # Tighten stop loss
        old_value = strategy["stop_loss_pct"]
        strategy["stop_loss_pct"] = round(old_value - 0.2, 2)
        new_value = strategy["stop_loss_pct"]
        changed_var = "stop_loss_pct"
        reasoning = (
            f"Max drawdown {max_observed_dd:.4%} > limit {max_dd:.4%}. "
            f"Tightening stop_loss_pct from {old_value} → {new_value}."
        )
    else:
        # Performing well — slightly tighten entry for quality
        old_value = strategy["entry"]["threshold"]
        strategy["entry"]["threshold"] = max(20, old_value - 1)
        new_value = strategy["entry"]["threshold"]
        changed_var = "entry.threshold"
        reasoning = (
            f"Score {current_score:.4f} is healthy. "
            f"Tightening entry.threshold from {old_value} → {new_value} for quality."
        )

    new_version = _bump_version({"version": old_version})
    strategy["version"] = new_version
    _save_yaml(STRATEGY_FILE, strategy)

    hypothesis = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "mode": "fallback",
        "from_version": old_version,
        "to_version": new_version,
        "changed_var": changed_var,
        "old_value": old_value,
        "new_value": new_value,
        "score_before": current_score,
        "reasoning": reasoning,
        "trades_evaluated": len(trades),
    }
    _append_hypothesis(hypothesis)

    print(f"[reflect/fallback] v{old_version} → v{new_version}: {changed_var} {old_value} → {new_value}")
    print(f"  Reasoning: {reasoning}")


# ---------------------------------------------------------------------------
# Hermes (production) reflection
# ---------------------------------------------------------------------------

def reflect_hermes() -> None:
    strategy = _load_yaml(STRATEGY_FILE)
    goal = _load_yaml(GOAL_FILE)
    trades = _load_trades(25)

    if not trades:
        print("[reflect/hermes] No trades yet — nothing to reflect on.")
        return

    from hermes_trading.score import score as compute_score
    current_score = compute_score(trades, goal)

    prompt = f"""You are the reflection engine of a self-improving trading agent.

Current strategy (YAML):
{yaml.dump(strategy, default_flow_style=False)}

Goal:
{yaml.dump(goal, default_flow_style=False)}

Last {len(trades)} closed trades (JSON lines):
{json.dumps(trades, indent=2)}

Current composite score: {current_score:.4f} (range -1 to +1, target >= 0)

Your task:
1. Identify the single most impactful variable to change in the strategy YAML.
2. Propose a new value for that variable.
3. Explain your reasoning in one sentence.

Reply in this exact JSON format:
{{
  "changed_var": "<dot.path to variable>",
  "old_value": <current value>,
  "new_value": <proposed value>,
  "reasoning": "<one sentence>"
}}

Only output the JSON. No other text."""

    result = subprocess.run(
        ["hermes"],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=120,
    )

    if result.returncode != 0:
        print(f"[reflect/hermes] Hermes exited with code {result.returncode}: {result.stderr}")
        print("[reflect/hermes] Falling back to deterministic reflection.")
        reflect_fallback()
        return

    try:
        raw = result.stdout.strip()
        # Extract JSON from output (Hermes may wrap it)
        start = raw.find("{")
        end = raw.rfind("}") + 1
        proposal = json.loads(raw[start:end])
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[reflect/hermes] Could not parse Hermes output: {e}")
        print("[reflect/hermes] Falling back to deterministic reflection.")
        reflect_fallback()
        return

    old_version = strategy.get("version", "01")
    _archive_strategy(strategy)

    # Apply the change (supports simple keys and entry.* keys)
    var_path = proposal["changed_var"]
    new_val = proposal["new_value"]
    parts = var_path.split(".")
    node = strategy
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = new_val

    new_version = _bump_version({"version": old_version})
    strategy["version"] = new_version
    _save_yaml(STRATEGY_FILE, strategy)

    hypothesis = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "mode": "hermes",
        "from_version": old_version,
        "to_version": new_version,
        "changed_var": var_path,
        "old_value": proposal.get("old_value"),
        "new_value": new_val,
        "score_before": current_score,
        "reasoning": proposal.get("reasoning", ""),
        "trades_evaluated": len(trades),
    }
    _append_hypothesis(hypothesis)

    print(f"[reflect/hermes] v{old_version} → v{new_version}: {var_path} → {new_val}")
    print(f"  Reasoning: {proposal.get('reasoning', '')}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="hermes-trading reflection cycle")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--fallback", action="store_true", help="Deterministic rule-based reflection")
    group.add_argument("--hermes", action="store_true", help="Hermes-powered reflection")
    args = parser.parse_args()

    if args.fallback:
        reflect_fallback()
    else:
        reflect_hermes()


if __name__ == "__main__":
    main()
