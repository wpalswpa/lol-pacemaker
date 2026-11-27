"""AGENTS §26 step 1: the live HUD briefing speaks plain language (no stats jargon, no blame for a gap we win)."""
import re

import pytest

from src.i18n import Translator
from src.predictor import LocalOnnxPredictor, ScoreboardState
from src.strategy_core import LiveSnapshot, PLAIN_FEATURES, evaluate_snapshot

LANGS = ["en", "ko", "ja", "es", "pt"]
# Words §26 rule 2 keeps out of the HUD briefing (percentiles, T-scores, %p, model vocabulary).
JARGON = {
    "ko": ["%p", "상위", "하위", "요인", "피처", "정규분포", "T점수"],
    "en": ["%p", "top ", "bottom ", "percentile", "factor", "feature", "T-score"],
    "ja": ["%p", "上位", "下位", "要因", "特徴量", "正規分布"],
    "es": ["%p", "percentil", "factor", "lastrando"],
    "pt": ["%p", "percentil", "fator", "reduzindo"],
}


@pytest.fixture(scope="module")
def pred() -> LocalOnnxPredictor:
    return LocalOnnxPredictor(model_dirs=[])  # bundled real-data model (monotone logistic, §19)


def _snap(side: str = "blue", **sb_kw) -> LiveSnapshot:
    base = dict(my_side=side, game_time_min=15.0, tier="mid", blue_kills=3, red_kills=9, blue_cs=300, red_cs=380,
                blue_avg_level=8.0, red_avg_level=9.0, red_towers=3, recent_3m_kill_diff=-3, lane_cs_diff=-60)
    base.update(sb_kw)
    return LiveSnapshot(game_time=900.0, scoreboard=ScoreboardState(**base), my_position="MIDDLE", my_cs=80, my_level=8,
                        my_kill_participation=0.3)


AHEAD = dict(blue_kills=9, red_kills=3, blue_cs=380, red_cs=300, blue_avg_level=9.0, red_avg_level=8.0,
             red_towers=0, blue_towers=3, recent_3m_kill_diff=3, lane_cs_diff=60)


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("state", ["behind", "ahead", "even"])
def test_briefing_has_no_stats_jargon_and_no_raw_keys(pred, lang, state):
    snap = {"behind": _snap(), "ahead": _snap(**AHEAD),
            "even": LiveSnapshot(game_time=600.0, scoreboard=ScoreboardState(game_time_min=10.0))}[state]
    text = evaluate_snapshot(snap, pred, Translator(lang))["smart_briefing"]
    for word in JARGON[lang]:
        assert word.lower() not in text.lower(), (lang, word, text)
    assert "{" not in text and not re.search(r"\b(why|fix|me|feat|metric)_[a-z_0-9]+\b", text), text


@pytest.mark.parametrize("side", ["blue", "red"])
def test_cause_sentence_names_a_gap_my_team_is_losing_with_the_real_number(pred, side):
    # Mirror the same game for red so "my team" is always the one behind.
    snap = _snap() if side == "blue" else _snap(
        side="red", blue_kills=9, red_kills=3, blue_cs=380, red_cs=300, blue_avg_level=9.0, red_avg_level=8.0,
        red_towers=0, blue_towers=3, recent_3m_kill_diff=3, lane_cs_diff=60)
    res = evaluate_snapshot(snap, pred, Translator("en"))
    top_feature, top_delta = res["causes"][0]["feature"], res["causes"][0]["delta_pp"]
    assert top_feature in PLAIN_FEATURES and top_delta <= -1.0
    gap = {"kills_diff": 6, "cs_diff": 80, "towers_diff": 3, "recent_3m_kill_diff": 3, "lane_cs_diff": 60,
           "avg_level_diff": "1.0"}.get(top_feature)
    if gap is not None:
        assert Translator("en").t(f"why_{top_feature}", n=str(gap)) in res["smart_briefing"]
    # counterfactual: "from {now}% to {even}%" uses the model's own numbers (win - delta)
    now, even = round(res["win_pct"]), round(min(99.0, res["win_pct"] - top_delta))
    assert f"from {now}% to {even}%" in res["smart_briefing"]
    assert even > now


def test_winning_gap_is_never_blamed(pred):
    # My team is ahead everywhere: nothing should be phrased as "you have fewer ...".
    snap = _snap(**AHEAD)
    text = evaluate_snapshot(snap, pred, Translator("en"))["smart_briefing"]
    tr = Translator("en")
    assert tr.t("briefing_cause_none") in text
    for f in PLAIN_FEATURES:
        for n in ("1", "1.0", "3", "6", "60", "80"):
            assert tr.t(f"why_{f}", n=n) not in text, (f, text)


def test_every_plain_feature_has_why_and_fix_in_all_languages():
    for lang in LANGS:
        tr = Translator(lang)
        for f in PLAIN_FEATURES:
            why, fix = tr.t(f"why_{f}", n="2"), tr.t(f"fix_{f}")
            assert "2" in why and why != f"why_{f}" and fix != f"fix_{f}", (lang, f)
