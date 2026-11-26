import json
import re
from pathlib import Path

import pytest

from ml.train import train_and_export_onnx
from src.i18n import Translator
from src.percentile import lookup_percentiles, nearest_minute
from src.predictor import LocalOnnxPredictor, ScoreboardState
from src.strategy_core import LiveSnapshot, evaluate_snapshot
from tests.synth import many_rows

LANGS = ["en", "ko", "ja", "es", "pt"]


@pytest.fixture(scope="module")
def trained_dir(tmp_path_factory) -> Path:
    rows, players = many_rows(60)
    d = tmp_path_factory.mktemp("model")
    train_and_export_onnx(rows, d, player_rows=players)
    return d


def _behind_snapshot(**kw) -> LiveSnapshot:
    sb = ScoreboardState(my_side="blue", game_time_min=10.0, tier="mid", blue_kills=1, red_kills=6, blue_assists=2, red_assists=9,
                         blue_cs=180, red_cs=240, blue_avg_level=6.0, red_avg_level=7.2, red_dragons=1, red_towers=1,
                         recent_3m_kill_diff=-2, lane_cs_diff=-40)
    base = dict(game_time=600.0, scoreboard=sb, ally_alive=4, enemy_alive=5, my_position="MIDDLE", my_cs=45, my_level=6,
                my_kill_participation=0.25)
    base.update(kw)
    return LiveSnapshot(**base)


@pytest.mark.parametrize("lang", LANGS)
def test_extended_model_briefing_renders_in_every_language(trained_dir, lang):
    pred = LocalOnnxPredictor(model_dirs=[trained_dir])
    assert pred.mode == "extended" and pred.stats
    res = evaluate_snapshot(_behind_snapshot(), pred, Translator(lang))

    assert res["lang"] == lang and res["model_mode"] == "extended" and res["rank_mode"] == "distribution"
    assert 0.0 <= res["win_pct"] <= 100.0
    assert res["causes"] and res["causes"][0]["delta_pp"] < 0
    # percentages from the stored tier distribution appear in the summary and the briefing
    assert re.search(r"\d+%", res["rank_summary"]) and res["smart_briefing"]
    for text in [res["rank_summary"], res["smart_briefing"], res["labels"]["model"], *[c["title"] + c["detail"] for c in res["calls"]],
                 *[c["label"] for c in res["causes"]]]:
        assert "{" not in text and "}" not in text, text          # unformatted placeholder
        assert not re.search(r"\b(feat|metric|tier|briefing|rank|hud)_[a-z_0-9]+\b", text), text  # raw key leaked


def test_languages_are_actually_translated_not_english_fallback(trained_dir):
    pred = LocalOnnxPredictor(model_dirs=[trained_dir])
    out = {lang: evaluate_snapshot(_behind_snapshot(), pred, Translator(lang)) for lang in LANGS}
    for lang in LANGS[1:]:
        assert out[lang]["smart_briefing"] != out["en"]["smart_briefing"], lang
        assert out[lang]["rank_summary"] != out["en"]["rank_summary"], lang
    assert re.search(r"[가-힣]", out["ko"]["smart_briefing"]) and re.search(r"[ぁ-んァ-ン一-龥]", out["ja"]["smart_briefing"])


def test_legacy_model_still_renders_all_languages_without_stats():
    pred = LocalOnnxPredictor()  # tests run with an empty user home -> bundled 8-feature model
    assert pred.mode == "legacy8" and pred.stats is None
    for lang in LANGS:
        res = evaluate_snapshot(_behind_snapshot(), pred, Translator(lang))
        assert res["rank_mode"] == "legacy" and res["model_mode"] == "legacy8"
        assert "{" not in res["smart_briefing"] and res["causes"]


def test_explain_signs_follow_my_side(trained_dir):
    pred = LocalOnnxPredictor(model_dirs=[trained_dir])
    blue_ahead = ScoreboardState(my_side="blue", blue_kills=8, red_kills=2, blue_cs=300, red_cs=250, blue_avg_level=8, red_avg_level=7)
    names_blue = dict(pred.explain(blue_ahead))
    names_red = dict(pred.explain(blue_ahead.model_copy(update={"my_side": "red"})))
    assert names_blue["kills_diff"] > 0 > names_red["kills_diff"]
    assert abs(names_blue["kills_diff"] + names_red["kills_diff"]) < 1e-2  # exact mirror
    assert "item_gold_diff" not in names_blue and "game_time_min" not in names_blue  # masked/context never reported


def test_explain_even_state_is_empty_and_briefing_says_no_drag(trained_dir):
    pred = LocalOnnxPredictor(model_dirs=[trained_dir])
    even = LiveSnapshot(game_time=600.0, scoreboard=ScoreboardState(game_time_min=10.0))
    assert pred.explain(even.scoreboard) == []
    res = evaluate_snapshot(even, pred, Translator("en"))
    assert Translator("en").t("briefing_cause_none") in res["smart_briefing"]


STATS = {"tiers": {"mid": {"10": {"ALL": {"cs_per_min": {"mean": 6.0, "std": 1.0, "n": 50}},
                                  "MIDDLE": {"cs_per_min": {"mean": 7.0, "std": 1.0, "n": 50}, "level": {"mean": 7.0, "std": 0.5, "n": 50}}},
                         "20": {"ALL": {"cs_per_min": {"mean": 7.0, "std": 1.0, "n": 50}}}}}}


def test_percentile_lookup_edges():
    assert lookup_percentiles(None, "mid", 600, "MIDDLE", {"cs_per_min": 7.0}) is None
    assert lookup_percentiles(STATS, "mid", 300, "MIDDLE", {"cs_per_min": 7.0}) is None  # too early
    assert lookup_percentiles(STATS, "mid", 4000, "MIDDLE", {"cs_per_min": 7.0}) is None  # too late
    assert lookup_percentiles(STATS, "high", 600, "MIDDLE", {"cs_per_min": 7.0}) is None  # tier missing
    mid = lookup_percentiles(STATS, "mid", 600, "MIDDLE", {"cs_per_min": 7.0, "level": 7.0, "kill_participation": None})
    assert mid == {"minute": 10, "role": "MIDDLE", "pct": {"cs_per_min": 50, "level": 50}}
    fallback = lookup_percentiles(STATS, "mid", 600, "TOP", {"cs_per_min": 8.0})  # role missing -> ALL
    assert fallback["role"] == "ALL" and fallback["pct"]["cs_per_min"] < 50  # better than the mean -> small Top-%
    assert lookup_percentiles(STATS, "mid", 1150, None, {"cs_per_min": 7.0})["minute"] == 20  # nearest stored minute
    assert nearest_minute(600, []) is None
    assert lookup_percentiles({"tiers": "garbage"}, "mid", 600, None, {"cs_per_min": 1}) is None


def test_corrupt_stats_file_does_not_break_loading(trained_dir, tmp_path):
    d = tmp_path / "m"
    d.mkdir()
    (d / "model_extended.onnx").write_bytes((trained_dir / "model_extended.onnx").read_bytes())
    (d / "distribution_stats.json").write_text("{not json")
    pred = LocalOnnxPredictor(model_dirs=[d])
    assert pred.mode == "extended" and pred.stats is None
    assert evaluate_snapshot(_behind_snapshot(), pred, Translator("en"))["rank_mode"] == "legacy"
