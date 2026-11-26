"""Language-neutral real-time shotcaller engine for LoL Shotcaller.

Produces priority-ranked, contradiction-free tactical calls using message keys
and parameters so any locale in `locales/*.json` renders natively.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.i18n import Translator
from src.predictor import LocalOnnxPredictor, ScoreboardState

CallKind = Literal["offense", "defense", "object", "recall", "counter", "macro"]
Lane = Literal["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]


class LiveSnapshot(BaseModel):
    """Normalized live game state extracted from Riot 2999 Live Client API."""

    model_config = ConfigDict(extra="ignore")

    game_time: float = Field(default=0.0, ge=0.0)
    scoreboard: ScoreboardState = Field(default_factory=ScoreboardState)
    ally_alive: int = Field(default=5, ge=0, le=5)
    enemy_alive: int = Field(default=5, ge=0, le=5)
    enemy_max_respawn: float = Field(default=0.0, ge=0.0)
    ally_jungler_dead_sec: float = Field(default=0.0, ge=0.0)
    enemy_jungler_dead_sec: float = Field(default=0.0, ge=0.0)
    ally_dragons: int = Field(default=0, ge=0, le=7)
    enemy_dragons: int = Field(default=0, ge=0, le=7)
    ally_grubs: int = Field(default=0, ge=0, le=6)
    ally_carry_lane: Lane | None = None
    ally_carry_kills: int = Field(default=0, ge=0)
    my_current_gold: float = Field(default=500.0, ge=0.0)
    team_gold_diff: float = 0.0
    enemy_has_heavy_heal: bool = False
    enemy_has_heavy_tank: bool = False


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
    primary_obj = "key:obj_baron" if snap.game_time >= 1200 else "key:obj_dragon"

    # 1. Smite advantage / disadvantage (highest priority tactical signals)
    if snap.enemy_jungler_dead_sec >= 8.0 and snap.ally_jungler_dead_sec <= 0.0 and man_diff >= 0:
        calls.append(
            Shotcall(
                priority=105,
                kind="offense",
                title_key="call_smite_adv_title",
                detail_key="call_smite_adv_detail",
                params={"objective": primary_obj, "sec": int(round(snap.enemy_jungler_dead_sec))},
            )
        )
    elif snap.ally_jungler_dead_sec >= 8.0 and snap.enemy_jungler_dead_sec <= 0.0:
        calls.append(
            Shotcall(
                priority=102,
                kind="defense",
                title_key="call_smite_warn_title",
                detail_key="call_smite_warn_detail",
                params={"sec": int(round(snap.ally_jungler_dead_sec))},
            )
        )

    # 2. Numerical advantage / disadvantage with respawn window
    if man_diff >= 2:
        window_sec = max(10, int(round(snap.enemy_max_respawn)))
        calls.append(
            Shotcall(
                priority=99,
                kind="offense",
                title_key="call_num_adv_title",
                detail_key="call_num_adv_detail",
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

    # 3. Objective windows: Dragon Soul, Baron, Voidgrubs / Herald
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

    if 360 <= snap.game_time <= 840 and snap.ally_grubs < 6:
        calls.append(
            Shotcall(
                priority=82,
                kind="object",
                title_key="call_grubs_window_title",
                detail_key="call_grubs_window_detail",
                params={"ally_grubs": snap.ally_grubs},
            )
        )

    if snap.game_time >= 1200 and man_diff >= 1:
        calls.append(
            Shotcall(
                priority=90,
                kind="offense",
                title_key="call_baron_window_title",
                detail_key="call_baron_window_detail",
            )
        )

    # 4. Ally carry grouping in mid/late game
    if snap.game_time >= 840 and snap.ally_carry_lane and snap.ally_carry_kills >= 4:
        calls.append(
            Shotcall(
                priority=80,
                kind="macro",
                title_key="call_carry_group_title",
                detail_key="call_carry_group_detail",
                params={"lane": f"key:lane_{snap.ally_carry_lane}", "kills": snap.ally_carry_kills},
            )
        )

    # 5. Recall gold spike (never included in win_prob features)
    if snap.my_current_gold >= 1300:
        calls.append(
            Shotcall(
                priority=78,
                kind="recall",
                title_key="call_recall_gold_title",
                detail_key="call_recall_gold_detail",
                params={"gold": int(snap.my_current_gold)},
            )
        )

    # 6. Counter items
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

    if not calls:
        calls.append(
            Shotcall(
                priority=50,
                kind="macro",
                title_key="call_even_farm_title",
                detail_key="call_even_farm_detail",
            )
        )

    calls.sort(key=lambda c: c.priority, reverse=True)
    pace_key = _pace_note_key(snap.game_time)

    return {
        "lang": tr.lang,
        "app_title": tr.t("app_title"),
        "game_time": round(snap.game_time, 1),
        "phase": tr.t(_phase_key(snap.game_time)),
        "win_pct": win_pct,
        "state": tr.t(state_key),
        "team_gold_diff": int(snap.team_gold_diff),
        "pace_note": tr.t(pace_key) if pace_key else None,
        "calls": [c.render(tr) for c in calls[:max_calls]],
        "labels": {
            "win_rate": tr.t("hud_win_rate"),
            "gold_diff": tr.t("hud_gold_diff"),
            "hint_keys": tr.t("hud_hint_keys"),
        },
    }
