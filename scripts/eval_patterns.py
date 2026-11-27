"""AGENTS §19: reproducible offline benchmark for the live briefing and the web review.

1. Win model calibration on held-out real matches (data/eval, never used in training):
   Brier, LogLoss, ECE (10 bins) and direction accuracy per minute (5..30), plus symmetry probes.
2. Shotcaller edge cases: hand-written 2999 `allgamedata` payloads (synthetic, labelled as such).
3. Web review: runs scripts/eval_review.mjs (node) on the same held-out matches.

  python scripts/eval_patterns.py [--model-dir assets] [--review-module worker/review.js] [--gate]
Writes ml/eval_report.json. With --gate, exits 1 when an absolute pass criterion fails.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from ml.collector import extract_timeline_snapshots  # noqa: E402
from ml.features import CONTEXT_COLUMNS, EXTENDED_FEATURES, vectorize_rows  # noqa: E402

MINUTES = (5, 10, 15, 20, 25, 30)
DEFAULT_DATA = ROOT / "data" / "store" / "matches.jsonl.gz"


# ------------------------------------------------------------------ 1. model calibration
def load_eval(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def snapshots(rows: list[dict[str, Any]], queue: int) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if r["queue"] != queue or r["remake"] or not r["timeline"]:
            continue
        match = r["match"]
        if queue != 420:  # reuse the ranked extractor for other queues (same timeline schema)
            match = copy.deepcopy(match)
            match["info"]["queueId"] = 420
        out.extend(extract_timeline_snapshots(match, r["timeline"], r["bucket"], MINUTES))
    return out


def calib(p: np.ndarray, y: np.ndarray, bins: int = 10) -> dict[str, float]:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    ece = sum(abs(p[idx == b].mean() - y[idx == b].mean()) * (idx == b).sum() for b in range(bins) if (idx == b).any()) / len(p)
    return {
        "n": int(len(p)),
        "brier": round(float(np.mean((p - y) ** 2)), 4),
        "log_loss": round(float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))), 4),
        "ece": round(float(ece), 4),
        "direction_acc": round(float(np.mean((p >= 0.5) == (y == 1))), 4),
    }


def model_report(model_dir: Path, rows: list[dict[str, Any]], calibrated: bool = True) -> dict[str, Any]:
    from src.predictor import LocalOnnxPredictor, ScoreboardState

    pred = LocalOnnxPredictor(assets_dir=model_dir, model_dirs=[])
    if not calibrated:  # AGENTS §28 ablation: the same ONNX without the per-minute scale
        pred.calibration = None
    rep: dict[str, Any] = {"model_dir": str(model_dir.relative_to(ROOT)) if model_dir.is_relative_to(ROOT) else model_dir.name, "mode": pred.mode}
    for name, queue in (("ranked_heldout", 420), ("aram_heldout", 450)):
        snaps = snapshots(rows, queue)
        if not snaps:
            continue
        X = vectorize_rows(snaps)
        y = np.array([s["blue_win"] for s in snaps], dtype=float)
        p = pred._blue_probs(X)
        rep[name] = {"all": calib(p, y), "matches": len({s["match_id"] for s in snaps}),
                     "by_minute": {str(m): calib(p[X[:, 0] == m], y[X[:, 0] == m]) for m in MINUTES if (X[:, 0] == m).any()}}
        if queue == 420:
            ctx = np.array([f in CONTEXT_COLUMNS for f in EXTENDED_FEATURES])
            Xf = X.copy()
            Xf[:, ~ctx] = -Xf[:, ~ctx]
            rep["mirror_gap_mean"] = round(float(np.mean(np.abs(p + pred._blue_probs(Xf) - 1.0))), 4)
            fb = np.array([s["first_blood"] for s in snaps])  # raw value: the model input may be masked
            rep["heldout_first_blood_team_win_rate"] = round(float(np.mean(np.where(fb > 0, y, 1 - y)[fb != 0])), 4)
            rep["heldout_blue_win_rate"] = round(float(np.mean([r["match"]["info"]["teams"][0]["win"] for r in rows if r["queue"] == 420 and not r["remake"]])), 4)
    rep["even_state_blue_prob"] = {str(t): round(pred.predict_win_prob(ScoreboardState(game_time_min=t)), 3) for t in MINUTES}
    even = pred.predict_win_prob(ScoreboardState(game_time_min=10))
    # one kill as the event is recorded (killer + one assist), the realistic unit of a skirmish
    k1 = pred.predict_win_prob(ScoreboardState(game_time_min=10, blue_first_blood=1, blue_kills=1, blue_assists=1))
    rep["blue_kill_effect_pp"] = round((k1 - even) * 100, 1)
    from ml.train import monotone_violations  # same probe the trainer uses to reject candidates

    class _Served:  # the served ONNX through the trainer's probe interface
        @staticmethod
        def predict_proba(X: np.ndarray) -> np.ndarray:
            p = pred._blue_probs(X)
            return np.stack([1 - p, p], axis=1)

    rep["monotone_violations"] = monotone_violations(_Served()) if pred.mode == "extended" else []
    return rep


# ------------------------------------------------------------------ 2. shotcaller edge cases (synthetic 2999)
def _player(name: str, team: str, pos: str, dead: bool = False, respawn: float = 0.0, kills: int = 0, smite: bool = False) -> dict[str, Any]:
    spell = {"rawDisplayName": "SummonerSmite" if smite else "SummonerFlash", "displayName": "Smite" if smite else "Flash"}
    return {"riotIdGameName": name, "summonerName": name, "team": team, "position": pos, "level": 10, "isDead": dead,
            "respawnTimer": respawn, "items": [], "summonerSpells": {"summonerSpellOne": spell, "summonerSpellTwo": spell},
            "scores": {"kills": kills, "assists": 0, "creepScore": 100}}


def _game(t: float, mode: str = "CLASSIC", gold: float = 500.0, ally_dead: int = 0, enemy_dead: int = 0,
          me_dead: bool = False, enemy_jg_dead: float = 0.0, events: list[dict] | None = None, players: bool = True) -> dict[str, Any]:
    pos = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]
    ps = []
    if players:
        for i, p in enumerate(pos):
            ps.append(_player("me" if i == 2 else f"a{i}", "ORDER", p, dead=(i == 2 and me_dead) or i < ally_dead, respawn=20, smite=p == "JUNGLE"))
        for i, p in enumerate(pos):
            jg_dead = p == "JUNGLE" and enemy_jg_dead > 0
            ps.append(_player(f"e{i}", "CHAOS", p, dead=i < enemy_dead or jg_dead, respawn=enemy_jg_dead if jg_dead else 25, smite=p == "JUNGLE"))
    return {"gameData": {"gameTime": t, "gameMode": mode, "mapNumber": 12 if mode == "ARAM" else 11},
            "activePlayer": {"riotIdGameName": "me", "summonerName": "me", "currentGold": gold},
            "allPlayers": ps, "events": {"Events": events or []}}


def strategy_cases() -> list[tuple[str, dict[str, Any], Any]]:
    """(name, payload, check(result) -> bool). Rules fixed before measuring."""
    kinds = lambda r: {c["kind"] for c in r["calls"]}  # noqa: E731
    titles = lambda r, key: any(c["title"] == key for c in r["calls"])  # noqa: E731  (KeyTranslator: title == key)
    return [
        ("S1_ally_aced_defense_call", _game(1300, ally_dead=5), lambda r: "offense" not in kinds(r) and titles(r, "call_num_disadv_title")),
        ("S2_baron_dead_no_baron_call", _game(1380, enemy_dead=1, events=[{"EventName": "BaronKill", "EventTime": 1260, "KillerName": "a1"}]),
         lambda r: not titles(r, "call_baron_window_title")),
        ("S3_dragon_dead_no_smite_call", _game(720, enemy_jg_dead=20, events=[{"EventName": "DragonKill", "EventTime": 600, "KillerName": "a1", "DragonType": "Fire"}]),
         lambda r: not titles(r, "call_smite_adv_title")),
        ("S4_grubs_gone_no_grub_call", _game(540, events=[{"EventName": "HordeKill", "EventTime": 500 + i, "KillerName": "e1"} for i in range(3)]),
         lambda r: not titles(r, "call_grubs_window_title")),
        ("S5_dead_no_recall_call", _game(900, gold=2000, me_dead=True), lambda r: "recall" not in kinds(r)),
        ("S6_aram_no_rift_distribution", _game(600, mode="ARAM"), lambda r: r["rank_mode"] != "distribution"),
        ("S7_control_recall_alive", _game(900, gold=1500), lambda r: "recall" in kinds(r)),
        ("S8_control_baron_up", _game(1500, enemy_dead=1), lambda r: titles(r, "call_baron_window_title")),
        ("S9_control_grubs_open", _game(420), lambda r: titles(r, "call_grubs_window_title")),
        ("S10_control_smite_dragon_up", _game(1080, enemy_jg_dead=20), lambda r: titles(r, "call_smite_adv_title")),
        ("S11_loading_no_players", _game(5, players=False), lambda r: isinstance(r.get("win_pct"), float)),
        ("S12_no_epic_objective_no_towers_twice", _game(1350, enemy_dead=2, events=[{"EventName": "BaronKill", "EventTime": 1290, "KillerName": "a1"},
                                                                                {"EventName": "DragonKill", "EventTime": 1100, "KillerName": "a1", "DragonType": "Fire"}]),
         lambda r: any(c["detail"] == "call_num_adv_detail_towers" for c in r["calls"])),
        ("S13_aram_no_towers_twice", _game(600, mode="ARAM", enemy_dead=2), lambda r: any(c["detail"] == "call_num_adv_detail_towers" for c in r["calls"])),
    ]


def strategy_report(model_dir: Path) -> dict[str, Any]:
    from src.engine import parse_liveclient_data
    from src.i18n import Translator
    from src.predictor import LocalOnnxPredictor
    from src.strategy_core import evaluate_snapshot

    pred = LocalOnnxPredictor(assets_dir=model_dir, model_dirs=[])
    class KeyTranslator(Translator):
        def t(self, key: str, **_: Any) -> str:  # render message keys, so checks do not depend on wording
            return key

    tr = KeyTranslator(lang="en")
    res, times = {}, []
    for name, payload, check in strategy_cases():
        try:
            t0 = time.perf_counter()
            out = evaluate_snapshot(parse_liveclient_data(payload, "mid"), pred, tr)
            times.append((time.perf_counter() - t0) * 1000)
            res[name] = bool(check(out))
        except Exception as exc:  # a crash is a failed case
            res[name] = False
            res[name + "_error"] = type(exc).__name__
    fails = [k for k, v in res.items() if v is False]
    return {"synthetic": True, "cases": len(strategy_cases()), "failed": fails, "errors": len(fails),
            "evaluate_ms_p95": round(float(np.percentile(times, 95)), 3) if times else None}


# ------------------------------------------------------------------ 3. web review (node)
def review_report(module: Path, data: Path) -> dict[str, Any]:
    node = shutil.which("node")
    if not node:
        return {"skipped": "node not found"}
    out = subprocess.run([node, str(ROOT / "scripts" / "eval_review.mjs"), "--module", str(module), "--data", str(data)],
                         capture_output=True, text=True, timeout=600)
    if out.returncode != 0:
        return {"failed_to_run": out.stderr.strip().splitlines()[-3:]}
    return json.loads(out.stdout)


def gates(rep: dict[str, Any]) -> list[str]:
    bad = []
    if rep["strategy"]["errors"]:
        bad.append(f"strategy edge errors {rep['strategy']['failed']}")
    rv = rep["review"]
    if "errors" not in rv:
        bad.append(f"review did not run: {rv}")
        return bad
    if sum(rv["errors"].values()):
        bad.append(f"review edge errors {rv['errors']}")
    if rv["contradictions"]["metric_side"] or rv["contradictions"]["tier_claim"]:
        bad.append(f"review contradictions {rv['contradictions']}")
    if rv["latency_ms"].get("analyze_p95", rv["latency_ms"]["p95"]) >= 5.0:
        bad.append(f"review latency {rv['latency_ms']}")
    m = rep["model"]
    if abs(m["even_state_blue_prob"]["10"] - 0.5) > 0.02 or m["blue_kill_effect_pp"] <= 0:
        bad.append("model symmetry / kill-direction probe")
    if m.get("monotone_violations"):
        bad.append(f"model advantage features lower win rate: {m['monotone_violations']}")
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA)
    ap.add_argument("--model-dir", type=Path, default=ROOT / "assets")
    ap.add_argument("--review-module", type=Path, default=ROOT / "worker" / "review.js")
    ap.add_argument("--out", type=Path, default=ROOT / "ml" / "eval_report.json")
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--model-rows", choices=["eval", "all"], default="eval",
                    help="'all' only while the model under test was trained on none of the store (AGENTS §20 split)")
    args = ap.parse_args(argv)
    if not args.data.exists():
        print(f"missing {args.data}: run `python -m ml.pipeline collect` first (needs RIOT_API_KEY)", file=sys.stderr)
        return 2
    from ml.pipeline import clean_rows

    raw_rows = load_eval(args.data)
    rows, quality = clean_rows(raw_rows)  # AGENTS §28: old season / AFK games are not evaluation material
    rows += [r for r in raw_rows if r["remake"]]  # remakes stay as review edge cases (models skip them)
    rep = {
        "data": {"matches": len(rows), "ranked": sum(r["queue"] == 420 and not r["remake"] for r in rows),
                 "aram": sum(r["queue"] == 450 and not r["remake"] for r in rows), "remake": sum(r["remake"] for r in rows),
                 "note": "KR matches collected by ml.pipeline (AGENTS §20); none are in data/*.jsonl", "quality": quality},
        "model": model_report(args.model_dir.resolve(), rows if args.model_rows == "all" else
                              [r for r in rows if r["remake"] or int(r["match"]["metadata"]["matchId"][:8], 16) % 3 == 0]),
        "model_rows": args.model_rows,
        "strategy": strategy_report(args.model_dir.resolve()),
        "review": review_report(args.review_module.resolve(), args.data.resolve()),
    }
    rep["gate_failures"] = gates(rep)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rep, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"out": str(args.out), "gate_failures": rep["gate_failures"]}, ensure_ascii=False))
    return 1 if (args.gate and rep["gate_failures"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
