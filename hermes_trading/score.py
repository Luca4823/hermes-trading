"""Score a list of trades against goal.yaml. Returns float in [-1, +1]."""
from __future__ import annotations
import math
from typing import List, Dict, Any


def score(trades: List[Dict[str, Any]], goal: Dict[str, Any]) -> float:
    """
    Composite score in [-1, +1]:
      - Realised return vs target_return_30d
      - Max drawdown vs max_drawdown
      - Sharpe vs min_sharpe
    """
    if not trades:
        return 0.0

    pnls = [t.get("pnl_pct", 0.0) for t in trades]
    total_return = sum(pnls)
    target = goal.get("target_return_30d", 0.05)
    max_dd_limit = goal.get("max_drawdown", 0.08)
    min_sharpe = goal.get("min_sharpe", 1.2)
    failure_floor = goal.get("failure_below", -0.04)

    # Return component: normalised to [-1, +1]
    return_score = max(-1.0, min(1.0, total_return / target)) if target else 0.0

    # Drawdown component
    peak = 0.0
    equity = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        dd = (peak - equity) / (peak + 1e-9)
        max_dd = max(max_dd, dd)

    dd_score = 1.0 - (max_dd / max_dd_limit) if max_dd_limit else 1.0
    dd_score = max(-1.0, min(1.0, dd_score))

    # Sharpe component
    if len(pnls) >= 2:
        import statistics
        mean_pnl = statistics.mean(pnls)
        std_pnl = statistics.stdev(pnls)
        sharpe = (mean_pnl / std_pnl * math.sqrt(len(pnls))) if std_pnl > 1e-9 else 0.0
    else:
        sharpe = 0.0

    sharpe_score = max(-1.0, min(1.0, sharpe / min_sharpe)) if min_sharpe else 0.0

    composite = (return_score * 0.5) + (dd_score * 0.3) + (sharpe_score * 0.2)
    composite = max(-1.0, min(1.0, composite))

    # Hard floor: if total_return is deeply negative, clamp to failure_below
    if total_return < failure_floor:
        composite = min(composite, -0.8)

    return round(composite, 4)
