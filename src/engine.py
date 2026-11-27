"""UI-independent engine: Riot 2999 Live Client data -> LiveSnapshot -> localized result (every 2s)."""
from __future__ import annotations

import json
import ssl
import threading
import urllib.request
from typing import Any, Callable

from src.i18n import Translator
from src.icon_cache import normalize_champ_key
from src.predictor import LocalOnnxPredictor, ScoreboardState
from src.strategy_core import (
    BARON_FIRST_SPAWN_SEC,
    BARON_RESPAWN_SEC,
    DRAGON_FIRST_SPAWN_SEC,
    DRAGON_RESPAWN_SEC,
    Lane,
    LiveSnapshot,
    evaluate_snapshot,
    objective_up,
)

RIOT_LOCAL_URL = "https://127.0.0.1:2999/liveclientdata/allgamedata"
HEAL_ITEMS = {3072, 3153, 3142, 6676, 6630, 3065, 6617, 3116}
TANK_ITEMS = {3068, 3075, 3083, 3143, 3742, 6665, 3084}
VALID_LANES: set[str] = {"TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"}
MISSES_TO_WAIT = 3  # AGENTS §24: ~6 s of no 2999 answer = the match is over (one dropped request is tolerated)
LANE_CS_POSITIONS = {"TOP", "MIDDLE", "BOTTOM"}  # same set as ml.collector lane_cs_diff


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _names(p: dict[str, Any]) -> set[str]:
    return {str(p[k]) for k in ("summonerName", "riotIdGameName", "riotId") if p.get(k)}


def _has_smite(player: dict[str, Any]) -> bool:
    spells = player.get("summonerSpells") or {}
    for slot in ("summonerSpellOne", "summonerSpellTwo"):
        raw_name = str((spells.get(slot) or {}).get("rawDisplayName") or "").lower()
        disp_name = str((spells.get(slot) or {}).get("displayName") or "").lower()
        if "smite" in raw_name or "smite" in disp_name or "강타" in disp_name:
            return True
    return False


def parse_liveclient_data(raw: dict[str, Any], tier: str = "mid") -> LiveSnapshot:
    """Convert Riot 2999 allgamedata JSON into a normalized LiveSnapshot."""
    game_data = raw.get("gameData") or {}
    game_time = _safe_float(game_data.get("gameTime"), 0.0)
    raw_mode = str(game_data.get("gameMode") or "CLASSIC").upper()
    map_num = _safe_int(game_data.get("mapNumber"), 11)
    game_mode = "ARAM" if (raw_mode == "ARAM" or map_num == 12) else raw_mode
    players = raw.get("allPlayers") or []
    active = raw.get("activePlayer") or {}
    active_names = _names(active)

    my_side = "blue"
    me: dict[str, Any] | None = None
    for p in players:
        if active_names & _names(p):
            my_side = "red" if p.get("team") == "CHAOS" else "blue"
            me = p
            break

    blue_kills = red_kills = blue_assists = red_assists = blue_cs = red_cs = 0
    blue_lane_cs = red_lane_cs = 0
    blue_levels: list[int] = []
    red_levels: list[int] = []
    ally_alive = enemy_alive = 0
    enemy_max_respawn = 0.0
    ally_jg_dead = enemy_jg_dead = 0.0
    enemy_heal = enemy_tank = False
    ally_carry_lane: Lane | None = None
    ally_carry_kills = -1
    blue_names: set[str] = set()
    red_names: set[str] = set()

    for p in players:
        is_blue = p.get("team", "ORDER") == "ORDER"
        (blue_names if is_blue else red_names).update(_names(p))
        scores = p.get("scores") or {}
        kills = _safe_int(scores.get("kills"), 0)
        assists = _safe_int(scores.get("assists"), 0)
        cs = _safe_int(scores.get("creepScore"), 0)
        lvl = max(1, min(18, _safe_int(p.get("level"), 1)))
        is_dead = bool(p.get("isDead"))
        respawn = max(0.0, _safe_float(p.get("respawnTimer"), 0.0)) if is_dead else 0.0
        pos = str(p.get("position") or "").upper()
        item_ids = {
            _safe_int(it.get("itemID"), 0)
            for it in (p.get("items") or [])
            if isinstance(it, dict) and _safe_int(it.get("itemID"), 0) > 0
        }
        lane_cs = cs if pos in LANE_CS_POSITIONS else 0

        if is_blue:
            blue_kills += kills
            blue_assists += assists
            blue_cs += cs
            blue_lane_cs += lane_cs
            blue_levels.append(lvl)
        else:
            red_kills += kills
            red_assists += assists
            red_cs += cs
            red_lane_cs += lane_cs
            red_levels.append(lvl)

        is_ally = (is_blue and my_side == "blue") or ((not is_blue) and my_side == "red")
        is_jg = pos == "JUNGLE" or _has_smite(p)

        if is_ally:
            if not is_dead:
                ally_alive += 1
            elif is_jg:
                ally_jg_dead = max(ally_jg_dead, respawn)
            if pos in VALID_LANES and kills > ally_carry_kills:
                ally_carry_kills = kills
                ally_carry_lane = pos  # type: ignore[assignment]
        else:
            if not is_dead:
                enemy_alive += 1
            else:
                enemy_max_respawn = max(enemy_max_respawn, respawn)
                if is_jg:
                    enemy_jg_dead = max(enemy_jg_dead, respawn)
            if item_ids & HEAL_ITEMS:
                enemy_heal = True
            if item_ids & TANK_ITEMS:
                enemy_tank = True

    c = {k: [0, 0] for k in ("fb", "dragon", "herald", "tower", "grub", "baron", "elder", "inhib", "recent")}  # [blue, red]
    last_dragon_t = last_baron_t = None  # AGENTS §19: objective respawn gating

    def bump(key: str, name: Any) -> None:
        n = str(name or "")
        if n in blue_names:
            c[key][0] += 1
        elif n in red_names:
            c[key][1] += 1

    for ev in ((raw.get("events") or {}).get("Events") or []):
        ename = ev.get("EventName")
        if ename == "FirstBlood":
            bump("fb", ev.get("Recipient"))
        elif ename == "DragonKill":
            bump("elder" if str(ev.get("DragonType") or "").lower() == "elder" else "dragon", ev.get("KillerName"))
            last_dragon_t = _safe_float(ev.get("EventTime"), game_time)
        elif ename == "HeraldKill":
            bump("herald", ev.get("KillerName"))
        elif ename == "HordeKill":
            bump("grub", ev.get("KillerName"))
        elif ename == "BaronKill":
            bump("baron", ev.get("KillerName"))
            last_baron_t = _safe_float(ev.get("EventTime"), game_time)
        elif ename == "TurretKilled":
            tname = str(ev.get("TurretName") or "")
            if "T2" in tname:
                c["tower"][0] += 1
            elif "T1" in tname:
                c["tower"][1] += 1
        elif ename == "InhibKilled":
            iname = str(ev.get("InhibName") or "")
            if "T2" in iname:
                c["inhib"][0] += 1
            elif "T1" in iname:
                c["inhib"][1] += 1
        elif ename == "ChampionKill" and _safe_float(ev.get("EventTime"), -1.0) >= game_time - 180.0:
            bump("recent", ev.get("KillerName"))

    sb = ScoreboardState(
        my_side=my_side,
        game_time_min=min(120.0, game_time / 60.0),
        tier=tier if tier in ("low", "mid", "high") else "mid",  # type: ignore[arg-type]
        blue_first_blood=min(1, c["fb"][0]),
        red_first_blood=min(1, c["fb"][1]),
        blue_kills=blue_kills,
        red_kills=red_kills,
        blue_assists=blue_assists,
        red_assists=red_assists,
        blue_cs=blue_cs,
        red_cs=red_cs,
        blue_avg_level=(sum(blue_levels) / len(blue_levels)) if blue_levels else 1.0,
        red_avg_level=(sum(red_levels) / len(red_levels)) if red_levels else 1.0,
        blue_dragons=c["dragon"][0],
        red_dragons=c["dragon"][1],
        blue_heralds=c["herald"][0],
        red_heralds=c["herald"][1],
        blue_towers=c["tower"][0],
        red_towers=c["tower"][1],
        blue_voidgrubs=c["grub"][0],
        red_voidgrubs=c["grub"][1],
        blue_barons=c["baron"][0],
        red_barons=c["baron"][1],
        blue_elder=c["elder"][0],
        red_elder=c["elder"][1],
        blue_inhibitors=c["inhib"][0],
        red_inhibitors=c["inhib"][1],
        recent_3m_kill_diff=max(-60, min(60, c["recent"][0] - c["recent"][1])),
        lane_cs_diff=max(-3000, min(3000, blue_lane_cs - red_lane_cs)),
    )
    blue_side = my_side == "blue"
    ally_drag, enemy_drag = (c["dragon"][0], c["dragon"][1]) if blue_side else (c["dragon"][1], c["dragon"][0])
    ally_grubs, enemy_grubs = (c["grub"][0], c["grub"][1]) if blue_side else (c["grub"][1], c["grub"][0])

    my_scores = (me or {}).get("scores") or {}
    ally_team_kills = blue_kills if blue_side else red_kills
    my_pos = str((me or {}).get("position") or "").upper()

    return LiveSnapshot(
        game_time=game_time,
        game_mode=game_mode,
        scoreboard=sb,
        # AGENTS §19: an aced team really has 0 alive; only an empty player list means "unknown" (5v5)
        ally_alive=ally_alive if players else 5,
        enemy_alive=enemy_alive if players else 5,
        enemy_max_respawn=enemy_max_respawn,
        ally_jungler_dead_sec=ally_jg_dead,
        enemy_jungler_dead_sec=enemy_jg_dead,
        ally_dragons=ally_drag,
        enemy_dragons=enemy_drag,
        ally_grubs=ally_grubs,
        enemy_grubs=enemy_grubs,
        dragon_up=objective_up(game_time, last_dragon_t, DRAGON_FIRST_SPAWN_SEC, DRAGON_RESPAWN_SEC),
        baron_up=objective_up(game_time, last_baron_t, BARON_FIRST_SPAWN_SEC, BARON_RESPAWN_SEC),
        my_dead=bool((me or {}).get("isDead")),
        ally_carry_lane=ally_carry_lane,
        ally_carry_kills=max(0, ally_carry_kills),
        my_current_gold=_safe_float(active.get("currentGold"), 0.0),
        enemy_has_heavy_heal=enemy_heal,
        enemy_has_heavy_tank=enemy_tank,
        my_position=my_pos if my_pos in VALID_LANES else None,  # type: ignore[arg-type]
        my_cs=_safe_int(my_scores.get("creepScore"), 0),
        my_level=max(1, min(18, _safe_int((me or {}).get("level"), 1))),
        my_kill_participation=(
            (_safe_int(my_scores.get("kills")) + _safe_int(my_scores.get("assists"))) / ally_team_kills
            if ally_team_kills > 0 and me is not None else None
        ),
    )


def extract_hud_extras(raw: dict[str, Any]) -> dict[str, Any]:
    """Extract read-only 2999 UI extras: per-lane item gold diff, live timers, and gold/deaths."""
    game_data = raw.get("gameData") or {}
    game_time = _safe_float(game_data.get("gameTime"), 0.0)
    players = raw.get("allPlayers") or []
    active = raw.get("activePlayer") or {}
    active_names = _names(active)

    my_side = "blue"
    for p in players:
        if active_names & _names(p):
            my_side = "red" if p.get("team") == "CHAOS" else "blue"
            break

    lanes = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")
    blue_by_lane: dict[str, dict[str, Any]] = {}
    red_by_lane: dict[str, dict[str, Any]] = {}
    blue_fallback: list[dict[str, Any]] = []
    red_fallback: list[dict[str, Any]] = []
    blue_total = red_total = ally_dead = enemy_dead = 0

    for p in players:
        is_blue = p.get("team", "ORDER") == "ORDER"
        is_ally = (is_blue and my_side == "blue") or ((not is_blue) and my_side == "red")
        if bool(p.get("isDead")):
            if is_ally:
                ally_dead += 1
            else:
                enemy_dead += 1
        gold = 0
        for it in (p.get("items") or []):
            if isinstance(it, dict):
                gold += max(0, _safe_int(it.get("price"), 0)) * max(1, _safe_int(it.get("count"), 1))
        if is_blue:
            blue_total += gold
        else:
            red_total += gold
        entry = {
            "name": str(p.get("championName") or ""),
            "key": normalize_champ_key(str(p.get("rawChampionName") or p.get("championName") or "")),
            "gold": gold,
            "is_me": bool(active_names & _names(p)),
        }
        pos = str(p.get("position") or "").upper()
        target = blue_by_lane if is_blue else red_by_lane
        if pos in lanes and pos not in target:
            target[pos] = entry
        else:
            (blue_fallback if is_blue else red_fallback).append(entry)

    rows = []
    for lane in lanes:
        b = blue_by_lane.get(lane) or (blue_fallback.pop(0) if blue_fallback else {"name": "-", "key": "", "gold": 0, "is_me": False})
        r = red_by_lane.get(lane) or (red_fallback.pop(0) if red_fallback else {"name": "-", "key": "", "gold": 0, "is_me": False})
        rows.append({"lane": lane, "blue": b, "red": r, "diff": b["gold"] - r["gold"]})

    last_dragon_t: float | None = None
    last_baron_t: float | None = None
    for ev in ((raw.get("events") or {}).get("Events") or []):
        en = ev.get("EventName")
        et = _safe_float(ev.get("EventTime"), 0.0)
        if en == "DragonKill":
            last_dragon_t = max(last_dragon_t or 0.0, et)
        elif en == "BaronKill":
            last_baron_t = max(last_baron_t or 0.0, et)

    dragon_next = DRAGON_FIRST_SPAWN_SEC if last_dragon_t is None else last_dragon_t + DRAGON_RESPAWN_SEC
    baron_next = BARON_FIRST_SPAWN_SEC if last_baron_t is None else last_baron_t + BARON_RESPAWN_SEC
    my_team_diff = (blue_total - red_total) if my_side == "blue" else (red_total - blue_total)
    return {
        "game_time_sec": int(game_time),
        "my_gold": int(_safe_float(active.get("currentGold"), 0.0)),
        "team_item_gold_diff": my_team_diff,
        "ally_dead": ally_dead,
        "enemy_dead": enemy_dead,
        "item_compare": {"blue_total": blue_total, "red_total": red_total, "rows": rows},
        "obj_timers": {
            "dragon_sec": max(0, int(dragon_next - game_time)),
            "baron_sec": max(0, int(baron_next - game_time)),
        },
    }


def fetch_liveclient() -> dict[str, Any]:
    """Read Riot's local Live Client API (self-signed cert on 127.0.0.1 only)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(urllib.request.Request(RIOT_LOCAL_URL), context=ctx, timeout=1.5) as resp:
        return json.loads(resp.read().decode("utf-8"))


class Engine:
    """Holds the latest localized result; switching language re-renders instantly without a new fetch."""

    def __init__(
        self,
        predictor: LocalOnnxPredictor,
        translator: Translator,
        tier: str = "mid",
        fetch: Callable[[], dict[str, Any]] = fetch_liveclient,
        on_poll: Callable[[dict[str, Any], LiveSnapshot], None] | None = None,
    ) -> None:
        self.predictor = predictor
        self.tr = translator
        self.tier = tier
        self._fetch = fetch
        self._on_poll = on_poll  # AGENTS §14: opt-in contribution recorder (never raises into the loop)
        self._lock = threading.Lock()
        self._snap: LiveSnapshot | None = None
        self._extras: dict[str, Any] = {}
        self.latest: dict[str, Any] = {"status": "waiting"}
        self._misses = 0  # consecutive failed polls (AGENTS §24)

    def _render(self) -> None:
        if self._snap is not None:
            out = evaluate_snapshot(self._snap, self.predictor, self.tr)
            if self._extras:
                out["hud_extras"] = self._extras
            self.latest = out

    def step(self) -> bool:
        """One poll cycle. Returns True when a game was read, False when LoL is not running."""
        try:
            raw = self._fetch()
            snap = parse_liveclient_data(raw, self.tier)
            extras = extract_hud_extras(raw)
        except Exception:
            self._misses += 1
            if self._misses >= MISSES_TO_WAIT:
                self._reset_waiting()
            return False
        self._misses = 0
        if any(ev.get("EventName") == "GameEnd" for ev in ((raw.get("events") or {}).get("Events") or [])):
            self._notify(raw, snap)  # §14 recorder needs GameEnd's Result before we forget the match
            self._reset_waiting()  # the post-game screen still answers 2999; the match is over
            return False
        with self._lock:
            self._snap = snap
            self._extras = extras
            self._render()
        self._notify(raw, snap)
        return True

    def _notify(self, raw: dict[str, Any], snap: LiveSnapshot) -> None:
        if self._on_poll is not None:
            try:
                self._on_poll(raw, snap)
            except Exception:
                pass

    def _reset_waiting(self) -> None:
        """Back to 'no match': the HUD hides instead of showing the last game's numbers (AGENTS §24)."""
        with self._lock:
            self._snap = None
            self._extras = {}
            self.latest = {"status": "waiting"}

    def set_lang(self, lang: str) -> None:
        with self._lock:
            self.tr = Translator(lang=lang)
            self._render()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self.latest)

    def run(self, stop: threading.Event, interval: float = 2.0) -> None:
        while not stop.is_set():
            self.step()
            stop.wait(interval)
