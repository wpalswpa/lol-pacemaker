"""Unified 18-feature contract shared between MATCH-V5 training and 2999 live serving.

Every feature here is strictly derived from public scoreboard/events so
offline training (Riot MATCH-V5 Timeline) and online inference (2999 API)
have zero definition mismatch.
"""
from __future__ import annotations

import numpy as np

TIER_BUCKETS: dict[str, int] = {"low": 0, "mid": 1, "high": 2}

EXTENDED_FEATURES: list[str] = [
    "game_time_min",
    "first_blood",
    "kills_diff",
    "assists_diff",
    "cs_diff",
    "avg_level_diff",
    "dragons_diff",
    "heralds_diff",
    "towers_diff",
    "voidgrubs_diff",
    "barons_diff",
    "elder_diff",
    "inhibitors_diff",
    "item_gold_diff",
    "recent_3m_kill_diff",
    "lane_cs_diff",
    "alive_diff",
    "tier_bucket",
]


# Features whose definition cannot be reproduced identically from MATCH-V5 Timeline
# (see AGENTS.md 10.1). They stay in the ONNX input but are forced to 0 on BOTH the
# training and the serving side, so they can never create a train/serve mismatch.
MASKED_FEATURES: frozenset[str] = frozenset({"item_gold_diff", "alive_diff", "first_blood"})
MASK_VECTOR: np.ndarray = np.array(
    [0.0 if f in MASKED_FEATURES else 1.0 for f in EXTENDED_FEATURES], dtype=np.float32
)
# Context columns describe the situation, not a play pattern; never reported as a "cause".
CONTEXT_COLUMNS: frozenset[str] = frozenset({"game_time_min", "tier_bucket"})


def vectorize_rows(rows: list[dict[str, float | int]]) -> np.ndarray:
    """Convert feature dicts into float32 [N, 18] matrix (masked features are 0)."""
    m = np.array([[float(r.get(f, 0.0)) for f in EXTENDED_FEATURES] for r in rows], dtype=np.float32)
    return m.reshape(len(rows), len(EXTENDED_FEATURES)) * MASK_VECTOR


def vectorize_row(row: dict[str, float | int]) -> np.ndarray:
    """Convert a single feature dict into float32 [1, 18] array in contract order."""
    return vectorize_rows([row])
