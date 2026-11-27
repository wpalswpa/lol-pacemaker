"""Unified 18-feature contract shared between MATCH-V5 training and 2999 live serving.

Every feature here is strictly derived from public scoreboard/events so
offline training (Riot MATCH-V5 Timeline) and online inference (2999 API)
have zero definition mismatch.
"""
from __future__ import annotations

from typing import Any

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


# AGENTS §28: per game-time logit scale. Upper edges in minutes; the last bin is open.
CALIB_EDGES = (7.5, 12.5, 17.5, 22.5, 27.5)
CALIB_BOUNDS = (0.5, 2.0)


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=np.float64), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def time_bin(minutes: np.ndarray, edges: tuple[float, ...] | list[float] = CALIB_EDGES) -> np.ndarray:
    return np.searchsorted(np.asarray(edges, dtype=np.float64), np.asarray(minutes, dtype=np.float64), side="right")


def apply_time_calibration(p: np.ndarray, minutes: np.ndarray, calib: dict[str, Any] | None) -> np.ndarray:
    """p' = sigmoid(a_bin * logit(p)). No intercept: P(even)=0.5, side mirror and monotonicity are preserved."""
    if not calib:
        return np.asarray(p, dtype=np.float64)
    scale = np.asarray(calib["scale"], dtype=np.float64)
    a = scale[np.clip(time_bin(minutes, calib["edges"]), 0, len(scale) - 1)]
    return 1.0 / (1.0 + np.exp(-a * _logit(p)))
