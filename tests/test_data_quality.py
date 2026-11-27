"""AGENTS §28: clean-data rules (remake / old season / AFK) and the per-minute win calibration."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from ml.features import apply_time_calibration
from ml.pipeline import afk_participants, clean_rows, quality_issues
from src.predictor import LocalOnnxPredictor, ScoreboardState, _valid_calibration

ROOT = Path(__file__).resolve().parent.parent


def _timeline(xp_by_pid: dict[int, list[float]], minutes: int = 20) -> dict:
    frames = []
    for t in range(minutes):
        pf = {str(pid): {"xp": xp_by_pid.get(pid, [300.0 * t for t in range(minutes)])[t]} for pid in range(1, 11)}
        frames.append({"timestamp": t * 60000, "participantFrames": pf, "events": []})
    return {"info": {"frames": frames}}


def _row(version: str = "16.23.1.1", remake: bool = False, timeline: dict | None = None) -> dict:
    return {"queue": 420, "remake": remake, "bucket": "mid", "timeline": timeline or _timeline({}),
            "match": {"metadata": {"matchId": "abcdef123456"}, "info": {"gameVersion": version, "gameDuration": 1800, "participants": []}}}


def test_afk_is_a_four_minute_xp_stall_after_minute_three():
    stalled = [300.0 * t if t < 8 else 2100.0 for t in range(20)]  # stops gaining XP at minute 7
    assert afk_participants(_timeline({4: stalled})) == [4]
    early = [0.0 if t < 4 else 300.0 * t for t in range(20)]  # slow start in the first minutes is not AFK
    assert afk_participants(_timeline({2: early})) == []
    dead_3m = [300.0 * t if not 10 <= t <= 12 else 3000.0 for t in range(20)]  # 3 frames flat: a long death timer
    assert afk_participants(_timeline({7: dead_3m})) == []


def test_quality_issues_and_clean_rows():
    afk = _timeline({3: [300.0 * t if t < 6 else 1500.0 for t in range(20)]})
    rows = [_row(), _row("15.24.1"), _row(remake=True), _row(timeline=afk)]
    assert quality_issues(rows[0], 16) == []
    assert quality_issues(rows[1], 16) == ["old_season"]
    assert quality_issues(rows[2], 16) == ["remake"]
    assert quality_issues(rows[3], 16) == ["afk"]
    kept, q = clean_rows(rows)
    assert kept == [rows[0]] and q == {"season": 16, "kept": 1, "remake": 1, "old_season": 1, "afk": 1}


def test_js_afk_rule_matches_python():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not found")
    cases = [_timeline({}), _timeline({4: [300.0 * t if t < 8 else 2100.0 for t in range(20)]}),
             _timeline({9: [300.0 * t if not 10 <= t <= 12 else 3000.0 for t in range(20)]}),
             _timeline({1: [300.0 * t if t < 5 else 1200.0 for t in range(20)], 6: [0.0] * 20})]
    script = ("import { afkParticipants } from './worker/review.js';"
              f"console.log(JSON.stringify({json.dumps(cases)}.map(afkParticipants)));")
    out = subprocess.run([node, "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == [afk_participants(c) for c in cases]


CAL = {"edges": [7.5, 12.5, 17.5, 22.5, 27.5], "scale": [1.9, 1.0, 0.9, 0.75, 0.6, 1.0]}


def test_time_calibration_keeps_even_mirror_and_order():
    p = np.array([0.5, 0.6, 0.7, 0.3, 0.4])
    for minute in (5.0, 10.0, 20.0, 25.0, 35.0):
        m = np.full(len(p), minute)
        c = apply_time_calibration(p, m, CAL)
        assert abs(c[0] - 0.5) < 1e-12                                     # even stays 50 %
        assert np.allclose(c + apply_time_calibration(1 - p, m, CAL), 1.0)  # red view mirrors blue
        assert list(np.argsort(c)) == list(np.argsort(p))                 # never reorders states
    early = apply_time_calibration(np.array([0.7]), np.array([5.0]), CAL)[0]
    late = apply_time_calibration(np.array([0.7]), np.array([25.0]), CAL)[0]
    assert early > 0.7 > late  # scale > 1 sharpens, scale < 1 softens
    assert apply_time_calibration(p, np.full(5, 10.0), None).tolist() == p.tolist()


@pytest.mark.parametrize("bad", [None, {}, {"edges": [1, 2], "scale": [1, 1]}, {"edges": [2, 1], "scale": [1, 1, 1]},
                                 {"edges": [1], "scale": [1, -1]}, {"edges": ["x"], "scale": [1, 1]}])
def test_malformed_calibration_is_ignored(bad):
    assert _valid_calibration(bad) is None


def test_bundled_model_ships_its_calibration_and_stays_symmetric():
    pred = LocalOnnxPredictor(model_dirs=[])
    assert pred.calibration is not None and len(pred.calibration["scale"]) == 6
    for t in (5, 10, 20, 30):
        assert abs(pred.predict_win_prob(ScoreboardState(game_time_min=t)) - 0.5) < 1e-6
        blue = pred.predict_win_prob(ScoreboardState(game_time_min=t, blue_kills=4, blue_towers=2))
        red = pred.predict_win_prob(ScoreboardState(my_side="red", game_time_min=t, red_kills=4, red_towers=2))
        assert abs(blue - red) < 1e-6 and blue > 0.5


def test_corrupt_calibration_falls_back_to_raw_model(tmp_path):
    for f in ("model_extended.onnx", "distribution_stats.json"):
        shutil.copy(ROOT / "assets" / f, tmp_path / f)
    stats = json.loads((tmp_path / "distribution_stats.json").read_text())
    stats["win_calibration"] = {"edges": "garbage"}
    (tmp_path / "distribution_stats.json").write_text(json.dumps(stats))
    pred = LocalOnnxPredictor(assets_dir=tmp_path, model_dirs=[])
    assert pred.mode == "extended" and pred.calibration is None and pred.stats is not None


def test_review_flags_afk_games():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not found")
    tl = _timeline({7: [300.0 * t if t < 8 else 2100.0 for t in range(20)]})
    parts = [{"participantId": i, "puuid": f"p{i}", "teamId": 100 if i <= 5 else 200, "championName": "Ahri",
              "teamPosition": ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"][(i - 1) % 5], "win": i <= 5,
              "kills": 2, "deaths": 2, "assists": 2, "totalMinionsKilled": 100, "neutralMinionsKilled": 0, "goldEarned": 9000,
              "visionScore": 20, "totalDamageDealtToChampions": 10000, "totalDamageTaken": 10000} for i in range(1, 11)]
    match = {"metadata": {"matchId": "KR_1"}, "info": {"queueId": 420, "mapId": 11, "gameDuration": 1200, "gameVersion": "16.23.1",
                                                      "participants": parts, "teams": [{"teamId": 100, "win": True}, {"teamId": 200, "win": False}]}}
    script = ("import { analyzeMatch } from './worker/review.js'; import { REVIEW_BASELINE } from './worker/review_baseline.js';"
              f"const m = {json.dumps(match)}, t = {json.dumps(tl)};"
              "console.log(JSON.stringify(['p7','p8'].map(u => analyzeMatch(m, t, u, 'ko', REVIEW_BASELINE).data_quality)));")
    out = subprocess.run([node, "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True, check=True)
    me_afk, ally = json.loads(out.stdout)
    assert me_afk == {"afk": 1, "me_afk": True, "ally_afk": 0}
    assert ally == {"afk": 1, "me_afk": False, "ally_afk": 1}
