"""Riot MATCH-V5 Timeline collector & data quality cleaner (tier-stratified).

Extracts clean multi-timestamp snapshots (e.g. 10, 15, 20, 25 min) from
Riot MATCH-V5 match + timeline JSONs while filtering remakes, early surrenders,
and malformed timelines.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from ml.features import TIER_BUCKETS

MIN_VALID_DURATION_SEC = 600  # Exclude remakes (<3m) and early AFK surrenders (<10m)
SNAPSHOT_MINUTES = (10, 15, 20, 25)

TIER_MAP: dict[str, str] = {
    "IRON": "low",
    "BRONZE": "low",
    "SILVER": "low",
    "GOLD": "low",
    "PLATINUM": "mid",
    "EMERALD": "mid",
    "DIAMOND": "high",
    "MASTER": "high",
    "GRANDMASTER": "high",
    "CHALLENGER": "high",
}


class RiotRateLimitedClient:
    """Lightweight Riot API HTTP client with 429 Retry-After backoff."""

    def __init__(self, api_key: str, max_retries: int = 3) -> None:
        self.api_key = api_key
        self.max_retries = max_retries

    def get_json(self, url: str) -> Any:
        req = urllib.request.Request(url, headers={"X-Riot-Token": self.api_key})
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=10.0) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < self.max_retries:
                    wait = float(exc.headers.get("Retry-After", "2"))
                    time.sleep(min(wait, 15.0))
                    continue
                raise


def tier_to_bucket(tier: str) -> str:
    return TIER_MAP.get(tier.upper(), "mid")


def is_valid_ranked_match(match_json: dict[str, Any]) -> bool:
    """Filter out non-ranked, remakes, and <10min early surrenders."""
    info = match_json.get("info") or {}
    if int(info.get("queueId") or 0) != 420:
        return False
    duration = float(info.get("gameDuration") or 0.0)
    if duration < MIN_VALID_DURATION_SEC:
        return False
    participants = info.get("participants") or []
    if len(participants) != 10:
        return False
    return True


def extract_timeline_snapshots(
    match_json: dict[str, Any],
    timeline_json: dict[str, Any],
    tier_bucket: str = "mid",
    minutes: tuple[int, ...] = SNAPSHOT_MINUTES,
) -> list[dict[str, float | int]]:
    """Extract clean 18-feature snapshots + label (`blue_win`) at target minutes."""
    if not is_valid_ranked_match(match_json):
        return []

    info = match_json["info"]
    teams = {int(t["teamId"]): bool(t.get("win")) for t in (info.get("teams") or [])}
    if 100 not in teams:
        return []
    blue_win = 1 if teams[100] else 0

    frames = (timeline_json.get("info") or {}).get("frames") or []
    if not frames:
        return []

    # Map participantId (1..5 = blue/100, 6..10 = red/200)
    p_team: dict[int, int] = {}
    p_lane: dict[int, str] = {}
    for p in info.get("participants") or []:
        pid = int(p.get("participantId") or 0)
        p_team[pid] = int(p.get("teamId") or 100)
        p_lane[pid] = str(p.get("teamPosition") or "")

    # Pre-scan events with timestamps
    events: list[dict[str, Any]] = []
    for fr in frames:
        events.extend(fr.get("events") or [])

    bucket_id = TIER_BUCKETS.get(tier_bucket, 1)
    rows: list[dict[str, float | int]] = []

    for minute in minutes:
        target_ms = minute * 60_000
        # Find nearest frame <= target_ms + 5s tolerance
        frame = next((fr for fr in frames if int(fr.get("timestamp", 0)) >= target_ms - 5_000), None)
        if frame is None or int(frame.get("timestamp", 0)) > target_ms + 30_000:
            continue

        pframes = frame.get("participantFrames") or {}
        b_cs = r_cs = b_lvl_sum = r_lvl_sum = b_cnt = r_cnt = 0
        b_lane_cs = r_lane_cs = 0
        for pid_str, pf in pframes.items():
            pid = int(pid_str)
            cs = int(pf.get("minionsKilled") or 0) + int(pf.get("jungleMinionsKilled") or 0)
            lvl = int(pf.get("level") or 1)
            lane = p_lane.get(pid, "")
            if p_team.get(pid) == 100:
                b_cs += cs
                b_lvl_sum += lvl
                b_cnt += 1
                if lane in ("TOP", "MIDDLE", "BOTTOM"):
                    b_lane_cs += cs
            else:
                r_cs += cs
                r_lvl_sum += lvl
                r_cnt += 1
                if lane in ("TOP", "MIDDLE", "BOTTOM"):
                    r_lane_cs += cs

        first_blood = 0
        b_kills = r_kills = b_assists = r_assists = 0
        b_recent_k = r_recent_k = 0
        b_drag = r_drag = b_herald = r_herald = b_grub = r_grub = 0
        b_baron = r_baron = b_elder = r_elder = b_tower = r_tower = b_inhib = r_inhib = 0

        for ev in events:
            ts = int(ev.get("timestamp") or 0)
            if ts > target_ms:
                continue
            etype = ev.get("type")
            if etype == "CHAMPION_KILL":
                kid = int(ev.get("killerId") or 0)
                if first_blood == 0 and kid in p_team:
                    first_blood = 1 if p_team[kid] == 100 else -1
                if p_team.get(kid) == 100:
                    b_kills += 1
                    if ts >= target_ms - 180_000:
                        b_recent_k += 1
                elif p_team.get(kid) == 200:
                    r_kills += 1
                    if ts >= target_ms - 180_000:
                        r_recent_k += 1
                for aid in ev.get("assistingParticipantIds") or []:
                    if p_team.get(int(aid)) == 100:
                        b_assists += 1
                    elif p_team.get(int(aid)) == 200:
                        r_assists += 1
            elif etype == "ELITE_MONSTER_KILL":
                killer_team = int(ev.get("killerTeamId") or p_team.get(int(ev.get("killerId") or 0), 0))
                mtype = ev.get("monsterType")
                msub = ev.get("monsterSubType")
                is_b = killer_team == 100
                if mtype == "DRAGON":
                    if msub == "ELDER_DRAGON":
                        if is_b:
                            b_elder += 1
                        else:
                            r_elder += 1
                    else:
                        if is_b:
                            b_drag += 1
                        else:
                            r_drag += 1
                elif mtype == "RIFTHERALD":
                    if is_b:
                        b_herald += 1
                    else:
                        r_herald += 1
                elif mtype == "HORDE":
                    if is_b:
                        b_grub += 1
                    else:
                        r_grub += 1
                elif mtype == "BARON_NASHOR":
                    if is_b:
                        b_baron += 1
                    else:
                        r_baron += 1
            elif etype == "BUILDING_KILL":
                lost_team = int(ev.get("teamId") or 0)
                btype = ev.get("buildingType")
                # teamId in BUILDING_KILL is the team that LOST the structure
                if btype == "TOWER_BUILDING":
                    if lost_team == 200:
                        b_tower += 1
                    elif lost_team == 100:
                        r_tower += 1
                elif btype == "INHIBITOR_BUILDING":
                    if lost_team == 200:
                        b_inhib += 1
                    elif lost_team == 100:
                        r_inhib += 1

        rows.append(
            {
                "game_time_min": float(minute),
                "first_blood": float(first_blood),
                "kills_diff": float(b_kills - r_kills),
                "assists_diff": float(b_assists - r_assists),
                "cs_diff": float(b_cs - r_cs),
                "avg_level_diff": float((b_lvl_sum / max(1, b_cnt)) - (r_lvl_sum / max(1, r_cnt))),
                "dragons_diff": float(b_drag - r_drag),
                "heralds_diff": float(b_herald - r_herald),
                "towers_diff": float(b_tower - r_tower),
                "voidgrubs_diff": float(b_grub - r_grub),
                "barons_diff": float(b_baron - r_baron),
                "elder_diff": float(b_elder - r_elder),
                "inhibitors_diff": float(b_inhib - r_inhib),
                "item_gold_diff": 0.0,
                "recent_3m_kill_diff": float(b_recent_k - r_recent_k),
                "lane_cs_diff": float(b_lane_cs - r_lane_cs),
                "alive_diff": 0.0,
                "tier_bucket": float(bucket_id),
                "blue_win": blue_win,
            }
        )

    return rows
