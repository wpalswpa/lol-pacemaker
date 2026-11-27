"""Automatic match collection pipeline (AGENTS.md §20). Replaces scripts/fetch_eval_matches.py.

  python -m ml.pipeline collect [--store local|d1] [--max 30]   # Riot API -> anonymized store
  python -m ml.pipeline export                                   # D1 -> data/store/matches.jsonl.gz
  python -m ml.pipeline build                                    # store -> candidate model/baselines + gate

Store rows: {"bucket", "queue", "remake", "match", "timeline"}; names, Riot IDs and PUUIDs are dropped,
participants keep participantId only, match ids become a 12-char SHA-256 tag. Matches whose tag hash
% 3 == 0 are evaluation-only (never used to train or to build baselines).
Secrets come from env only: RIOT_API_KEY, PACEMAKER_ADMIN_TOKEN. Nothing secret is printed.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parent.parent
LOCAL_STORE = ROOT / "data" / "store" / "matches.jsonl.gz"
DEFAULT_API = "https://api.lol-pacemaker.workers.dev"

PART_KEEP = (
    "participantId", "teamId", "teamPosition", "individualPosition", "championName", "win",
    "kills", "deaths", "assists", "totalMinionsKilled", "neutralMinionsKilled", "goldEarned",
    "visionScore", "totalDamageDealtToChampions", "wardsPlaced", "wardsKilled",
    "detectorWardsPlaced", "gameEndedInEarlySurrender", "teamEarlySurrendered", "champLevel",
    "timePlayed", "damageDealtToObjectives", "bountyLevel", "totalDamageTaken", "damageSelfMitigated",
)
EVENT_KEEP = {
    "CHAMPION_KILL", "ELITE_MONSTER_KILL", "BUILDING_KILL", "WARD_PLACED", "WARD_KILL",
    "LEVEL_UP", "ITEM_PURCHASED", "TURRET_PLATE_DESTROYED",
}
EVENT_DROP_KEYS = ("victimDamageDealt", "victimDamageReceived", "victimTeamfightDamageDealt", "victimTeamfightDamageReceived")
PF_KEEP = ("totalGold", "currentGold", "level", "xp", "minionsKilled", "jungleMinionsKilled", "position")
QUEUES = {"ranked": "queue=420&type=ranked", "aram": "queue=450"}
MAX_PAYLOAD = 64 * 1024
STORE_SNAPSHOT_MINUTES = (5, 10, 15, 20, 25, 30)  # AGENTS §28: same minutes the HUD serves and eval_patterns scores


# ------------------------------------------------------------------ anonymize / encode
def tag(match_id: str) -> str:
    return hashlib.sha256(match_id.encode()).hexdigest()[:12]


def is_eval_tag(t: str) -> bool:
    return int(t[:8], 16) % 3 == 0


def is_remake(match: dict[str, Any]) -> bool:
    info = match.get("info") or {}
    return float(info.get("gameDuration") or 0) < 300 or any(p.get("gameEndedInEarlySurrender") for p in info.get("participants") or [])


AFK_FROM_FRAME = 3      # ignore the first minutes (everyone is walking to lane)
AFK_STALL_FRAMES = 4    # this many consecutive minutes without XP growth ...
AFK_XP_EPS = 5          # ... of at most this much XP = the player is not in the game


def season_of(row: dict[str, Any]) -> int:
    """Patch major (e.g. 16 for '16.23.731.0'); 0 when unknown."""
    try:
        return int(str(row["match"]["info"].get("gameVersion") or "0").split(".")[0])
    except ValueError:
        return 0


def afk_participants(timeline: dict[str, Any] | None) -> list[int]:
    """AGENTS §28: participantIds whose XP stalls for AFK_STALL_FRAMES minutes after minute AFK_FROM_FRAME."""
    frames = ((timeline or {}).get("info") or {}).get("frames") or []
    out = []
    for pid in range(1, 11):
        xs = [float(((f.get("participantFrames") or {}).get(str(pid)) or {}).get("xp") or 0) for f in frames]
        stall = 0
        for a, b in zip(xs[AFK_FROM_FRAME:], xs[AFK_FROM_FRAME + 1:]):
            stall = stall + 1 if b - a <= AFK_XP_EPS else 0
            if stall >= AFK_STALL_FRAMES:
                out.append(pid)
                break
    return out


def quality_issues(row: dict[str, Any], season: int) -> list[str]:
    """AGENTS §28: reasons a stored match must not train, build baselines or be evaluated on. [] = clean."""
    bad = []
    if row.get("remake"):
        bad.append("remake")
    if season and season_of(row) != season:
        bad.append("old_season")
    if afk_participants(row.get("timeline")):
        bad.append("afk")
    return bad


def clean_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Split the store into clean rows + a count per drop reason (a row can have several)."""
    season = max((season_of(r) for r in rows), default=0)
    kept, dropped = [], {"remake": 0, "old_season": 0, "afk": 0}
    for r in rows:
        issues = quality_issues(r, season)
        for i in issues:
            dropped[i] += 1
        if not issues:
            kept.append(r)
    return kept, {"season": season, "kept": len(kept), **dropped}


def slim(match: dict[str, Any], timeline: dict[str, Any] | None, bucket: str) -> dict[str, Any]:
    """Anonymized, size-bounded store row (AGENTS §20)."""
    info = match.get("info") or {}
    parts = [{k: p.get(k) for k in PART_KEEP if k in p} for p in info.get("participants") or []]
    for p in parts:
        p["puuid"] = f"p{p.get('participantId')}"  # pseudo id so the review code can select "me"
    mid = str((match.get("metadata") or {}).get("matchId") or "")
    out_match = {
        "metadata": {"matchId": mid if len(mid) == 12 and "_" not in mid else tag(mid)},
        "info": {
            "queueId": info.get("queueId"), "mapId": info.get("mapId"), "gameMode": info.get("gameMode"),
            "gameDuration": info.get("gameDuration"), "gameVersion": info.get("gameVersion"),
            "participants": parts,
            "teams": [{"teamId": t.get("teamId"), "win": t.get("win"), "objectives": t.get("objectives")} for t in info.get("teams") or []],
        },
    }
    out_tl = None
    if timeline:
        frames = []
        for fr in (timeline.get("info") or {}).get("frames") or []:
            pfs = {pid: {k: pf.get(k) for k in PF_KEEP if k in pf} for pid, pf in (fr.get("participantFrames") or {}).items()}
            evs = [{k: v for k, v in e.items() if k not in EVENT_DROP_KEYS} for e in fr.get("events") or [] if e.get("type") in EVENT_KEEP]
            frames.append({"timestamp": fr.get("timestamp"), "participantFrames": pfs, "events": evs})
        out_tl = {"info": {"frames": frames}}
    queue = int(info.get("queueId") or 0)
    return {"bucket": bucket, "queue": queue, "remake": is_remake(match), "match": out_match, "timeline": out_tl}


def encode_payload(row: dict[str, Any]) -> str:
    import zlib

    return base64.b64encode(zlib.compress(json.dumps(row, separators=(",", ":")).encode(), 9)).decode()


def decode_payload(payload: str) -> dict[str, Any]:
    import zlib

    return json.loads(zlib.decompress(base64.b64decode(payload)).decode())


def store_record(row: dict[str, Any], platform: str) -> dict[str, Any]:
    info = row["match"]["info"]
    return {
        "tag": row["match"]["metadata"]["matchId"], "platform": platform, "queue": row["queue"], "bucket": row["bucket"],
        "patch": ".".join(str(info.get("gameVersion") or "").split(".")[:2]), "duration": int(info.get("gameDuration") or 0),
        "remake": 1 if row["remake"] else 0, "payload": encode_payload(row),
    }


# ------------------------------------------------------------------ stores
class LocalStore:
    def __init__(self, path: Path = LOCAL_STORE) -> None:
        self.path = Path(path)

    def rows(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with gzip.open(self.path, "rt", encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def tags(self) -> set[str]:
        return {r["match"]["metadata"]["matchId"] for r in self.rows()}

    def add(self, rows: Iterable[dict[str, Any]], platform: str = "kr") -> int:
        known = self.tags()
        new = [r for r in rows if r["match"]["metadata"]["matchId"] not in known]
        if new:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(self.path, "at", encoding="utf-8") as fh:  # gzip members append safely
                for r in new:
                    fh.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
        return len(new)


class D1Store:
    """Talks to the Worker admin routes (AGENTS §20). Never sees Cloudflare credentials."""

    def __init__(self, base_url: str, token: str, opener: Callable[..., Any] = urllib.request.urlopen) -> None:
        self.base, self._token, self._open = base_url.rstrip("/"), token, opener

    def _req(self, method: str, path: str, body: Any = None) -> Any:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self._token}", "Content-Type": "application/json", "User-Agent": "LoLPacemaker-pipeline/1.0"})
        with self._open(req, timeout=30) as resp:
            return json.loads(resp.read().decode())

    def export(self, fields: str = "all") -> list[dict[str, Any]]:
        out, after = [], 0
        while True:
            page = self._req("GET", f"/v1/admin/matches?after={after}&limit=50&fields={fields}")
            items = page.get("items") or []
            out.extend(items)
            if len(items) < 50:
                return out
            after = items[-1]["id"]

    def tags(self) -> set[str]:
        return {r["tag"] for r in self.export(fields="tag")}

    def rows(self) -> list[dict[str, Any]]:
        return [decode_payload(r["payload"]) for r in self.export()]

    def add(self, rows: Iterable[dict[str, Any]], platform: str = "kr") -> int:
        recs = [store_record(r, platform) for r in rows]
        recs = [r for r in recs if len(r["payload"]) <= MAX_PAYLOAD]
        n = 0
        for i in range(0, len(recs), 20):
            n += int(self._req("POST", "/v1/admin/matches", {"matches": recs[i:i + 20]}).get("inserted") or 0)
        return n


# ------------------------------------------------------------------ collect
def collect(client: Any, store: Any, platform: str = "kr", per_bucket: int = 10, aram: int = 10, max_total: int = 30,
            seed: int | None = None, log: Callable[[str], None] = lambda m: print(m, file=sys.stderr)) -> dict[str, Any]:
    """Sample players per tier bucket, fetch new ranked/ARAM matches (+ any remakes seen), add them to the store."""
    from ml.collector import REGION_OF, Collector, FatalApiError

    known = store.tags()
    known_train = _bundled_match_ids()
    mbase = f"https://{REGION_OF.get(platform, 'asia')}.api.riotgames.com/lol/match/v5/matches"
    counts: dict[str, Any] = {"ranked": {"low": 0, "mid": 0, "high": 0}, "aram": 0, "remake": 0, "skipped_known": 0}
    pending: list[dict[str, Any]] = []
    total = 0
    with tempfile.TemporaryDirectory() as tmp:
        for bucket in ("low", "mid", "high"):
            coll = Collector(client, platform, bucket, Path(tmp) / "x.jsonl", Path(tmp) / f"ck_{bucket}.json",
                             seed=seed if seed is not None else int.from_bytes(os.urandom(4), "big"), log=log)
            for puuid in coll.sample_puuids(per_tier=3):
                if total >= max_total:
                    break
                for kind, q in QUEUES.items():
                    def full() -> bool:
                        return total >= max_total or (counts["ranked"][bucket] >= per_bucket if kind == "ranked" else counts["aram"] >= aram)

                    if full():
                        continue
                    try:
                        ids = client.get_json(f"{mbase}/by-puuid/{puuid}/ids?{q}&start=0&count=3") or []
                    except FatalApiError:
                        raise
                    except urllib.error.HTTPError as exc:
                        log(f"ids HTTP {exc.code}")
                        continue
                    for mid in ids:
                        if full():
                            break
                        t = tag(mid)
                        if t in known or mid in known_train:
                            counts["skipped_known"] += 1
                            continue
                        known.add(t)
                        try:
                            match = client.get_json(f"{mbase}/{mid}")
                            timeline = client.get_json(f"{mbase}/{mid}/timeline")
                        except FatalApiError:
                            raise
                        except urllib.error.HTTPError as exc:
                            log(f"match HTTP {exc.code}")
                            continue
                        row = slim(match, timeline, bucket)
                        if row["remake"]:
                            counts["remake"] += 1
                        elif row["queue"] == 420:
                            counts["ranked"][bucket] += 1
                        elif row["queue"] == 450:
                            counts["aram"] += 1
                        else:
                            continue
                        pending.append(row)
                        total += 1
                        if len(pending) >= 10:  # flush often so a stopped run keeps its work
                            store.add(pending, platform)
                            pending.clear()
    if pending:
        store.add(pending, platform)
    counts["added"] = total
    return counts


def _bundled_match_ids() -> set[str]:
    ids: set[str] = set()
    for f in (ROOT / "data").glob("*.jsonl"):
        for line in f.open(encoding="utf-8"):
            try:
                ids.add(json.loads(line).get("match_id"))
            except ValueError:
                pass
    return ids


# ------------------------------------------------------------------ build + gate
def build(rows: list[dict[str, Any]], out_dir: Path, store_path: Path) -> dict[str, Any]:
    """Train candidates on the non-eval split + bundled data, rebuild baselines, gate against the current bundle."""
    from ml.collector import extract_player_stats, extract_timeline_snapshots, load_jsonl
    from ml.train import train_and_export_onnx

    sys.path.insert(0, str(ROOT / "scripts"))
    import eval_patterns  # noqa: PLC0415

    out_dir.mkdir(parents=True, exist_ok=True)
    clean, quality = clean_rows(rows)  # AGENTS §28: remake / old season / AFK never train, build or evaluate
    train_rows = [r for r in clean if r["queue"] == 420 and r["timeline"] and not is_eval_tag(r["match"]["metadata"]["matchId"])]
    snaps, players = load_jsonl(sorted((ROOT / "data").glob("*.jsonl")))
    for r in train_rows:
        snaps += extract_timeline_snapshots(r["match"], r["timeline"], r["bucket"], STORE_SNAPSHOT_MINUTES)
        players += extract_player_stats(r["match"], r["timeline"], r["bucket"])
    metrics = train_and_export_onnx(snaps, out_dir, player_rows=players)

    wdir = out_dir / "worker"
    wdir.mkdir(exist_ok=True)
    shutil.copy(ROOT / "worker" / "review.js", wdir / "review.js")
    shutil.copy(ROOT / "worker" / "review_baseline.js", wdir / "review_baseline.js")  # seed; replaced below
    node = shutil.which("node")
    if node:
        subprocess.run([node, str(ROOT / "scripts" / "build_review_baseline.mjs"), str(store_path), "--out", str(wdir / "review_baseline.js"),
                        "--module", str(wdir / "review.js")], check=True, capture_output=True, text=True)

    eval_rows = [r for r in clean if is_eval_tag(r["match"]["metadata"]["matchId"])] + [r for r in rows if r["remake"]]
    cand = eval_patterns.model_report(out_dir, eval_rows)
    cand_raw = eval_patterns.model_report(out_dir, eval_rows, calibrated=False)
    cur = eval_patterns.model_report(ROOT / "assets", eval_rows)
    rep = {
        "quality": quality, "train_matches": {"bundled_90": True, "store": len(train_rows)}, "eval_matches": len(eval_rows),
        "candidate_without_time_calibration": cand_raw.get("ranked_heldout"),
        "train_metrics": {k: metrics[k] for k in ("selected", "n_matches", "log_loss", "brier", "auc", "time_calibration")},
        "candidate": cand, "current": cur,
        "strategy": eval_patterns.strategy_report(out_dir),
        "review": eval_patterns.review_report(wdir / "review.js", store_path) if node else {"skipped": "node not found"},
    }
    rep["gate_failures"] = eval_patterns.gates({"model": cand, "strategy": rep["strategy"], "review": rep["review"]})
    c, k = cand.get("ranked_heldout", {}).get("all"), cur.get("ranked_heldout", {}).get("all")
    if not c or not k:
        rep["gate_failures"].append("no ranked evaluation matches")
    else:
        if c["brier"] > k["brier"]:
            rep["gate_failures"].append(f"candidate Brier {c['brier']} worse than current {k['brier']}")
        if c["ece"] > k["ece"]:
            rep["gate_failures"].append(f"candidate ECE {c['ece']} worse than current {k['ece']}")
    rep["accepted"] = not rep["gate_failures"]
    (out_dir / "pipeline_report.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False), encoding="utf-8")
    return rep


# ------------------------------------------------------------------ CLI
def _store(kind: str, api: str) -> Any:
    if kind == "local":
        return LocalStore()
    token = os.environ.get("PACEMAKER_ADMIN_TOKEN")
    if not token:
        raise SystemExit("PACEMAKER_ADMIN_TOKEN is not set")
    return D1Store(api, token)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AGENTS §20 collection pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--store", choices=["local", "d1"], default="local")
    c.add_argument("--api", default=os.environ.get("PACEMAKER_API", DEFAULT_API))
    c.add_argument("--platform", default="kr")
    c.add_argument("--per-bucket", type=int, default=10)
    c.add_argument("--aram", type=int, default=10)
    c.add_argument("--max", type=int, default=30)
    e = sub.add_parser("export")
    e.add_argument("--api", default=os.environ.get("PACEMAKER_API", DEFAULT_API))
    b = sub.add_parser("build")
    b.add_argument("--out", type=Path, default=ROOT / "build" / "candidate")
    args = ap.parse_args(argv)

    if args.cmd == "collect":
        from ml.collector import FatalApiError, RiotRateLimitedClient

        key = os.environ.get("RIOT_API_KEY")
        if not key:
            print("RIOT_API_KEY is not set", file=sys.stderr)
            return 2
        try:
            counts = collect(RiotRateLimitedClient(key), _store(args.store, args.api), args.platform, args.per_bucket, args.aram, args.max)
        except FatalApiError as exc:  # expired dev key etc.: fail loudly, do not retry
            print(f"stopped: {exc}", file=sys.stderr)
            return 3
        print(json.dumps(counts))
        return 0
    if args.cmd == "export":
        rows = _store("d1", args.api).rows()
        n = LocalStore().add(rows)
        print(json.dumps({"exported": len(rows), "new_local": n}))
        return 0
    rep = build(LocalStore().rows(), args.out, LOCAL_STORE)
    print(json.dumps({"accepted": rep["accepted"], "gate_failures": rep["gate_failures"], "out": str(args.out)}, ensure_ascii=False))
    return 0 if rep["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
