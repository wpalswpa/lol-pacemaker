"""Zero-dependency normal distribution percentile engine (uses stdlib math.erf)."""
from __future__ import annotations

import math


def top_percentile(value: float, mean: float, std: float) -> int:
    """Return integer Top X% (1..99) from normal distribution (lower % = better)."""
    safe_std = std if std > 1e-6 else 1.0
    z = (value - mean) / safe_std
    cdf = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    top_pct = int(round((1.0 - cdf) * 100.0))
    return max(1, min(99, top_pct))


METRICS = ("cs_per_min", "level", "kill_participation")
MIN_STATS_SECONDS = 480.0   # below 8:00 the per-minute averages are too noisy
MAX_STATS_SECONDS = 2100.0  # beyond 35:00 the 25-minute bucket no longer describes the game


def nearest_minute(game_time_sec: float, available: list[int]) -> int | None:
    if not available or not (MIN_STATS_SECONDS <= game_time_sec <= MAX_STATS_SECONDS):
        return None
    minute = game_time_sec / 60.0
    return min(available, key=lambda m: abs(m - minute))


def lookup_percentiles(
    stats: dict | None,
    tier: str,
    game_time_sec: float,
    position: str | None,
    values: dict[str, float | None],
) -> dict | None:
    """Top-% per metric from `distribution_stats.json` (AGENTS.md 10.4); None if unavailable.

    Returns {"minute": int, "role": str, "pct": {metric: int}} using the closest stored minute and
    the player's role (falls back to the "ALL" bucket).
    """
    try:
        by_minute = ((stats or {}).get("tiers") or {}).get(tier) or {}
        minute = nearest_minute(game_time_sec, sorted(int(m) for m in by_minute))
        if minute is None:
            return None
        by_role = by_minute[str(minute)]
        role = position if position in by_role else "ALL"
        if role not in by_role:
            return None
        pct: dict[str, int] = {}
        for metric in METRICS:
            v = values.get(metric)
            cell = by_role[role].get(metric)
            if v is None or not cell:
                continue
            pct[metric] = top_percentile(float(v), float(cell["mean"]), float(cell["std"]))
        return {"minute": minute, "role": role, "pct": pct} if pct else None
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
