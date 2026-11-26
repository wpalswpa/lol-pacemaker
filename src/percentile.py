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
