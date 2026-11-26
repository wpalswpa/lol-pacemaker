"""AGENTS §19 regression gates that run without the held-out Riot data (data/eval is gitignored)."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import eval_patterns  # noqa: E402

from src.predictor import LocalOnnxPredictor, ScoreboardState  # noqa: E402


def test_shotcaller_edge_cases_all_pass():
    rep = eval_patterns.strategy_report(ROOT / "assets")
    assert rep["failed"] == [], rep


def test_bundled_model_is_side_symmetric():
    pred = LocalOnnxPredictor(assets_dir=ROOT / "assets", model_dirs=[])
    for t in (5, 10, 20, 30):
        assert abs(pred.predict_win_prob(ScoreboardState(game_time_min=t)) - 0.5) < 0.02
    even = pred.predict_win_prob(ScoreboardState(game_time_min=10))
    assert pred.predict_win_prob(ScoreboardState(game_time_min=10, blue_first_blood=1, blue_kills=1, blue_assists=1)) > even
    blue = pred.predict_win_prob(ScoreboardState(game_time_min=15, blue_kills=4, blue_cs=300, red_cs=250))
    red = pred.predict_win_prob(ScoreboardState(my_side="red", game_time_min=15, red_kills=4, red_cs=300, blue_cs=250))
    assert abs(blue - red) < 0.02


def test_bundled_model_is_monotone_in_every_advantage():
    pytest.importorskip("sklearn")
    rep = eval_patterns.model_report(ROOT / "assets", [])
    assert rep["monotone_violations"] == []


def test_side_flip_augment_negates_only_differences():
    pytest.importorskip("sklearn")
    from ml.features import EXTENDED_FEATURES
    from ml.train import side_flip_augment

    X = np.arange(len(EXTENDED_FEATURES), dtype=np.float32).reshape(1, -1) + 1
    Xa, ya = side_flip_augment(X, np.array([1]))
    assert ya.tolist() == [1, 0]
    for i, f in enumerate(EXTENDED_FEATURES):
        expect = X[0, i] if f in ("game_time_min", "tier_bucket") else -X[0, i]
        assert Xa[1, i] == expect


REVIEW_SCRIPT = r"""
import { analyzeMatch } from "./worker/review.js";
import { REVIEW_BASELINE } from "./worker/review_baseline.js";
const parts = (q) => Array.from({ length: 10 }, (_, i) => ({
  participantId: i + 1, puuid: `p${i + 1}`, teamId: i < 5 ? 100 : 200, championName: "Ahri",
  teamPosition: q === 450 ? "" : ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"][i % 5], win: i < 5,
  kills: 2, deaths: 3, assists: 4, totalMinionsKilled: 150, neutralMinionsKilled: 5, goldEarned: 10000,
  visionScore: 20, totalDamageDealtToChampions: 15000, totalDamageTaken: 18000, damageSelfMitigated: 9000,
}));
const match = (q, dur, extra = {}) => ({ metadata: { matchId: "t" }, info: { queueId: q, mapId: q === 450 ? 12 : 11, gameDuration: dur, participants: parts(q), teams: [], ...extra } });
const frames = (n) => ({ info: { frames: Array.from({ length: n }, (_, m) => ({ timestamp: m * 60000, events: [],
  participantFrames: Object.fromEntries(Array.from({ length: 10 }, (_, i) => [String(i + 1), { totalGold: 500 + m * (i < 5 ? 420 : 380), level: 1 + m / 2, currentGold: 100 }])) })) } });
const out = {
  remake: analyzeMatch(match(420, 200), frames(4), "p1", "ko", REVIEW_BASELINE),
  aram: analyzeMatch(match(450, 1100), frames(19), "p1", "ko", REVIEW_BASELINE),
  rift: analyzeMatch(match(420, 1800), frames(31), "p3", "ko", REVIEW_BASELINE),
  short: analyzeMatch(match(420, 780), frames(14), "p3", "ko", REVIEW_BASELINE),
  notl: analyzeMatch(match(420, 1800), null, "p3", "ko", REVIEW_BASELINE),
  other: analyzeMatch(match(1700, 1200, { mapId: 30 }), null, "p3", "ko", REVIEW_BASELINE),
};
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def review_out():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    res = subprocess.run([node, "--input-type=module", "-e", REVIEW_SCRIPT], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def test_review_excludes_remakes_and_unsupported_modes(review_out):
    assert review_out["remake"]["status"] == "remake" and review_out["remake"]["metrics"] == []
    assert review_out["other"]["status"] == "unsupported" and review_out["other"]["metrics"] == []


def test_review_overall_matches_web_contract(review_out):
    for key in ("rift", "aram", "notl"):
        o = review_out[key]["overall_perf"]
        assert o and {"t_score", "percentile", "top_pct", "grade"} <= set(o) and o["grade"] in {"최상", "높음", "보통", "낮음", "위험"}
        assert all(m["grade"] in {"최상", "높음", "보통", "낮음", "위험"} for m in review_out[key]["metrics"])
    assert review_out["remake"]["overall_perf"] is None


def test_review_aram_uses_aram_metrics(review_out):
    a = review_out["aram"]
    assert a["queue_group"] == "aram" and a["gold_diff_15m"] is None
    assert {m["key"] for m in a["metrics"]} == {"economy", "farm", "combat", "frontline", "survival", "damage"}


def test_review_15m_gold_needs_a_15m_frame(review_out):
    assert review_out["rift"]["gold_diff_15m"] == 15 * 420 - 15 * 380  # p3 MIDDLE vs p8 MIDDLE at the 15:00 frame
    assert review_out["short"]["gold_diff_15m"] is None
    assert review_out["notl"]["gold_diff_15m"] is None and len(review_out["notl"]["metrics"]) == 6


def test_review_coaching_sides_match_evidence(review_out):
    for key in ("rift", "aram", "short", "notl"):
        c = review_out[key]["coaching"]
        for e in c["evidence"]:
            if e["key"] == "turning_point":  # justified by a lead flip, or by a swing at least as big as usual
                assert e["basis"] == "lead_flip" or (e["z"] >= 0 if e["side"] == "strength" else e["z"] <= 0), e
            else:
                assert (e["z"] >= 0.5) if e["side"] == "strength" else (e["z"] <= -0.5), e
        assert not any("동티어" in s for s in c["strengths"] + c["improvements"] + [c["summary"]])


def test_layout_reset_and_lock_round_trip(tmp_path):
    from src.hud_state import HudSettings, load_settings, save_settings

    s = HudSettings(x=900, y=500, panels_pos={"win": [700, 300]}).toggled_locked()
    path = tmp_path / "s.json"
    save_settings(s, path)
    loaded = load_settings(path)
    assert loaded.locked is True and loaded.panels_pos == {"win": [700, 300]}
    reset = loaded.with_layout_reset()
    assert reset.panels_pos == {} and (reset.x, reset.y) == (HudSettings().x, HudSettings().y) and reset.locked is True


def test_engine_hides_after_game_and_reconnects():
    """AGENTS §24: 2 dropped polls keep the HUD, the 3rd hides it, a new match shows again; GameEnd hides at once."""
    from src.engine import Engine
    from src.i18n import Translator

    script: list = []
    seen: list = []

    def fetch():
        item = script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    eng = Engine(LocalOnnxPredictor(model_dirs=[]), Translator("en"), fetch=fetch, on_poll=lambda raw, snap: seen.append(raw))
    game = eval_patterns._game(900)
    script += [game, OSError(), OSError()]
    assert eng.step() and "win_pct" in eng.snapshot()
    assert not eng.step() and not eng.step() and "win_pct" in eng.snapshot()  # brief drop tolerated
    script += [OSError()]
    eng.step()
    assert eng.snapshot() == {"status": "waiting"}
    script += [game]
    assert eng.step() and "win_pct" in eng.snapshot()
    ended = eval_patterns._game(1500, events=[{"EventName": "GameEnd", "EventTime": 1500, "Result": "Win"}])
    script += [ended]
    assert not eng.step() and eng.snapshot() == {"status": "waiting"}
    assert seen[-1] is ended  # the contribution recorder still saw the result
