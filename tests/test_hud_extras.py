"""Tests for AGENTS §27: IconCache offline safety and 2999 HUD extras (item gold + timers)."""
from __future__ import annotations

from pathlib import Path

from src.engine import extract_hud_extras
from src.icon_cache import IconCache, normalize_champ_key


def test_normalize_champ_key_handles_special_names():
    assert normalize_champ_key("game_character_displayname_LeeSin") == "LeeSin"
    assert normalize_champ_key("Wukong") == "MonkeyKing"
    assert normalize_champ_key("Jarvan IV") == "JarvanIV"
    assert normalize_champ_key("K'Sante") == "KSante"
    assert normalize_champ_key("") == ""


def test_icon_cache_rejects_bad_keys_and_never_blocks(tmp_path: Path):
    cache = IconCache(cache_dir=tmp_path)
    assert cache.get_cached_path("champ", "../etc/passwd") is None
    assert cache.get_cached_path("evil", "Ahri") is None
    # Non-existent icon returns None immediately (async fetch, no blocking)
    assert cache.get_cached_path("champ", "Ahri") is None


def test_extract_hud_extras_computes_lane_gold_and_timers():
    raw = {
        "gameData": {"gameTime": 620.0, "gameMode": "CLASSIC"},
        "activePlayer": {"summonerName": "Me#KR1", "currentGold": 1450.0},
        "allPlayers": [
            {"summonerName": "Me#KR1", "team": "ORDER", "position": "MIDDLE", "championName": "Ahri", "isDead": False,
             "items": [{"price": 1100, "count": 1}, {"price": 1300, "count": 1}]},
            {"summonerName": "EnemyMid", "team": "CHAOS", "position": "MIDDLE", "championName": "Zed", "isDead": True,
             "items": [{"price": 1100, "count": 1}]},
        ],
        "events": {"Events": [{"EventName": "DragonKill", "EventTime": 400.0}]},
    }
    ex = extract_hud_extras(raw)
    assert ex["game_time_sec"] == 620
    assert ex["my_gold"] == 1450
    assert ex["ally_dead"] == 0 and ex["enemy_dead"] == 1
    assert ex["team_item_gold_diff"] == 1300
    assert ex["obj_timers"]["dragon_sec"] == 80  # 400 + 300 - 620
    assert ex["obj_timers"]["baron_sec"] == 580  # 1200 - 620
    mid = next(r for r in ex["item_compare"]["rows"] if r["lane"] == "MIDDLE")
    assert mid["blue"]["key"] == "Ahri" and mid["red"]["key"] == "Zed" and mid["diff"] == 1300


def test_single_instance_lock_wakes_existing_app():
    import threading
    from src.main import acquire_single_instance, start_wake_listener

    woken = threading.Event()
    srv = acquire_single_instance(port=47299)
    assert srv is not None
    try:
        start_wake_listener(srv, woken.set)
        second = acquire_single_instance(port=47299)
        assert second is None
        assert woken.wait(timeout=1.5)
    finally:
        srv.close()
