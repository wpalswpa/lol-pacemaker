"""Riot MATCH-V5 Timeline collector & data quality cleaner (tier-stratified).

Extracts clean multi-timestamp snapshots (e.g. 10, 15, 20, 25 min) from
Riot MATCH-V5 match + timeline JSONs while filtering remakes, early surrenders,
and malformed timelines.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

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


class FatalApiError(RuntimeError):
    """Invalid/expired key or forbidden: retrying cannot help."""


class RiotRateLimitedClient:
    """Riot API HTTP client: min call interval, 429 Retry-After, 5xx exponential backoff.

    The key is only ever sent in the `X-Riot-Token` header; it is never put in a URL,
    log line or exception message.
    """

    def __init__(
        self,
        api_key: str,
        max_retries: int = 5,
        min_interval: float = 1.25,  # dev key: 100 req / 2 min
        sleep: Callable[[float], None] = time.sleep,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self._api_key = api_key
        self.max_retries = max_retries
        self.min_interval = min_interval
        self._sleep = sleep
        self._opener = opener
        self._last_call = 0.0

    def get_json(self, url: str) -> Any:
        req = urllib.request.Request(
            url,
            headers={
                "X-Riot-Token": self._api_key,
                "User-Agent": "lol-pacemaker-collector/1.0",
                "Accept": "application/json",
            },
        )
        for attempt in range(self.max_retries + 1):
            wait_gap = self.min_interval - (time.monotonic() - self._last_call)
            if wait_gap > 0:
                self._sleep(wait_gap)
            self._last_call = time.monotonic()
            try:
                with self._opener(req, timeout=10.0) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    raise FatalApiError(f"Riot API rejected the key (HTTP {exc.code})") from None
                if exc.code == 429 and attempt < self.max_retries:
                    self._sleep(min(float(exc.headers.get("Retry-After", "2")), 130.0))
                    continue
                if exc.code >= 500 and attempt < self.max_retries:
                    self._sleep(min(2.0 ** attempt, 30.0))
                    continue
                raise
            except (urllib.error.URLError, TimeoutError):
                if attempt < self.max_retries:
                    self._sleep(min(2.0 ** attempt, 30.0))
                    continue
                raise
        raise RuntimeError("unreachable")


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
    if any(p.get("gameEndedInEarlySurrender") or p.get("teamEarlySurrendered") for p in participants):
        return False
    return True


def patch_of(match_json: dict[str, Any]) -> str:
    """'16.5.123.4567' -> '16.5'."""
    return ".".join(str((match_json.get("info") or {}).get("gameVersion") or "").split(".")[:2])


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
                "match_id": str((match_json.get("metadata") or {}).get("matchId") or ""),
                "patch": patch_of(match_json),
            }
        )

    return rows


def extract_player_stats(
    match_json: dict[str, Any],
    timeline_json: dict[str, Any],
    tier_bucket: str = "mid",
    minutes: tuple[int, ...] = SNAPSHOT_MINUTES,
) -> list[dict[str, Any]]:
    """Per-player rows (CS/min, level, kill participation) for the tier distribution baseline."""
    if not is_valid_ranked_match(match_json):
        return []
    info = match_json["info"]
    frames = (timeline_json.get("info") or {}).get("frames") or []
    if not frames:
        return []
    p_team = {int(p["participantId"]): int(p["teamId"]) for p in info["participants"]}
    p_role = {int(p["participantId"]): str(p.get("teamPosition") or "") for p in info["participants"]}
    events = [e for fr in frames for e in (fr.get("events") or [])]
    match_id = str((match_json.get("metadata") or {}).get("matchId") or "")

    rows: list[dict[str, Any]] = []
    for minute in minutes:
        target_ms = minute * 60_000
        frame = next((fr for fr in frames if int(fr.get("timestamp", 0)) >= target_ms - 5_000), None)
        if frame is None or int(frame.get("timestamp", 0)) > target_ms + 30_000:
            continue
        kills: dict[int, int] = {}
        assists: dict[int, int] = {}
        team_kills = {100: 0, 200: 0}
        for ev in events:
            if ev.get("type") != "CHAMPION_KILL" or int(ev.get("timestamp") or 0) > target_ms:
                continue
            kid = int(ev.get("killerId") or 0)
            if kid in p_team:
                kills[kid] = kills.get(kid, 0) + 1
                team_kills[p_team[kid]] += 1
            for aid in ev.get("assistingParticipantIds") or []:
                assists[int(aid)] = assists.get(int(aid), 0) + 1
        for pid_str, pf in (frame.get("participantFrames") or {}).items():
            pid = int(pid_str)
            if pid not in p_team:
                continue
            tk = team_kills[p_team[pid]]
            cs = int(pf.get("minionsKilled") or 0) + int(pf.get("jungleMinionsKilled") or 0)
            rows.append(
                {
                    "match_id": match_id,
                    "tier": tier_bucket,
                    "minute": minute,
                    "role": p_role.get(pid) or "ALL",
                    "cs_per_min": cs / minute,
                    "level": float(pf.get("level") or 1),
                    "kill_participation": ((kills.get(pid, 0) + assists.get(pid, 0)) / tk) if tk > 0 else None,
                }
            )
    return rows


# ---------------------------------------------------------------------------------------------
# CLI: LEAGUE-V4 sampling -> MATCH-V5 -> JSONL with checkpoint/resume
# ---------------------------------------------------------------------------------------------
BUCKET_TIERS: dict[str, list[str]] = {
    "low": ["IRON", "BRONZE", "SILVER", "GOLD"],
    "mid": ["PLATINUM", "EMERALD"],
    "high": ["DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"],
}
DIVISIONS = ["I", "II", "III", "IV"]
APEX_PATH = {"MASTER": "masterleagues", "GRANDMASTER": "grandmasterleagues", "CHALLENGER": "challengerleagues"}
REGION_OF = {
    "kr": "asia", "jp1": "asia",
    "na1": "americas", "br1": "americas", "la1": "americas", "la2": "americas",
    "euw1": "europe", "eun1": "europe", "tr1": "europe", "ru": "europe",
    "oc1": "sea", "ph2": "sea", "sg2": "sea", "th2": "sea", "tw2": "sea", "vn2": "sea",
}
QUEUE = "RANKED_SOLO_5x5"


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class Collector:
    """Tier-bucket sampler. Safe to stop at any time and re-run with the same checkpoint."""

    def __init__(
        self,
        client: Any,
        platform: str,
        bucket: str,
        out_path: Path,
        checkpoint_path: Path,
        patch: str | None = None,
        seed: int = 42,
        log: Callable[[str], None] = lambda m: print(m, file=sys.stderr),
    ) -> None:
        if platform not in REGION_OF:
            raise ValueError(f"unknown platform {platform!r}; choose from {sorted(REGION_OF)}")
        if bucket not in BUCKET_TIERS:
            raise ValueError(f"unknown bucket {bucket!r}; choose from {sorted(BUCKET_TIERS)}")
        self.client, self.platform, self.bucket = client, platform, bucket
        self.region = REGION_OF[platform]
        self.out_path, self.checkpoint_path = Path(out_path), Path(checkpoint_path)
        self.patch, self.rng, self.log = patch, random.Random(seed), log
        self.state: dict[str, Any] = {"done_puuids": [], "done_matches": [], "skipped": 0, "written_matches": 0}
        if self.checkpoint_path.exists():
            self.state.update(json.loads(self.checkpoint_path.read_text(encoding="utf-8")))

    def _save(self) -> None:
        _atomic_write(self.checkpoint_path, json.dumps(self.state))

    def sample_puuids(self, per_tier: int, pages: int = 3) -> list[str]:
        base = f"https://{self.platform}.api.riotgames.com/lol/league/v4"
        found: list[str] = []
        for tier in BUCKET_TIERS[self.bucket]:
            try:
                if tier in APEX_PATH:
                    entries = (self.client.get_json(f"{base}/{APEX_PATH[tier]}/by-queue/{QUEUE}") or {}).get("entries") or []
                else:
                    div = self.rng.choice(DIVISIONS)
                    entries = self.client.get_json(f"{base}/entries/{QUEUE}/{tier}/{div}?page={self.rng.randint(1, pages)}") or []
            except urllib.error.HTTPError as exc:
                self.log(f"league {tier}: HTTP {exc.code}, skipped")
                continue
            ids = [e["puuid"] for e in entries if isinstance(e, dict) and e.get("puuid")]
            self.rng.shuffle(ids)
            found.extend(ids[:per_tier])
        return [p for p in dict.fromkeys(found) if p not in set(self.state["done_puuids"])]

    def collect(self, max_matches: int, per_tier: int = 10, matches_per_player: int = 5) -> int:
        """Write up to `max_matches` new matches. Returns how many were written this run."""
        mbase = f"https://{self.region}.api.riotgames.com/lol/match/v5/matches"
        written = 0
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        for puuid in self.sample_puuids(per_tier):
            if written >= max_matches:
                break
            try:
                ids = self.client.get_json(f"{mbase}/by-puuid/{puuid}/ids?queue=420&type=ranked&start=0&count={matches_per_player}")
            except urllib.error.HTTPError as exc:
                self.log(f"match ids: HTTP {exc.code}, skipped")
                continue
            for mid in ids or []:
                if written >= max_matches:
                    break
                if mid in self.state["done_matches"]:
                    continue
                self.state["done_matches"].append(mid)
                try:
                    match = self.client.get_json(f"{mbase}/{mid}")
                    if self.patch and patch_of(match) != self.patch:
                        self.state["skipped"] += 1
                        continue
                    if not is_valid_ranked_match(match):
                        self.state["skipped"] += 1
                        continue
                    timeline = self.client.get_json(f"{mbase}/{mid}/timeline")
                except urllib.error.HTTPError as exc:
                    self.log(f"{mid}: HTTP {exc.code}, skipped")
                    self.state["skipped"] += 1
                    continue
                snaps = extract_timeline_snapshots(match, timeline, self.bucket)
                players = extract_player_stats(match, timeline, self.bucket)
                if not snaps:
                    self.state["skipped"] += 1
                    continue
                with self.out_path.open("a", encoding="utf-8") as fh:
                    for r in snaps:
                        fh.write(json.dumps({"kind": "snapshot", **r}) + "\n")
                    for r in players:
                        fh.write(json.dumps({"kind": "player", **r}) + "\n")
                self.state["written_matches"] += 1
                written += 1
                self._save()
            self.state["done_puuids"].append(puuid)
            self._save()
        self._save()
        return written


def load_jsonl(paths: list[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read collector output -> (snapshot rows, player rows)."""
    snaps: list[dict[str, Any]] = []
    players: list[dict[str, Any]] = []
    for path in paths:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            (snaps if row.pop("kind", "snapshot") == "snapshot" else players).append(row)
    return snaps, players


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Collect tier-stratified MATCH-V5 snapshots (offline dev use; needs RIOT_API_KEY)")
    ap.add_argument("--platform", default="kr", choices=sorted(REGION_OF))
    ap.add_argument("--bucket", required=True, choices=sorted(BUCKET_TIERS))
    ap.add_argument("--max-matches", type=int, default=200)
    ap.add_argument("--per-tier", type=int, default=10, help="players sampled per tier")
    ap.add_argument("--matches-per-player", type=int, default=5)
    ap.add_argument("--patch", default=None, help="keep only this patch, e.g. 16.5")
    ap.add_argument("--out", type=Path, default=Path("data/snapshots.jsonl"))
    ap.add_argument("--checkpoint", type=Path, default=Path("data/checkpoint.json"))
    ap.add_argument("--min-interval", type=float, default=1.25)
    args = ap.parse_args(argv)
    key = os.environ.get("RIOT_API_KEY", "").strip()
    if not key:
        print("RIOT_API_KEY is not set.", file=sys.stderr)
        return 2
    args.checkpoint.parent.mkdir(parents=True, exist_ok=True)
    col = Collector(RiotRateLimitedClient(key, min_interval=args.min_interval), args.platform, args.bucket,
                    args.out, args.checkpoint, patch=args.patch)
    try:
        n = col.collect(args.max_matches, args.per_tier, args.matches_per_player)
    except FatalApiError as exc:
        col._save()
        print(str(exc), file=sys.stderr)
        return 3
    print(f"wrote {n} matches this run (total {col.state['written_matches']}, skipped {col.state['skipped']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
