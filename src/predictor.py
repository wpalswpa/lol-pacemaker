"""Local zero-server ONNX win-rate predictor.

Model resolution order (AGENTS.md 10.5):
  1. latest verified model downloaded by `src.updater` (user data dir)
  2. bundled `assets/model_extended.onnx` (18-feature)
  3. bundled `assets/model_sklearn.onnx` (8-feature legacy)
Any candidate that is missing, unloadable or has the wrong input shape is skipped.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

import numpy as np
import onnxruntime as ort
from pydantic import BaseModel, ConfigDict, Field

from ml.features import CONTEXT_COLUMNS, EXTENDED_FEATURES, MASKED_FEATURES, TIER_BUCKETS, apply_time_calibration, vectorize_rows

Side = Literal["blue", "red"]
Tier = Literal["low", "mid", "high"]

LEGACY_FEATURES = ["first_blood", "kills", "assists", "cs", "avg_level", "dragons", "heralds", "towers"]
# legacy column -> extended feature name (used so explanations share one vocabulary)
LEGACY_TO_EXTENDED = {
    "first_blood": "first_blood", "kills": "kills_diff", "assists": "assists_diff", "cs": "cs_diff",
    "avg_level": "avg_level_diff", "dragons": "dragons_diff", "heralds": "heralds_diff", "towers": "towers_diff",
}
# Context columns are never "ablated" in explanations.
CONTEXT_FEATURES = set(CONTEXT_COLUMNS)

DEFAULT_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"


class ScoreboardState(BaseModel):
    """Public scoreboard snapshot (diff = blue - red internally)."""

    model_config = ConfigDict(extra="ignore")

    my_side: Side = "blue"
    game_time_min: float = Field(default=10.0, ge=0.0, le=120.0)
    tier: Tier = "mid"
    blue_first_blood: int = Field(default=0, ge=0, le=1)
    red_first_blood: int = Field(default=0, ge=0, le=1)
    blue_kills: int = Field(default=0, ge=0, le=150)
    red_kills: int = Field(default=0, ge=0, le=150)
    blue_assists: int = Field(default=0, ge=0, le=400)
    red_assists: int = Field(default=0, ge=0, le=400)
    blue_cs: int = Field(default=0, ge=0, le=3000)
    red_cs: int = Field(default=0, ge=0, le=3000)
    blue_avg_level: float = Field(default=1.0, ge=1.0, le=18.0)
    red_avg_level: float = Field(default=1.0, ge=1.0, le=18.0)
    blue_dragons: int = Field(default=0, ge=0, le=10)
    red_dragons: int = Field(default=0, ge=0, le=10)
    blue_heralds: int = Field(default=0, ge=0, le=3)
    red_heralds: int = Field(default=0, ge=0, le=3)
    blue_towers: int = Field(default=0, ge=0, le=11)
    red_towers: int = Field(default=0, ge=0, le=11)
    blue_voidgrubs: int = Field(default=0, ge=0, le=6)
    red_voidgrubs: int = Field(default=0, ge=0, le=6)
    blue_barons: int = Field(default=0, ge=0, le=10)
    red_barons: int = Field(default=0, ge=0, le=10)
    blue_elder: int = Field(default=0, ge=0, le=10)
    red_elder: int = Field(default=0, ge=0, le=10)
    blue_inhibitors: int = Field(default=0, ge=0, le=10)
    red_inhibitors: int = Field(default=0, ge=0, le=10)
    recent_3m_kill_diff: int = Field(default=0, ge=-60, le=60)  # blue - red kills in the last 3 minutes
    lane_cs_diff: int = Field(default=0, ge=-3000, le=3000)  # blue - red CS of TOP/MID/BOT

    def to_feature_row(self) -> np.ndarray:
        """Legacy [1, 8] float32 row (first blood is +1 blue / -1 otherwise, as in the Kaggle model)."""
        fb = 1.0 if self.blue_first_blood else -1.0
        return np.array(
            [[fb, float(self.blue_kills - self.red_kills), float(self.blue_assists - self.red_assists),
              float(self.blue_cs - self.red_cs), float(self.blue_avg_level - self.red_avg_level),
              float(self.blue_dragons - self.red_dragons), float(self.blue_heralds - self.red_heralds),
              float(self.blue_towers - self.red_towers)]],
            dtype=np.float32,
        )

    def to_feature_dict(self) -> dict[str, float]:
        """18-feature dict with the same keys/signs/meaning as `ml.collector` snapshots."""
        fb = 1.0 if self.blue_first_blood else (-1.0 if self.red_first_blood else 0.0)
        return {
            "game_time_min": float(self.game_time_min),
            "first_blood": fb,
            "kills_diff": float(self.blue_kills - self.red_kills),
            "assists_diff": float(self.blue_assists - self.red_assists),
            "cs_diff": float(self.blue_cs - self.red_cs),
            "avg_level_diff": float(self.blue_avg_level - self.red_avg_level),
            "dragons_diff": float(self.blue_dragons - self.red_dragons),
            "heralds_diff": float(self.blue_heralds - self.red_heralds),
            "towers_diff": float(self.blue_towers - self.red_towers),
            "voidgrubs_diff": float(self.blue_voidgrubs - self.red_voidgrubs),
            "barons_diff": float(self.blue_barons - self.red_barons),
            "elder_diff": float(self.blue_elder - self.red_elder),
            "inhibitors_diff": float(self.blue_inhibitors - self.red_inhibitors),
            "item_gold_diff": 0.0,
            "recent_3m_kill_diff": float(self.recent_3m_kill_diff),
            "lane_cs_diff": float(self.lane_cs_diff),
            "alive_diff": 0.0,
            "tier_bucket": float(TIER_BUCKETS[self.tier]),
        }


def _open_session(path: Path) -> ort.InferenceSession:
    return ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])


def _output_spec(sess: ort.InferenceSession) -> tuple[str, int]:
    names = [o.name for o in sess.get_outputs()]
    if "probabilities" in names:
        return "probabilities", 1
    if "win_prob" in names:
        return "win_prob", 0
    raise RuntimeError(f"Unsupported ONNX outputs: {names}")


def _input_width(sess: ort.InferenceSession) -> int | None:
    shape = sess.get_inputs()[0].shape
    return shape[1] if len(shape) == 2 and isinstance(shape[1], int) else None


def _valid_calibration(c: Any) -> dict[str, Any] | None:
    """Accept only a well-formed per-minute scale table with positive scales (keeps monotonicity)."""
    try:
        edges, scale = [float(e) for e in c["edges"]], [float(a) for a in c["scale"]]
    except (TypeError, KeyError, ValueError):
        return None
    if len(scale) != len(edges) + 1 or edges != sorted(edges) or not all(0.0 < a <= 5.0 for a in scale):
        return None
    return {"edges": edges, "scale": scale}


class LocalOnnxPredictor:
    """Zero-server ONNX Runtime inference wrapper with extended/legacy fallback."""

    def __init__(self, assets_dir: Path | str | None = None, model_dirs: list[Path] | None = None) -> None:
        base = Path(assets_dir) if assets_dir else DEFAULT_ASSETS_DIR
        if model_dirs is None:
            from src.updater import current_model_dir  # local import: updater imports this module's peers

            updated = current_model_dir()
            model_dirs = [updated] if updated else []
        candidates = [Path(d) for d in model_dirs] + [base]

        self.mode: Literal["extended", "legacy8"] = "legacy8"
        self.source: Path | None = None
        self.stats: dict[str, Any] | None = None
        self.load_errors: list[str] = []
        self.calibration: dict[str, Any] | None = None  # AGENTS §28 per-minute logit scale (from distribution_stats.json)
        self._sess: ort.InferenceSession | None = None

        for cand in candidates:
            ext = cand / "model_extended.onnx"
            if not ext.exists():
                continue
            try:
                sess = _open_session(ext)
                if _input_width(sess) != len(EXTENDED_FEATURES):
                    raise RuntimeError(f"expected {len(EXTENDED_FEATURES)} inputs, got {_input_width(sess)}")
                self._out_name, self._col = _output_spec(sess)
                self._sess, self.mode, self.source = sess, "extended", ext
                self.stats = self._load_stats(cand / "distribution_stats.json")
                self.calibration = _valid_calibration((self.stats or {}).get("win_calibration"))
                break
            except Exception as exc:  # corrupted/mismatched model -> try the next candidate
                self.load_errors.append(f"{ext}: {exc}")

        if self._sess is None:
            onnx_path = base / os.getenv("ONNX_FILE", "model_sklearn.onnx")
            if not onnx_path.exists():
                raise FileNotFoundError(f"ONNX model not found: {onnx_path}")
            self._sess = _open_session(onnx_path)
            self._out_name, self._col = _output_spec(self._sess)
            self.source = onnx_path

        meta_path = base / "model.json"
        self.meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}

    @staticmethod
    def _load_stats(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) and isinstance(data.get("tiers"), dict) else None
        except (OSError, ValueError):
            return None

    # -- inference -----------------------------------------------------------------
    def _blue_probs(self, matrix: np.ndarray) -> np.ndarray:
        assert self._sess is not None
        out = self._sess.run([self._out_name], {"X": matrix.astype(np.float32)})[0]
        p = np.asarray(out)[:, self._col].astype(np.float64)
        if self.calibration is None or self.mode != "extended":
            return p
        return apply_time_calibration(p, matrix[:, 0], self.calibration)

    def _matrix(self, state: ScoreboardState) -> np.ndarray:
        if self.mode == "extended":
            return vectorize_rows([state.to_feature_dict()])
        return state.to_feature_row()

    def predict_win_prob(self, state: ScoreboardState) -> float:
        """Return win probability [0.0, 1.0] for `state.my_side`."""
        blue_prob = float(self._blue_probs(self._matrix(state))[0])
        prob = blue_prob if state.my_side == "blue" else 1.0 - blue_prob
        return max(0.0, min(1.0, prob))

    def explain(self, state: ScoreboardState) -> list[tuple[str, float]]:
        """Per-feature win-prob contribution (percentage points) for `state.my_side`.

        Model-agnostic ablation: each feature is reset to neutral (0 = even) and the change in
        win probability is measured in one batched ONNX call. Negative = this feature is
        currently hurting my side. Sorted most-hurting first.
        """
        base = self._matrix(state)
        names = EXTENDED_FEATURES if self.mode == "extended" else LEGACY_FEATURES
        cols = [
            i for i, n in enumerate(names)
            if n not in CONTEXT_FEATURES and n not in MASKED_FEATURES and base[0, i] != 0.0
        ]
        if not cols:
            return []
        batch = np.repeat(base, len(cols) + 1, axis=0)
        for row, col in enumerate(cols, start=1):
            batch[row, col] = 0.0
        probs = self._blue_probs(batch)
        sign = 1.0 if state.my_side == "blue" else -1.0
        out = []
        for row, col in enumerate(cols, start=1):
            delta_pp = sign * (probs[0] - probs[row]) * 100.0  # positive: feature helps my side
            name = names[col]
            out.append((LEGACY_TO_EXTENDED.get(name, name), round(float(delta_pp), 2)))
        out.sort(key=lambda kv: kv[1])
        return out
