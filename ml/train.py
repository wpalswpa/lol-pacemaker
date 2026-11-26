"""Train 18-feature win-rate model, extract feature patterns, and verify ONNX parity."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime as ort
from skl2onnx import convert_sklearn
from skl2onnx.common.data_types import FloatTensorType
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from ml.features import EXTENDED_FEATURES, vectorize_rows


def train_and_export_onnx(
    rows: list[dict[str, float | int]],
    out_dir: Path | str,
    parity_tol: float = 1e-4,
) -> dict[str, Any]:
    """Train StandardScaler + LogisticRegression pipeline, export ONNX, and verify parity."""
    if len(rows) < 20:
        raise ValueError(f"Need at least 20 snapshots to train/eval, got {len(rows)}")

    X = vectorize_rows(rows)
    y = np.array([int(r["blue_win"]) for r in rows], dtype=np.int64)

    split = max(10, int(len(rows) * 0.8))
    X_train, X_eval = X[:split], X[split:]
    y_train, y_eval = y[:split], y[split:]

    pipe = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(max_iter=1000, C=1.0, random_state=42)),
        ]
    )
    pipe.fit(X_train, y_train)

    sk_probs = pipe.predict_proba(X_eval)[:, 1]
    brier = float(brier_score_loss(y_eval, sk_probs))
    ll = float(log_loss(y_eval, sk_probs, labels=[0, 1]))
    auc = float(roc_auc_score(y_eval, sk_probs)) if len(set(y_eval.tolist())) > 1 else 0.5

    # Export to ONNX with embedded StandardScaler
    onx = convert_sklearn(
        pipe,
        initial_types=[("X", FloatTensorType([None, len(EXTENDED_FEATURES)]))],
        options={id(pipe): {"zipmap": False}},
    )
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    onnx_file = out_path / "model_extended.onnx"
    onnx_file.write_bytes(onx.SerializeToString())

    # Parity check against sklearn
    sess = ort.InferenceSession(str(onnx_file), providers=["CPUExecutionProvider"])
    onnx_probs = sess.run(["probabilities"], {"X": X_eval})[0][:, 1]
    max_diff = float(np.max(np.abs(sk_probs - onnx_probs)))
    if max_diff > parity_tol:
        raise RuntimeError(f"ONNX parity check failed: max_diff={max_diff} > {parity_tol}")

    # Extract standardized pattern weights (top drivers of win probability)
    coefs = pipe.named_steps["clf"].coef_[0]
    patterns = sorted(
        [{"feature": f, "weight": round(float(w), 4)} for f, w in zip(EXTENDED_FEATURES, coefs)],
        key=lambda item: abs(item["weight"]),
        reverse=True,
    )

    # Save normal distribution baseline (mean, std) per feature for Top-X% player ranking
    scaler = pipe.named_steps["scaler"]
    dist_stats = {
        f: {"mean": round(float(m), 4), "std": round(float(s), 4)}
        for f, m, s in zip(EXTENDED_FEATURES, scaler.mean_, scaler.scale_)
    }
    (out_path / "distribution_stats.json").write_text(json.dumps(dist_stats, indent=2), encoding="utf-8")

    metrics = {
        "n_train": int(len(X_train)),
        "n_eval": int(len(X_eval)),
        "brier": round(brier, 4),
        "log_loss": round(ll, 4),
        "auc": round(auc, 4),
        "onnx_max_diff": max_diff,
        "top_patterns": patterns[:5],
    }
    (out_path / "metrics_extended.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics
