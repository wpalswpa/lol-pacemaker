"""Train the 18-feature win-rate model, compare candidates, verify ONNX parity, build tier baselines.

Spec: AGENTS.md 10.3 / 10.4. Splits by *match* so snapshots of one game never straddle train/eval.
"""
from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime as ort
from skl2onnx import convert_sklearn
from skl2onnx.common.data_types import FloatTensorType
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from ml.features import (CALIB_BOUNDS, CALIB_EDGES, CONTEXT_COLUMNS, EXTENDED_FEATURES, MASKED_FEATURES, _logit,
                         apply_time_calibration, time_bin, vectorize_rows)

__all__ = ["apply_time_calibration", "fit_time_calibration", "time_bin"]

STATS_METRICS = ("cs_per_min", "level", "kill_participation")
MIN_N = 30


def split_by_match(rows: list[dict[str, Any]], eval_fraction: float = 0.2, seed: int = 42) -> tuple[list[int], list[int]]:
    """Return (train_idx, eval_idx) with every match entirely on one side."""
    groups: dict[str, list[int]] = {}
    for i, r in enumerate(rows):
        groups.setdefault(str(r.get("match_id") or f"row{i}"), []).append(i)
    ids = sorted(groups)
    random.Random(seed).shuffle(ids)
    n_eval = max(1, int(round(len(ids) * eval_fraction)))
    eval_ids = set(ids[:n_eval])
    eval_idx = [i for g in eval_ids for i in groups[g]]
    train_idx = [i for g in ids[n_eval:] for i in groups[g]]
    return sorted(train_idx), sorted(eval_idx)


def side_flip_augment(X: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """AGENTS §19: append the red-side view of every training row.

    Every non-context feature is a blue-minus-red difference, so the same game seen from red is
    (-diff, context unchanged, 1 - label). Training on both views forces P(blue | even) = 0.5 and
    removes the side/first-blood bias a small sample can carry. Applied to the training split only.
    """
    ctx = np.array([f in CONTEXT_COLUMNS for f in EXTENDED_FEATURES])
    flipped = X.copy()
    flipped[:, ~ctx] = -flipped[:, ~ctx]
    return np.vstack([X, flipped]).astype(np.float32), np.concatenate([y, 1 - y])


def build_distribution_stats(player_rows: list[dict[str, Any]], min_n: int = MIN_N) -> dict[str, Any]:
    """tier -> minute -> role(+ALL) -> metric -> {mean, std, n}; cells with n < min_n are dropped."""
    cells: dict[tuple[str, int, str], dict[str, list[float]]] = {}
    for r in player_rows:
        tier, minute = str(r["tier"]), int(r["minute"])
        for role in {str(r.get("role") or "ALL"), "ALL"}:
            cell = cells.setdefault((tier, minute, role), {m: [] for m in STATS_METRICS})
            for m in STATS_METRICS:
                v = r.get(m)
                if v is not None and math.isfinite(float(v)):
                    cell[m].append(float(v))
    tiers: dict[str, Any] = {}
    for (tier, minute, role), metrics in cells.items():
        out = {}
        for m, vals in metrics.items():
            if len(vals) < min_n:
                continue
            arr = np.array(vals, dtype=np.float64)
            std = float(arr.std())
            if std <= 1e-9:
                continue
            out[m] = {"mean": round(float(arr.mean()), 4), "std": round(std, 4), "n": int(len(vals))}
        if out:
            tiers.setdefault(tier, {}).setdefault(str(minute), {})[role] = out
    return {"schema": 1, "min_n": min_n, "tiers": tiers}


# Every non-context feature is a blue-minus-red advantage: its coefficient must be >= 0 (AGENTS §19).
ADVANTAGE_FEATURES = [f for f in EXTENDED_FEATURES if f not in CONTEXT_COLUMNS and f not in MASKED_FEATURES]


class MonotoneLogistic(ClassifierMixin, BaseEstimator):
    """L2 logistic regression (same objective as sklearn, C=1) with advantage coefficients bounded >= 0.

    Exported through a plain StandardScaler + LogisticRegression pipeline, so ONNX conversion is unchanged.
    """

    def __init__(self, C: float = 1.0) -> None:
        self.C = C

    def fit(self, X: np.ndarray, y: np.ndarray) -> "MonotoneLogistic":
        from scipy.optimize import minimize

        scaler = StandardScaler().fit(X)
        Z = scaler.transform(X).astype(np.float64)
        y = np.asarray(y, dtype=np.float64)
        n_feat = Z.shape[1]

        def obj(theta: np.ndarray) -> tuple[float, np.ndarray]:
            w, b = theta[:n_feat], theta[n_feat]
            m = Z @ w + b
            p = 1.0 / (1.0 + np.exp(-m))
            loss = 0.5 * float(w @ w) + self.C * float(np.sum(np.logaddexp(0.0, m) - y * m))
            g = self.C * (p - y)
            return loss, np.concatenate([w + Z.T @ g, [g.sum()]])

        bounds = [(0.0, None) if f in ADVANTAGE_FEATURES else ((0.0, 0.0) if f in MASKED_FEATURES else (None, None)) for f in EXTENDED_FEATURES]
        res = minimize(obj, np.zeros(n_feat + 1), jac=True, method="L-BFGS-B", bounds=bounds + [(None, None)])
        clf = LogisticRegression()
        clf.classes_ = np.array([0, 1])
        clf.coef_ = res.x[:n_feat].reshape(1, -1)
        clf.intercept_ = np.array([res.x[n_feat]])
        clf.n_features_in_ = n_feat
        self.pipeline_ = Pipeline([("scaler", scaler), ("clf", clf)])
        self.classes_ = clf.classes_
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return self.pipeline_.predict_proba(X)

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.pipeline_.predict(X)


def monotone_violations(model: Any, tol: float = 1e-6) -> list[str]:
    """Advantage features whose +1 lowers blue win probability at 10/15/20/25 min, any tier (AGENTS §19)."""
    bad = []
    for minute in (10.0, 15.0, 20.0, 25.0):
        for tier in (0.0, 1.0, 2.0):
            base = {f: 0.0 for f in EXTENDED_FEATURES} | {"game_time_min": minute, "tier_bucket": tier}
            rows = [base] + [base | {f: 1.0} for f in ADVANTAGE_FEATURES]
            p = model.predict_proba(vectorize_rows(rows))[:, 1]
            bad += [f for f, v in zip(ADVANTAGE_FEATURES, p[1:]) if v < p[0] - tol]
    return sorted(set(bad))


def fit_time_calibration(make_model: Any, X: np.ndarray, y: np.ndarray, groups: list[str], augment: bool,
                         folds: int = 5, seed: int = 42) -> dict[str, Any] | None:
    """Out-of-fold (by match) predictions on the training split -> one logit scale per time bin."""
    from scipy.optimize import minimize_scalar

    ids = sorted(set(groups))
    if len(ids) < folds * 4:
        return None
    random.Random(seed).shuffle(ids)
    fold_of = {g: i % folds for i, g in enumerate(ids)}
    fold = np.array([fold_of[g] for g in groups])
    oof = np.full(len(y), np.nan)
    for k in range(folds):
        tr, te = fold != k, fold == k
        Xk, yk = (side_flip_augment(X[tr], y[tr]) if augment else (X[tr], y[tr]))
        if len(set(yk.tolist())) < 2:
            return None
        oof[te] = make_model().fit(Xk, yk).predict_proba(X[te])[:, 1]
    z, bins = _logit(oof), time_bin(X[:, 0])
    scale, counts = [], []
    for b in range(len(CALIB_EDGES) + 1):
        m = bins == b
        counts.append(int(m.sum()))
        if m.sum() < 30:
            scale.append(1.0)
            continue
        zb, yb = z[m], y[m].astype(np.float64)
        nll = lambda a, zb=zb, yb=yb: float(np.mean(np.logaddexp(0.0, a * zb) - yb * a * zb))  # noqa: E731
        scale.append(round(float(minimize_scalar(nll, bounds=CALIB_BOUNDS, method="bounded").x), 4))
    return {"schema": 1, "kind": "logit_scale_by_minute", "edges": list(CALIB_EDGES), "scale": scale, "n": counts,
            "fit": f"{folds}-fold out-of-fold by match on the training split"}


def _candidates() -> dict[str, Any]:
    return {
        "logistic": MonotoneLogistic(C=1.0),
        "gboost": GradientBoostingClassifier(n_estimators=80, max_depth=3, learning_rate=0.1, subsample=0.9, random_state=42),
    }


def _to_onnx(model: Any, path: Path) -> None:
    model = getattr(model, "pipeline_", model)
    onx = convert_sklearn(
        model,
        initial_types=[("X", FloatTensorType([None, len(EXTENDED_FEATURES)]))],
        options={id(model): {"zipmap": False}},
    )
    path.write_bytes(onx.SerializeToString())


def train_and_export_onnx(
    rows: list[dict[str, Any]],
    out_dir: Path | str,
    parity_tol: float = 1e-4,
    player_rows: list[dict[str, Any]] | None = None,
    augment_sides: bool = True,
    time_calibration: bool = True,
) -> dict[str, Any]:
    """Compare logistic vs. small boosted trees, export the best ONNX that passes parity."""
    if len(rows) < 20:
        raise ValueError(f"Need at least 20 snapshots to train/eval, got {len(rows)}")
    train_idx, eval_idx = split_by_match(rows)
    X = vectorize_rows(rows)
    y = np.array([int(r["blue_win"]) for r in rows], dtype=np.int64)
    X_tr, y_tr, X_ev, y_ev = X[train_idx], y[train_idx], X[eval_idx], y[eval_idx]
    if len(set(y_tr.tolist())) < 2:
        raise ValueError("Training split contains a single class; need wins and losses")
    if augment_sides:
        X_tr, y_tr = side_flip_augment(X_tr, y_tr)

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    scored: list[dict[str, Any]] = []
    best: tuple[float, str, Any, Path] | None = None
    for name, model in _candidates().items():
        model.fit(X_tr, y_tr)
        sk = model.predict_proba(X_ev)[:, 1]
        ll = float(log_loss(y_ev, sk, labels=[0, 1]))
        cand_file = out_path / f"_candidate_{name}.onnx"
        _to_onnx(model, cand_file)
        sess = ort.InferenceSession(str(cand_file), providers=["CPUExecutionProvider"])
        onnx_p = sess.run(["probabilities"], {"X": X_ev})[0][:, 1]
        diff = float(np.max(np.abs(sk - onnx_p)))
        entry = {
            "model": name,
            "log_loss": round(ll, 4),
            "brier": round(float(brier_score_loss(y_ev, sk)), 4),
            "auc": round(float(roc_auc_score(y_ev, sk)), 4) if len(set(y_ev.tolist())) > 1 else 0.5,
            "onnx_max_diff": diff,
            "parity_ok": diff <= parity_tol,
            "monotone_violations": monotone_violations(model),
        }
        scored.append(entry)
        if entry["parity_ok"] and not entry["monotone_violations"] and (best is None or ll < best[0]):
            best = (ll, name, model, cand_file)

    if best is None:
        for f in out_path.glob("_candidate_*.onnx"):
            f.unlink()
        raise RuntimeError(f"No candidate passed ONNX parity (tol {parity_tol}) and the monotonicity check: {scored}")

    _, chosen, model, cand_file = best
    (out_path / "model_extended.onnx").write_bytes(cand_file.read_bytes())
    for f in out_path.glob("_candidate_*.onnx"):
        f.unlink()

    skip = set(MASKED_FEATURES) | set(CONTEXT_COLUMNS)
    imp = permutation_importance(model, X_ev, y_ev, scoring="neg_log_loss", n_repeats=5, random_state=42)
    patterns = sorted(
        (
            {"feature": f, "importance": round(float(m), 5)}
            for f, m in zip(EXTENDED_FEATURES, imp.importances_mean)
            if f not in skip
        ),
        key=lambda d: d["importance"],
        reverse=True,
    )

    calib = None
    if time_calibration:
        groups = [str(rows[i].get("match_id") or f"row{i}") for i in train_idx]
        calib = fit_time_calibration(lambda: _candidates()[chosen], X[train_idx], y[train_idx], groups, augment_sides)
    raw_ev = model.predict_proba(X_ev)[:, 1]
    cal_ev = apply_time_calibration(raw_ev, X_ev[:, 0], calib)

    stats = build_distribution_stats(player_rows or [])
    if calib:
        stats["win_calibration"] = calib  # AGENTS §28: shipped with the model by the updater, applied by the predictor
    (out_path / "distribution_stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")

    chosen_entry = next(e for e in scored if e["model"] == chosen)
    metrics = {
        "selected": chosen,
        "n_train": int(len(train_idx)),
        "side_flip_augment": bool(augment_sides),
        "n_eval": int(len(eval_idx)),
        "n_matches": len({str(r.get("match_id") or i) for i, r in enumerate(rows)}),
        "brier": chosen_entry["brier"],
        "log_loss": chosen_entry["log_loss"],
        "auc": chosen_entry["auc"],
        "onnx_max_diff": chosen_entry["onnx_max_diff"],
        "candidates": scored,
        "time_calibration": calib,
        "eval_brier_raw_vs_calibrated": [round(float(brier_score_loss(y_ev, raw_ev)), 4), round(float(brier_score_loss(y_ev, cal_ev)), 4)],
        "top_patterns": patterns[:5],
        "stats_cells": sum(len(roles) for mins in stats["tiers"].values() for roles in mins.values()),
    }
    (out_path / "metrics_extended.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics


def main(argv: list[str] | None = None) -> int:
    from ml.collector import load_jsonl

    ap = argparse.ArgumentParser(description="Train from collector JSONL and export ONNX + distribution_stats.json")
    ap.add_argument("--data", nargs="+", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("build/model"))
    ap.add_argument("--no-side-flip", action="store_true", help="disable AGENTS §19 side-flip augmentation")
    args = ap.parse_args(argv)
    snaps, players = load_jsonl(args.data)
    m = train_and_export_onnx(snaps, args.out, player_rows=players, augment_sides=not args.no_side_flip)
    print(json.dumps({k: m[k] for k in ("selected", "n_matches", "n_eval", "log_loss", "auc", "onnx_max_diff", "stats_cells")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
