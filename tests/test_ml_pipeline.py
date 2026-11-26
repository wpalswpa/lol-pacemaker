from pathlib import Path

from ml.collector import extract_timeline_snapshots, is_valid_ranked_match
from ml.features import EXTENDED_FEATURES
from ml.train import train_and_export_onnx


def _sample_match(match_id: str, duration: int, blue_win: bool) -> dict:
    parts = [
        {"participantId": i, "teamId": 100 if i <= 5 else 200, "teamPosition": pos}
        for i, pos in zip(range(1, 11), ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"] * 2)
    ]
    return {
        "metadata": {"matchId": match_id},
        "info": {
            "queueId": 420,
            "gameDuration": duration,
            "participants": parts,
            "teams": [{"teamId": 100, "win": blue_win}, {"teamId": 200, "win": not blue_win}],
        },
    }


def _sample_timeline(blue_ahead: bool) -> dict:
    frames = []
    for m in (0, 10, 15, 20):
        pframes = {}
        for pid in range(1, 11):
            is_blue = pid <= 5
            cs = (m * 7 + (15 if (is_blue == blue_ahead) else 0)) if m > 0 else 0
            lvl = max(1, m // 2 + (1 if (is_blue == blue_ahead) else 0))
            pframes[str(pid)] = {"minionsKilled": cs, "jungleMinionsKilled": 0, "level": lvl}
        evs = []
        if m == 10:
            kid = 1 if blue_ahead else 6
            evs.append({"timestamp": 300_000, "type": "CHAMPION_KILL", "killerId": kid, "assistingParticipantIds": [kid + 1]})
            evs.append({"timestamp": 540_000, "type": "ELITE_MONSTER_KILL", "killerTeamId": 100 if blue_ahead else 200, "monsterType": "HORDE"})
        frames.append({"timestamp": m * 60_000, "participantFrames": pframes, "events": evs})
    return {"info": {"frames": frames}}


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
