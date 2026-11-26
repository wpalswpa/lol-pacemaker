from src.app import parse_liveclient_data
from src.i18n import Translator
from src.predictor import LocalOnnxPredictor, ScoreboardState
from src.strategy_core import LiveSnapshot, evaluate_snapshot


def test_shotcaller_renders_localized_calls_and_filters_contradictions():
    pred = LocalOnnxPredictor()

    snap_adv = LiveSnapshot(
        game_time=1250.0,
        scoreboard=ScoreboardState(my_side="blue", blue_kills=12, red_kills=8, blue_avg_level=13.0, red_avg_level=12.0),
        ally_alive=5,
        enemy_alive=3,
        enemy_max_respawn=28.0,
        enemy_dragons=3,
        my_current_gold=1600.0,
    )
    res_en = evaluate_snapshot(snap_adv, pred, Translator("en"))
    assert res_en["lang"] == "en"
    assert "Force Objective (5v3 · 28s window)" in res_en["calls"][0]["title"]
    assert "Baron" in res_en["calls"][0]["detail"]

    snap_def = LiveSnapshot(
        game_time=1250.0,
        scoreboard=ScoreboardState(my_side="blue", blue_kills=8, red_kills=14, blue_avg_level=12.0, red_avg_level=13.0),
        ally_alive=2,
        enemy_alive=5,
        ally_dragons=3,
    )
    res_ko = evaluate_snapshot(snap_def, pred, Translator("ko"))
    assert res_ko["lang"] == "ko"
    assert all(c["kind"] != "offense" for c in res_ko["calls"])
    assert "수적 열세 수성 (2대5)" in res_ko["calls"][0]["title"]


def test_smite_advantage_and_ally_jungler_dead_warning():
    pred = LocalOnnxPredictor()
    raw = {
        "gameData": {"gameTime": 1300.0},
        "activePlayer": {"summonerName": "Faker", "currentGold": 900.0},
        "allPlayers": [
            {"summonerName": "Faker", "team": "ORDER", "position": "MIDDLE", "level": 13, "isDead": False, "scores": {"kills": 5, "assists": 4, "creepScore": 180}},
            {"summonerName": "Oner", "team": "ORDER", "position": "JUNGLE", "level": 12, "isDead": False, "scores": {"kills": 2, "assists": 6, "creepScore": 140}, "summonerSpells": {"summonerSpellOne": {"displayName": "Smite"}}},
            {"summonerName": "EnemyJG", "team": "CHAOS", "position": "JUNGLE", "level": 12, "isDead": True, "respawnTimer": 32.0, "scores": {"kills": 1, "assists": 2, "creepScore": 130}, "summonerSpells": {"summonerSpellOne": {"displayName": "Smite"}}},
        ],
        "events": {"Events": [{"EventName": "HordeKill", "KillerName": "Oner"}]},
    }
    snap = parse_liveclient_data(raw)
    assert snap.enemy_jungler_dead_sec == 32.0
    assert snap.ally_grubs == 1
    assert snap.ally_carry_lane == "MIDDLE"

    res_en = evaluate_snapshot(snap, pred, Translator("en"))
    assert "Free Baron — Enemy JG Dead (32s)" in res_en["calls"][0]["title"]

    res_ko = evaluate_snapshot(snap, pred, Translator("ko"))
    assert "바론 확정 각 — 상대 정글 이탈 (32초)" in res_ko["calls"][0]["title"]


def test_aram_mode_disables_rift_objectives_and_uses_aram_calls():
    pred = LocalOnnxPredictor()
    raw_aram = {
        "gameData": {"gameTime": 620.0, "gameMode": "ARAM", "mapNumber": 12},
        "activePlayer": {"summonerName": "Player1", "currentGold": 3100.0},
        "allPlayers": [
            {"summonerName": "Player1", "team": "ORDER", "level": 11, "isDead": False, "scores": {"kills": 6, "assists": 10, "creepScore": 45}},
            {"summonerName": "Enemy1", "team": "CHAOS", "level": 10, "isDead": True, "respawnTimer": 22.0, "scores": {"kills": 3, "assists": 4, "creepScore": 30}, "summonerSpells": {"summonerSpellOne": {"displayName": "Smite"}}},
        ],
        "events": {"Events": []},
    }
    snap = parse_liveclient_data(raw_aram)
    assert snap.game_mode == "ARAM"

    res_en = evaluate_snapshot(snap, pred, Translator("en"))
    assert res_en["game_mode"] == "ARAM"
    assert res_en["phase"] == "ARAM Teamfight"
    titles_en = " ".join(c["title"] for c in res_en["calls"])
    assert "Baron" not in titles_en and "Dragon" not in titles_en and "Voidgrubs" not in titles_en
    assert "Reset to Spend (3100G)" in titles_en

    res_ko = evaluate_snapshot(snap, pred, Translator("ko"))
    assert res_ko["phase"] == "칼바람 한타"
    assert any("골드 정비 타이밍 (3100G 보유)" in c["title"] for c in res_ko["calls"])
