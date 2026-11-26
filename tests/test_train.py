import json
from pathlib import Path

import pytest

from ml.train import build_distribution_stats, split_by_match, train_and_export_onnx
from tests.synth import many_rows


def test_split_never_puts_one_match_on_both_sides():
    rows, _ = many_rows(30)
    tr, ev = split_by_match(rows)
    tr_ids = {rows[i]["match_id"] for i in tr}
    ev_ids = {rows[i]["match_id"] for i in ev}
    assert tr_ids and ev_ids and not (tr_ids & ev_ids)
    assert len(tr) + len(ev) == len(rows)


def test_train_compares_models_and_all_artifacts_are_consistent(tmp_path: Path):
    rows, players = many_rows(40)
    m = train_and_export_onnx(rows, tmp_path, player_rows=players)
    assert {c["model"] for c in m["candidates"]} == {"logistic", "gboost"}
    assert all(c["parity_ok"] and c["onnx_max_diff"] <= 1e-4 for c in m["candidates"])
    assert m["selected"] == min((c for c in m["candidates"]), key=lambda c: c["log_loss"])["model"]
    assert m["onnx_max_diff"] <= 1e-4 and m["n_matches"] == 40
    reported = {p["feature"] for p in m["top_patterns"]}
    assert not reported & {"item_gold_diff", "alive_diff", "game_time_min", "tier_bucket"}
    assert not list(tmp_path.glob("_candidate_*"))                      # temp candidates cleaned up
    assert json.loads((tmp_path / "metrics_extended.json").read_text())["selected"] == m["selected"]
    stats = json.loads((tmp_path / "distribution_stats.json").read_text())
    assert stats["schema"] == 1 and "mid" in stats["tiers"]


def test_distribution_stats_schema_and_min_n():
    rows = [{"tier": "low", "minute": 10, "role": "TOP", "cs_per_min": 5.0 + (i % 5) * 0.2, "level": 6.0 + i % 3, "kill_participation": None}
            for i in range(40)]
    rows += [{"tier": "high", "minute": 10, "role": "TOP", "cs_per_min": 8.0, "level": 7.0, "kill_participation": 0.5}] * 5  # too few
    st = build_distribution_stats(rows, min_n=30)
    cell = st["tiers"]["low"]["10"]["TOP"]
    assert set(cell) == {"cs_per_min", "level"}                     # kill_participation: no values -> omitted
    assert cell["cs_per_min"]["n"] == 40 and cell["cs_per_min"]["std"] > 0
    assert "ALL" in st["tiers"]["low"]["10"]
    assert "high" not in st["tiers"]                                  # n < min_n -> not stored
    assert build_distribution_stats([])["tiers"] == {}


def test_edge_cases_fail_loudly(tmp_path: Path):
    rows, _ = many_rows(40)
    with pytest.raises(ValueError):
        train_and_export_onnx(rows[:5], tmp_path)
    with pytest.raises(ValueError):                                  # one class only
        train_and_export_onnx([{**r, "blue_win": 1} for r in rows], tmp_path)
    with pytest.raises(RuntimeError, match="parity"):
        train_and_export_onnx(rows, tmp_path, parity_tol=-1.0)
    assert not (tmp_path / "model_extended.onnx").exists()           # nothing half-written is left as "the model"
