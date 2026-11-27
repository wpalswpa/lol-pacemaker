"""Language-neutral real-time shotcaller engine for LoL Shotcaller.

Produces priority-ranked, contradiction-free tactical calls using message keys
and parameters so any locale in `locales/*.json` renders natively.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.i18n import Translator
from src.percentile import lookup_percentiles, top_percentile
from src.predictor import LocalOnnxPredictor, ScoreboardState

# Game constants (Summoner's Rift, patch 16.x; re-check each season). AGENTS §19.
DRAGON_FIRST_SPAWN_SEC = 300.0
DRAGON_RESPAWN_SEC = 300.0
BARON_FIRST_SPAWN_SEC = 1200.0
BARON_RESPAWN_SEC = 360.0
# Voidgrubs available per game: max |voidgrubs_diff| in the 90 bundled KR matches (patch 16.x) is 3.
VOIDGRUBS_TOTAL = 3


def objective_up(game_time: float, last_kill_t: float | None, first_spawn: float, respawn: float) -> bool:
    """True when the objective is (approximately) alive: spawned and not inside its respawn timer."""
    if game_time < first_spawn:
        return False
    return last_kill_t is None or game_time - last_kill_t >= respawn


CallKind = Literal["offense", "defense", "object", "recall", "counter", "macro"]
Lane = Literal["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]


class LiveSnapshot(BaseModel):
    """Normalized live game state extracted from Riot 2999 Live Client API."""

    model_config = ConfigDict(extra="ignore")

    game_time: float = Field(default=0.0, ge=0.0)
    game_mode: str = Field(default="CLASSIC")
    scoreboard: ScoreboardState = Field(default_factory=ScoreboardState)
    ally_alive: int = Field(default=5, ge=0, le=5)
    enemy_alive: int = Field(default=5, ge=0, le=5)
    enemy_max_respawn: float = Field(default=0.0, ge=0.0)
    ally_jungler_dead_sec: float = Field(default=0.0, ge=0.0)
    enemy_jungler_dead_sec: float = Field(default=0.0, ge=0.0)
    ally_dragons: int = Field(default=0, ge=0, le=7)
    enemy_dragons: int = Field(default=0, ge=0, le=7)
    ally_grubs: int = Field(default=0, ge=0, le=6)
    enemy_grubs: int = Field(default=0, ge=0, le=6)
    dragon_up: bool = True
    baron_up: bool = True
    my_dead: bool = False
    ally_carry_lane: Lane | None = None
    ally_carry_kills: int = Field(default=0, ge=0)
    my_current_gold: float = Field(default=500.0, ge=0.0)
    team_gold_diff: float = 0.0
    enemy_has_heavy_heal: bool = False
    enemy_has_heavy_tank: bool = False
    # Active player's own numbers (for tier-distribution Top-% ranking)
    my_position: Lane | None = None
    my_cs: int = Field(default=0, ge=0)
    my_level: int = Field(default=1, ge=1, le=18)
    my_kill_participation: float | None = None  # (kills + assists) / ally team kills, None if no team kills yet


class Shotcall(BaseModel):
    priority: int
    kind: CallKind
    title_key: str
    detail_key: str
    params: dict[str, Any] = Field(default_factory=dict)

    def render(self, tr: Translator) -> dict[str, Any]:
        return {
            "priority": self.priority,
            "kind": self.kind,
            "title": tr.t(self.title_key, **self.params),
            "detail": tr.t(self.detail_key, **self.params),
        }


def _phase_key(game_time: float) -> str:
    if game_time < 840:  # < 14:00
        return "phase_early"
    if game_time < 1500:  # < 25:00
        return "phase_mid"
    return "phase_late"


def _pace_note_key(game_time: float) -> str | None:
    if game_time < 450:
        return "pace_early_note"
    if game_time >= 1200:
        return "pace_late_note"
    if game_time >= 720:
        return "pace_mid_note"
    return None


def _rank_block(snap: LiveSnapshot, predictor: LocalOnnxPredictor, tr: Translator) -> dict[str, Any]:
    """Top-% standing: tier distribution (AGENTS.md 10.4) if available, else legacy team-diff heuristic."""
    sb = snap.scoreboard
    minutes = max(snap.game_time / 60.0, 1.0)
    # AGENTS §19: the tier distribution comes from ranked Summoner's Rift; other modes use the legacy diff view
    stats = predictor.stats if snap.game_mode.upper() == "CLASSIC" else None
    found = lookup_percentiles(
        stats,
        sb.tier,
        snap.game_time,
        snap.my_position,
        {
            "cs_per_min": snap.my_cs / minutes,
            "level": float(snap.my_level),
            "kill_participation": snap.my_kill_participation,
        },
    )
    if found:
        pct: dict[str, int] = found["pct"]
        def fmt(metric: str, v: float) -> str:  # same unit the player sees in game
            return f"{round(v * 100)}%" if metric == "kill_participation" else f"{v:.1f}"

        items = [
            tr.t("rank_item_avg", metric=f"key:metric_{m}", value=fmt(m, found["value"][m]), mean=fmt(m, found["mean"][m]), pct=pct[m])
            for m in ("cs_per_min", "level", "kill_participation")
            if m in pct
        ]
        approx = tr.t("rank_approx", tier=f"key:tier_{sb.tier}", minute=found["minute"])
        return {
            "mode": "distribution", "tier": sb.tier, "pct": pct, "summary": " · ".join(items) + " " + approx,
            "value": {m: found["value"][m] for m in pct}, "mean": {m: found["mean"][m] for m in pct}, "fmt": fmt,
        }

    # Legacy: team CS/level lead against a fixed spread (no tier data available)
    sign = 1.0 if sb.my_side == "blue" else -1.0
    cs_pct = top_percentile(sign * float(sb.blue_cs - sb.red_cs), mean=0.0, std=25.0)
    lvl_pct = top_percentile(sign * float(sb.blue_avg_level - sb.red_avg_level), mean=0.0, std=0.8)
    return {
        "mode": "legacy",
        "pct": {"cs_per_min": cs_pct, "level": lvl_pct},
        "summary": tr.t("rank_summary", cs_pct=cs_pct, lvl_pct=lvl_pct),
    }


def _causes(snap: LiveSnapshot, predictor: LocalOnnxPredictor) -> list[tuple[str, float]]:
    return predictor.explain(snap.scoreboard)


# AGENTS §26: features that have a plain-language "why_/fix_" sentence (all extended, non-masked, non-context ones).
PLAIN_FEATURES = (
    "kills_diff", "assists_diff", "cs_diff", "avg_level_diff", "dragons_diff", "heralds_diff", "towers_diff",
    "voidgrubs_diff", "barons_diff", "elder_diff", "inhibitors_diff", "recent_3m_kill_diff", "lane_cs_diff",
)
CAUSE_MIN_PP = 1.0  # a cause must cost at least this many win-% points to be mentioned
ME_GAP_MIN = 0.10  # my metric must differ from the tier average by at least 10% of the average


def _my_gap(snap: LiveSnapshot, feature: str) -> float:
    """Feature difference from my team's side (positive = my team ahead)."""
    sign = 1.0 if snap.scoreboard.my_side == "blue" else -1.0
    return sign * snap.scoreboard.to_feature_dict().get(feature, 0.0)


def _smart_briefing(
    snap: LiveSnapshot,
    win_pct: float,
    rank: dict[str, Any],
    causes: list[tuple[str, float]],
    calls: list[dict[str, Any]],
    calls_are_fallback: bool,
    tr: Translator,
) -> str:
    """Plain-language briefing (AGENTS §26): what happened + the number behind it + what to do now."""
    parts: list[str] = []
    cause = None
    if causes and causes[0][1] <= -CAUSE_MIN_PP and causes[0][0] in PLAIN_FEATURES:
        feature, delta = causes[0]
        gap = _my_gap(snap, feature)
        if gap < 0:  # never blame a gap my team is actually winning (§26 rule 5)
            cause = feature
            n = f"{abs(gap):.1f}" if feature == "avg_level_diff" else str(int(round(abs(gap))))
            parts.append(tr.t(f"why_{feature}", n=n))
            even = min(99.0, max(1.0, win_pct - delta))
            parts.append(tr.t("why_impact", now=f"{win_pct:.0f}", even=f"{even:.0f}"))
    if cause is None:
        parts.append(tr.t("briefing_cause_none"))

    if rank["mode"] == "distribution" and rank.get("mean"):
        rel = {m: (rank["value"][m] - rank["mean"][m]) / rank["mean"][m] for m in rank["mean"] if rank["mean"][m] > 0}
        below = [m for m in rel if rel[m] <= -ME_GAP_MIN]
        above = [m for m in rel if rel[m] >= ME_GAP_MIN]
        pick, key = (min(below, key=lambda m: rel[m]), "me_below") if below else (
            (max(above, key=lambda m: rel[m]), "me_above") if above else (None, ""))
        if pick:
            fmt = rank["fmt"]
            parts.append(tr.t(key, metric=f"key:metric_{pick}", value=fmt(pick, rank["value"][pick]), mean=fmt(pick, rank["mean"][pick])))

    if calls and not (calls_are_fallback and cause):
        parts.append(tr.t("briefing_action", title=calls[0]["title"]))
    elif cause:
        parts.append(tr.t("briefing_action", title=tr.t(f"fix_{cause}")))
    return ("" if tr.lang == "ja" else " ").join(parts)  # Japanese runs sentences together without spaces


def evaluate_snapshot(
    snap: LiveSnapshot,
    predictor: LocalOnnxPredictor,
    tr: Translator,
    max_calls: int = 3,
) -> dict[str, Any]:
    """Evaluate live snapshot and return localized win-rate + top shotcalls."""
    win_prob = predictor.predict_win_prob(snap.scoreboard)
    win_pct = round(win_prob * 100.0, 1)

    if win_pct >= 55.0:
        state_key = "state_ahead"
    elif win_pct <= 45.0:
        state_key = "state_behind"
    else:
        state_key = "state_even"

    calls: list[Shotcall] = []
    man_diff = snap.ally_alive - snap.enemy_alive
    is_aram = snap.game_mode.upper() == "ARAM"
    if is_aram:
        primary_obj = "key:obj_turret"
    elif snap.game_time >= BARON_FIRST_SPAWN_SEC and snap.baron_up:
        primary_obj = "key:obj_baron"
    elif snap.dragon_up:
        primary_obj = "key:obj_dragon"
    else:
        primary_obj = "key:obj_turret"
    smite_objective_up = snap.dragon_up or (snap.game_time >= BARON_FIRST_SPAWN_SEC and snap.baron_up)

    # 1. Smite advantage / disadvantage (Summoner's Rift only)
    if (not is_aram and smite_objective_up and snap.enemy_jungler_dead_sec >= 8.0
            and snap.ally_jungler_dead_sec <= 0.0 and man_diff >= 0):
        calls.append(
            Shotcall(
                priority=105,
                kind="offense",
                title_key="call_smite_adv_title",
                detail_key="call_smite_adv_detail",
                params={"objective": primary_obj, "sec": int(round(snap.enemy_jungler_dead_sec))},
            )
        )
    elif not is_aram and snap.ally_jungler_dead_sec >= 8.0 and snap.enemy_jungler_dead_sec <= 0.0:
        calls.append(
            Shotcall(
                priority=102,
                kind="defense",
                title_key="call_smite_warn_title",
                detail_key="call_smite_warn_detail",
                params={"sec": int(round(snap.ally_jungler_dead_sec))},
            )
        )

    # 2. Numerical advantage / disadvantage with respawn window (all modes)
    if man_diff >= 2:
        window_sec = max(10, int(round(snap.enemy_max_respawn)))
        calls.append(
            Shotcall(
                priority=99,
                kind="offense",
                title_key="call_num_adv_title",
                # "towers or {objective}" would read "towers or towers" when no epic objective is up (and always on ARAM)
                detail_key="call_num_adv_detail_towers" if primary_obj == "key:obj_turret" else "call_num_adv_detail",
                params={
                    "ally": snap.ally_alive,
                    "enemy": snap.enemy_alive,
                    "enemy_dead": 5 - snap.enemy_alive,
                    "sec": window_sec,
                    "objective": primary_obj,
                },
            )
        )
    elif man_diff <= -2:
        calls.append(
            Shotcall(
                priority=98,
                kind="defense",
                title_key="call_num_disadv_title",
                detail_key="call_num_disadv_detail",
                params={"ally": snap.ally_alive, "enemy": snap.enemy_alive},
            )
        )

    # 3. Objective windows: Dragon Soul, Baron, Voidgrubs / Herald (Summoner's Rift only)
    if not is_aram:
        if snap.enemy_dragons >= 3:
            calls.append(
                Shotcall(
                    priority=92,
                    kind="object",
                    title_key="call_soul_threat_title",
                    detail_key="call_soul_threat_detail",
                    params={"enemy_dragons": snap.enemy_dragons},
                )
            )
        elif snap.ally_dragons == 3:
            calls.append(
                Shotcall(
                    priority=88,
                    kind="offense",
                    title_key="call_soul_point_title",
                    detail_key="call_soul_point_detail",
                    params={"ally_dragons": snap.ally_dragons},
                )
            )

        if 360 <= snap.game_time <= 840 and snap.ally_grubs + snap.enemy_grubs < VOIDGRUBS_TOTAL:
            calls.append(
                Shotcall(
                    priority=82,
                    kind="object",
                    title_key="call_grubs_window_title",
                    detail_key="call_grubs_window_detail",
                    params={"ally_grubs": snap.ally_grubs},
                )
            )

        if snap.game_time >= BARON_FIRST_SPAWN_SEC and snap.baron_up and man_diff >= 1:
            calls.append(
                Shotcall(
                    priority=90,
                    kind="offense",
                    title_key="call_baron_window_title",
                    detail_key="call_baron_window_detail",
                )
            )

    # 4. Ally carry grouping in mid/late game (Summoner's Rift)
    if not is_aram and snap.game_time >= 840 and snap.ally_carry_lane and snap.ally_carry_kills >= 4:
        calls.append(
            Shotcall(
                priority=80,
                kind="macro",
                title_key="call_carry_group_title",
                detail_key="call_carry_group_detail",
                params={"lane": f"key:lane_{snap.ally_carry_lane}", "kills": snap.ally_carry_kills},
            )
        )

    # 5. Gold spending timing: Recall on Rift (>=1300G) vs Fight-Reset on ARAM (>=2600G)
    if is_aram:
        if snap.my_current_gold >= 2600 and not snap.my_dead:
            calls.append(
                Shotcall(
                    priority=78,
                    kind="recall",
                    title_key="call_aram_reset_title",
                    detail_key="call_aram_reset_detail",
                    params={"gold": int(snap.my_current_gold)},
                )
            )
    elif snap.my_current_gold >= 1300 and not snap.my_dead:
        calls.append(
            Shotcall(
                priority=78,
                kind="recall",
                title_key="call_recall_gold_title",
                detail_key="call_recall_gold_detail",
                params={"gold": int(snap.my_current_gold)},
            )
        )

    # 6. Counter items (all modes)
    if snap.enemy_has_heavy_heal:
        calls.append(
            Shotcall(
                priority=74,
                kind="counter",
                title_key="call_counter_heal_title",
                detail_key="call_counter_heal_detail",
            )
        )
    if snap.enemy_has_heavy_tank:
        calls.append(
            Shotcall(
                priority=70,
                kind="counter",
                title_key="call_counter_tank_title",
                detail_key="call_counter_tank_detail",
            )
        )

    # Contradiction filter: when outnumbered or jungler dead defensively, drop offensive calls
    if any(c.kind == "defense" for c in calls):
        calls = [c for c in calls if c.kind != "offense"]

    calls_are_fallback = not calls
    if not calls:
        calls.append(
            Shotcall(
                priority=50,
                kind="macro",
                title_key="call_aram_poke_title" if is_aram else "call_even_farm_title",
                detail_key="call_aram_poke_detail" if is_aram else "call_even_farm_detail",
            )
        )

    calls.sort(key=lambda c: c.priority, reverse=True)
    pace_key = _pace_note_key(snap.game_time)

    sb = snap.scoreboard
    rank_info = _rank_block(snap, predictor, tr)
    causes = _causes(snap, predictor)
    calls_out = [c.render(tr) for c in calls[:max_calls]]
    smart_briefing = _smart_briefing(snap, win_pct, rank_info, causes, calls_out, calls_are_fallback, tr)

    return {
        "lang": tr.lang,
        "app_title": tr.t("app_title"),
        "game_time": round(snap.game_time, 1),
        "game_mode": snap.game_mode,
        "phase": tr.t("phase_aram" if is_aram else _phase_key(snap.game_time)),
        "win_pct": win_pct,
        "state": tr.t(state_key),
        "team_gold_diff": int(snap.team_gold_diff),
        "pace_note": tr.t(pace_key) if pace_key else None,
        "rank_mode": rank_info["mode"],
        "rank_summary": rank_info["summary"],
        "rank": rank_info["pct"],
        "causes": [{"feature": f, "label": tr.t(f"feat_{f}"), "delta_pp": d} for f, d in causes],
        "model_mode": predictor.mode,
        "smart_briefing": smart_briefing,
        "calls": calls_out,
        "labels": {
            "model": tr.t("hud_model_extended" if predictor.mode == "extended" else "hud_model_legacy"),
            "win_rate": tr.t("hud_win_rate"),
            "gold_diff": tr.t("hud_gold_diff"),
            "hint_keys": tr.t("hud_hint_keys"),
        },
    }
