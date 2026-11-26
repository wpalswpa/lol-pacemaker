"""AGENTS §14: opt-in anonymous contribution — allowlist, consent gate, labels, reset, withdrawal."""
import json

from ml.features import EXTENDED_FEATURES
from src.contribute import (
    CONSENT_VERSION,
    ContributionRecorder,
    build_payload,
    game_result,
    my_team,
    new_install_id,
    request_deletion,
)
from src.hud_state import HudSettings

FEATS = {k: 1.0 for k in EXTENDED_FEATURES}


def raw(team="ORDER", result=None, name="Me#KR1"):
    r = {
        "activePlayer": {"riotId": name},
        "allPlayers": [
            {"riotId": name, "summonerName": "Me", "team": team, "championName": "Ahri"},
            {"riotId": "Other#KR1", "summonerName": "Other", "team": "CHAOS" if team == "ORDER" else "ORDER"},
        ],
        "events": {"Events": []},
    }
    if result:
        r["events"]["Events"].append({"EventName": "GameEnd", "Result": result})
    return r


def recorder(consent=True, sent=None):
    sent = [] if sent is None else sent
    return ContributionRecorder(
        consented=lambda: consent, install_id=lambda: "11111111-1111-4111-8111-111111111111",
        lang=lambda: "ko", tier=lambda: "mid", send=sent.append,
    ), sent


def play(rec, team="ORDER", result="Win", mode="CLASSIC"):
    for t in (300, 600, 905, 1200, 1500, 1700):
        rec.observe(raw(team), t, mode, FEATS)
    return rec.observe(raw(team, result), 1800, mode, FEATS)


def test_no_consent_means_nothing_is_sent():
    rec, sent = recorder(consent=False)
    assert play(rec) is False and sent == []


def test_payload_is_allowlisted_and_has_no_names():
    rec, sent = recorder()
    assert play(rec) is True and len(sent) == 1
    body = sent[0]
    assert set(body) == {"install_id", "app_version", "lang", "tier", "blue_win", "snapshots"}
    assert [s["minute"] for s in body["snapshots"]] == [10, 15, 20, 25]
    assert all(set(s["features"]) == set(EXTENDED_FEATURES) for s in body["snapshots"])
    dumped = json.dumps(body)
    for leaked in ("Me#KR1", "Ahri", "Other", "riotId", "summonerName", "championName"):
        assert leaked not in dumped


def test_extra_feature_keys_cannot_leak_in():
    body = build_payload("x", "ko", "mid", {10: {**FEATS, "summonerName": "Me"}}, True)
    assert "summonerName" not in json.dumps(body)


def test_label_is_converted_to_blue_side():
    for team, result, blue_win in (("ORDER", "Win", 1), ("ORDER", "Lose", 0), ("CHAOS", "Win", 0), ("CHAOS", "Lose", 1)):
        rec, sent = recorder()
        play(rec, team, result)
        assert sent[0]["blue_win"] == blue_win, (team, result)


def test_aram_and_unknown_end_are_not_sent():
    rec, sent = recorder()
    assert play(rec, mode="ARAM") is False
    rec2, sent2 = recorder()
    for t in (600, 900, 1200):
        rec2.observe(raw(), t, "CLASSIC", FEATS)  # client closed before GameEnd: no label
    assert sent == [] and sent2 == []


def test_sent_once_and_new_game_resets():
    rec, sent = recorder()
    play(rec)
    rec.observe(raw("ORDER", "Win"), 1802, "CLASSIC", FEATS)  # same end screen polled again
    assert len(sent) == 1
    play(rec)  # clock restarts -> new game
    assert len(sent) == 2


def test_team_and_result_parsing():
    assert my_team(raw("CHAOS")) == "red" and my_team({"activePlayer": {}}) is None
    assert game_result(raw(result="Lose")) == "Lose" and game_result(raw()) is None


def test_withdrawal_asks_server_to_delete():
    calls = []
    assert request_deletion("abc", send=lambda url, body, method="POST": calls.append((url, method)) or 200)
    assert calls == [(calls[0][0], "DELETE")] and calls[0][0].endswith("?install_id=abc")
    assert request_deletion("abc", send=lambda *a, **k: (_ for _ in ()).throw(OSError())) is False


def test_settings_consent_roundtrip_and_sanitize():
    good = new_install_id()
    s = HudSettings().with_consent(CONSENT_VERSION, True, good).sanitized()
    assert s.share_data and s.install_id == good and s.consent_version == CONSENT_VERSION
    bad = HudSettings(share_data=True, install_id="not-a-uuid").sanitized()
    assert bad.share_data is False and bad.install_id == ""  # never "on" without a valid id
    assert HudSettings().consent_version == 0  # first launch asks


def test_requests_identify_as_the_app_not_python_urllib(monkeypatch):
    import urllib.request
    seen = {}

    class R:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake(req, timeout=0):
        seen["ua"] = req.get_header("User-agent")
        return R()

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    from src.contribute import post_json, USER_AGENT
    assert post_json("https://example.invalid", {"a": 1}) == 200
    assert seen["ua"] == USER_AGENT and USER_AGENT.startswith("LoLPacemaker/")
