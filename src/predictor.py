"""Local zero-server ONNX win-rate predictor.

Loads `assets/model_sklearn.onnx` (585 bytes, standardizer embedded inside ONNX)
and computes blue/my_side win probability locally in <1ms without FastAPI or cloud calls.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

import numpy as np
import onnxruntime as ort
from pydantic import BaseModel, ConfigDict, Field

Side = Literal["blue", "red"]
FEATURES = [
    "first_blood",
    "kills",
    "assists",
    "cs",
    "avg_level",
    "dragons",
    "heralds",
    "towers",
]

DEFAULT_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"


class ScoreboardState(BaseModel):
    """Public 8-feature scoreboard snapshot (diff = blue - red internally)."""

    model_config = ConfigDict(extra="ignore")

    my_side: Side = "blue"
    blue_first_blood: int = Field(default=0, ge=0, le=1)
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

    def to_feature_row(self) -> np.ndarray:
        """Convert scoreboard into [1, 8] float32 array in training feature order."""
        fb = 1.0 if self.blue_first_blood else -1.0
        return np.array(
            [
                [
                    fb,
                    float(self.blue_kills - self.red_kills),
                    float(self.blue_assists - self.red_assists),
                    float(self.blue_cs - self.red_cs),
                    float(self.blue_avg_level - self.red_avg_level),
                    float(self.blue_dragons - self.red_dragons),
                    float(self.blue_heralds - self.red_heralds),
                    float(self.blue_towers - self.red_towers),
                ]
            ],
            dtype=np.float32,
        )


class LocalOnnxPredictor:
    """Zero-server ONNX Runtime inference wrapper."""

    def __init__(self, assets_dir: Path | str | None = None) -> None:
        base = Path(assets_dir) if assets_dir else DEFAULT_ASSETS_DIR
        onnx_path = base / os.getenv("ONNX_FILE", "model_sklearn.onnx")
        if not onnx_path.exists():
            raise FileNotFoundError(f"ONNX model not found: {onnx_path}")

        self._sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
        names = [o.name for o in self._sess.get_outputs()]
        if "probabilities" in names:
            self._out_name, self._col = "probabilities", 1
        elif "win_prob" in names:
            self._out_name, self._col = "win_prob", 0
        else:
            raise RuntimeError(f"Unsupported ONNX outputs: {names}")

        meta_path = base / "model.json"
        self.meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}

    def predict_win_prob(self, state: ScoreboardState) -> float:
        """Return win probability [0.0, 1.0] for `state.my_side`."""
        x = state.to_feature_row()
        blue_prob = float(self._sess.run([self._out_name], {"X": x})[0][0, self._col])
        prob = blue_prob if state.my_side == "blue" else 1.0 - blue_prob
        return max(0.0, min(1.0, prob))
