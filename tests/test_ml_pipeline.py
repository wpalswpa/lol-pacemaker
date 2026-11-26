from pathlib import Path

from ml.collector import extract_timeline_snapshots, is_valid_ranked_match
from ml.features import EXTENDED_FEATURES
from ml.train import train_and_export_onnx
from tests.synth import sample_match as _sample_match, sample_timeline as _sample_timeline


def test_collector_filters_remake_and_extracts_18_features():
    remake = _sample_match("KR_1", duration=200, blue_win=True)
    assert is_valid_ranked_match(remake) is False
    assert extract_timeline_snapshots(remake, _sample_timeline(True)) == []

    valid = _sample_match("KR_2", duration=1600, blue_win=True)
    snaps = extract_timeline_snapshots(valid, _sample_timeline(True), tier_bucket="high", minutes=(10, 15, 20))
    assert len(snaps) == 3
    for s in snaps:
        for f in EXTENDED_FEATURES:
            assert f in s
        assert s["tier_bucket"] == 2.0
    assert snaps[0]["voidgrubs_diff"] == 1.0


def test_train_and_export_onnx_parity_passes(tmp_path: Path):
    rows = []
    for i in range(40):
        blue_win = i % 2 == 0
        m = _sample_match(f"KR_{i}", duration=1500, blue_win=blue_win)
        tl = _sample_timeline(blue_win)
        rows.extend(extract_timeline_snapshots(m, tl, tier_bucket="mid", minutes=(10, 15, 20)))

    metrics = train_and_export_onnx(rows, out_dir=tmp_path)
    assert (tmp_path / "model_extended.onnx").exists()
    assert (tmp_path / "distribution_stats.json").exists()
    assert metrics["onnx_max_diff"] <= 1e-4
    assert len(metrics["top_patterns"]) == 5
