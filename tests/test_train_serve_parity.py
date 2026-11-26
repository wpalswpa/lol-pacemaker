"""The same game described as MATCH-V5 Timeline (training) and 2999 Live JSON (serving) must give
identical values for every feature that is NOT masked (AGENTS.md 10.1)."""
from ml.collector import extract_timeline_snapshots
from ml.features import CONTEXT_COLUMNS, EXTENDED_FEATURES, MASKED_FEATURES, vectorize_row
from src.engine import parse_liveclient_data
from tests.synth import ROLES, sample_match

CS = {1: 80, 2: 60, 3: 90, 4: 95, 5: 20, 6: 70, 7: 55, 8: 85, 9: 80, 10: 25}
LVL = {1: 8, 2: 7, 3: 8, 4: 8, 5: 6, 6: 7, 7: 7, 8: 8, 9: 7, 10: 6}
# (time_sec, killer, assists)
KILLS = [(120, 1, [2, 3]), (400, 7, [8]), (500, 2, [1]), (560, 6, []), (580, 3, [])]


def _timeline():
    ev = []
    for t, k, a in KILLS:
        ev.append({"timestamp": t * 1000, "type": "CHAMPION_KILL", "killerId": k, "assistingParticipantIds": a})
    ev += [
        {"timestamp": 200_000, "type": "ELITE_MONSTER_KILL", "killerTeamId": 100, "monsterType": "DRAGON", "monsterSubType": "EARTH_DRAGON"},
        {"timestamp": 210_000, "type": "ELITE_MONSTER_KILL", "killerTeamId": 200, "monsterType": "DRAGON", "monsterSubType": "OCEAN_DRAGON"},
        {"timestamp": 220_000, "type": "ELITE_MONSTER_KILL", "killerTeamId": 100, "monsterType": "DRAGON", "monsterSubType": "ELDER_DRAGON"},
        {"timestamp": 230_000, "type": "ELITE_MONSTER_KILL", "killerTeamId": 200, "monsterType": "RIFTHERALD"},
        {"timestamp": 240_000, "type": "ELITE_MONSTER_KILL", "killerTeamId": 100, "monsterType": "HORDE"},
        {"timestamp": 250_000, "type": "ELITE_MONSTER_KILL", "killerTeamId": 100, "monsterType": "HORDE"},
        {"timestamp": 260_000, "type": "ELITE_MONSTER_KILL", "killerTeamId": 200, "monsterType": "BARON_NASHOR"},
        {"timestamp": 270_000, "type": "BUILDING_KILL", "teamId": 200, "buildingType": "TOWER_BUILDING"},
        {"timestamp": 280_000, "type": "BUILDING_KILL", "teamId": 200, "buildingType": "TOWER_BUILDING"},
        {"timestamp": 290_000, "type": "BUILDING_KILL", "teamId": 100, "buildingType": "TOWER_BUILDING"},
        {"timestamp": 295_000, "type": "BUILDING_KILL", "teamId": 200, "buildingType": "INHIBITOR_BUILDING"},
    ]
    pf = {str(p): {"minionsKilled": CS[p] - 5, "jungleMinionsKilled": 5, "level": LVL[p]} for p in range(1, 11)}
    return {"info": {"frames": [{"timestamp": 0, "participantFrames": {}, "events": []},
                                {"timestamp": 600_000, "participantFrames": pf, "events": ev}]}}


def _live(my_name: str):
    kills = {p: 0 for p in range(1, 11)}
    assists = {p: 0 for p in range(1, 11)}
    for _, k, a in KILLS:
        kills[k] += 1
        for x in a:
            assists[x] += 1
    players = [
        {"summonerName": f"P{p}", "team": "ORDER" if p <= 5 else "CHAOS", "position": ROLES[(p - 1) % 5], "level": LVL[p],
         "isDead": False, "scores": {"kills": kills[p], "assists": assists[p], "creepScore": CS[p]}}
        for p in range(1, 11)
    ]
    events = [{"EventName": "FirstBlood", "Recipient": "P1"}]
    for t, k, _ in KILLS:
        events.append({"EventName": "ChampionKill", "KillerName": f"P{k}", "EventTime": float(t)})
    events += [
        {"EventName": "DragonKill", "DragonType": "Earth", "KillerName": "P4"},
        {"EventName": "DragonKill", "DragonType": "Water", "KillerName": "P9"},
        {"EventName": "DragonKill", "DragonType": "Elder", "KillerName": "P4"},
        {"EventName": "HeraldKill", "KillerName": "P8"},
        {"EventName": "HordeKill", "KillerName": "P3"},
        {"EventName": "HordeKill", "KillerName": "P5"},
        {"EventName": "BaronKill", "KillerName": "P9"},
        {"EventName": "TurretKilled", "TurretName": "Turret_T2_L_03_A"},
        {"EventName": "TurretKilled", "TurretName": "Turret_T2_C_05_A"},
        {"EventName": "TurretKilled", "TurretName": "Turret_T1_C_05_A"},
        {"EventName": "InhibKilled", "InhibName": "Barracks_T2_C1"},
    ]
    return {"gameData": {"gameTime": 600.0}, "activePlayer": {"summonerName": my_name, "currentGold": 400},
            "allPlayers": players, "events": {"Events": events}}


def test_live_features_equal_training_features_for_the_same_game():
    snap_train = extract_timeline_snapshots(sample_match("KR_X", 1500, True), _timeline(), tier_bucket="high", minutes=(10,))
    assert len(snap_train) == 1
    train = snap_train[0]

    live = parse_liveclient_data(_live("P1"), tier="high").scoreboard.to_feature_dict()
    for f in EXTENDED_FEATURES:
        if f in MASKED_FEATURES or f in CONTEXT_COLUMNS:
            continue
        assert live[f] == train[f], f"{f}: live={live[f]} train={train[f]}"
    assert live["game_time_min"] == train["game_time_min"] == 10.0
    assert live["tier_bucket"] == train["tier_bucket"] == 2.0
    # sanity: the fixture really exercises the new features (not all zeros)
    for f in ("voidgrubs_diff", "barons_diff", "elder_diff", "inhibitors_diff", "recent_3m_kill_diff", "lane_cs_diff", "towers_diff", "first_blood"):
        assert train[f] != 0.0, f


def test_masked_features_are_zero_on_both_sides():
    snap = extract_timeline_snapshots(sample_match("KR_X", 1500, True), _timeline(), minutes=(10,))[0]
    snap["item_gold_diff"], snap["alive_diff"] = 999.0, 3.0  # even if someone fills them, vectorizing zeroes them
    v = vectorize_row(snap)[0]
    assert v[EXTENDED_FEATURES.index("item_gold_diff")] == 0.0 and v[EXTENDED_FEATURES.index("alive_diff")] == 0.0
    assert parse_liveclient_data(_live("P1")).scoreboard.to_feature_dict()["alive_diff"] == 0.0


def test_my_side_and_player_stats_from_live_json():
    snap = parse_liveclient_data(_live("P7"))  # a CHAOS player
    assert snap.scoreboard.my_side == "red" and snap.my_position == "JUNGLE"
    assert snap.my_cs == CS[7] and snap.my_level == LVL[7]
    # red team kills = 2 (P7, P6); P7 has 1 kill, 0 assists -> KP 1/2
    assert snap.my_kill_participation == 0.5
