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
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from ml.features import CONTEXT_COLUMNS, EXTENDED_FEATURES, MASKED_FEATURES, vectorize_rows

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


def _candidates() -> dict[str, Any]:
    return {
        "logistic": Pipeline([("scaler", StandardScaler()), ("clf", LogisticRegression(max_iter=1000, C=1.0, random_state=42))]),
        "gboost": GradientBoostingClassifier(n_estimators=80, max_depth=3, learning_rate=0.1, subsample=0.9, random_state=42),
    }


def _to_onnx(model: Any, path: Path) -> None:
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
        }
        scored.append(entry)
        if entry["parity_ok"] and (best is None or ll < best[0]):
            best = (ll, name, model, cand_file)

    if best is None:
        for f in out_path.glob("_candidate_*.onnx"):
            f.unlink()
        raise RuntimeError(f"ONNX parity check failed for every candidate (tol {parity_tol}): {scored}")

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

    stats = build_distribution_stats(player_rows or [])
    (out_path / "distribution_stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")

    chosen_entry = next(e for e in scored if e["model"] == chosen)
    metrics = {
        "selected": chosen,
        "n_train": int(len(train_idx)),
        "n_eval": int(len(eval_idx)),
        "n_matches": len({str(r.get("match_id") or i) for i, r in enumerate(rows)}),
        "brier": chosen_entry["brier"],
        "log_loss": chosen_entry["log_loss"],
        "auc": chosen_entry["auc"],
        "onnx_max_diff": chosen_entry["onnx_max_diff"],
        "candidates": scored,
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
    args = ap.parse_args(argv)
    snaps, players = load_jsonl(args.data)
    m = train_and_export_onnx(snaps, args.out, player_rows=players)
    print(json.dumps({k: m[k] for k in ("selected", "n_matches", "n_eval", "log_loss", "auc", "onnx_max_diff", "stats_cells")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
