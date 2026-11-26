"""Synthetic MATCH-V5 fixtures shared by tests (never shipped, never used as a bundled model)."""
from __future__ import annotations

from typing import Any

ROLES = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]


def sample_match(match_id: str, duration: int, blue_win: bool, version: str = "16.5.1") -> dict[str, Any]:
    parts = [
        {"participantId": i, "teamId": 100 if i <= 5 else 200, "teamPosition": ROLES[(i - 1) % 5]}
        for i in range(1, 11)
    ]
    return {
        "metadata": {"matchId": match_id},
        "info": {
            "queueId": 420,
            "gameDuration": duration,
            "gameVersion": version,
            "participants": parts,
            "teams": [{"teamId": 100, "win": blue_win}, {"teamId": 200, "win": not blue_win}],
        },
    }


def sample_timeline(blue_ahead: bool, jitter: int = 0) -> dict[str, Any]:
    frames = []
    for m in (0, 10, 15, 20, 25):
        pframes = {}
        for pid in range(1, 11):
            ahead = (pid <= 5) == blue_ahead
            cs = (m * 7 + (15 if ahead else 0) + (pid * jitter) % 7) if m > 0 else 0
            lvl = max(1, m // 2 + (1 if ahead else 0))
            pframes[str(pid)] = {"minionsKilled": cs, "jungleMinionsKilled": 0, "level": lvl}
        evs = []
        if m == 10:
            kid = 1 if blue_ahead else 6
            evs.append({"timestamp": 300_000, "type": "CHAMPION_KILL", "killerId": kid, "assistingParticipantIds": [kid + 1]})
            evs.append({"timestamp": 540_000, "type": "ELITE_MONSTER_KILL", "killerTeamId": 100 if blue_ahead else 200, "monsterType": "HORDE"})
        frames.append({"timestamp": m * 60_000, "participantFrames": pframes, "events": evs})
    return {"info": {"frames": frames}}


def many_rows(n_matches: int = 40, tier: str = "mid") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(snapshot rows, player rows) for n synthetic games with alternating winners."""
    from ml.collector import extract_player_stats, extract_timeline_snapshots

    rows: list[dict[str, Any]] = []
    players: list[dict[str, Any]] = []
    for i in range(n_matches):
        blue_ahead = i % 2 == 0
        blue_win = blue_ahead if i % 4 else (not blue_ahead)  # 25% upsets so probabilities are not saturated
        m = sample_match(f"KR_{i}", 1500, blue_win)
        tl = sample_timeline(blue_ahead, jitter=i)
        rows.extend(extract_timeline_snapshots(m, tl, tier_bucket=tier, minutes=(10, 15, 20, 25)))
        players.extend(extract_player_stats(m, tl, tier_bucket=tier, minutes=(10, 15, 20, 25)))
    return rows, players
