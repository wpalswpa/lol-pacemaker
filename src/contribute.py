"""Opt-in anonymous game-stat contribution (AGENTS §14).

Nothing here runs unless the user ticked "share anonymous stats" in the consent window.
Only an allowlist leaves the PC: random install id, app version, language, tier bucket,
the 18 model features at 10/15/20/25 min, and whether blue won. No names, Riot IDs,
PUUIDs, champions, chat, or exact timestamps.
"""
from __future__ import annotations

import json
import threading
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from ml.features import EXTENDED_FEATURES

CONSENT_VERSION = 1
APP_VERSION = "0.2.2"
CONTRIB_URL = "https://lol-pacemaker-riot-proxy.jayu25si-video.workers.dev/v1/games"
CHECKPOINTS = (10, 15, 20, 25)
# Cloudflare blocks the default "Python-urllib" agent with error 1010 (seen 2026-11-26 on the live worker).
USER_AGENT = f"LoLPacemaker/{APP_VERSION}"
LANGS = ("en", "ko", "ja", "es", "pt")
TIERS = ("low", "mid", "high")


def new_install_id() -> str:
    return str(uuid.uuid4())


def my_team(raw: dict[str, Any]) -> str | None:
    """'blue' / 'red' for the local player, from the 2999 allPlayers list (ORDER = blue)."""
    active = raw.get("activePlayer") or {}
    me = active.get("riotId") or active.get("summonerName")
    for p in raw.get("allPlayers") or []:
        if me and me in (p.get("riotId"), p.get("summonerName")):
            team = str(p.get("team") or "").upper()
            return {"ORDER": "blue", "CHAOS": "red"}.get(team)
    return None


def game_result(raw: dict[str, Any]) -> str | None:
    """'Win' / 'Lose' from the GameEnd event (local player's view), else None."""
    for ev in (raw.get("events") or {}).get("Events") or []:
        if ev.get("EventName") == "GameEnd" and ev.get("Result") in ("Win", "Lose"):
            return ev["Result"]
    return None


def build_payload(install_id: str, lang: str, tier: str, snapshots: dict[int, dict[str, float]], blue_win: bool) -> dict[str, Any]:
    """Allowlisted body. Feature keys are copied by EXTENDED_FEATURES only, so nothing else can leak in."""
    return {
        "install_id": install_id,
        "app_version": APP_VERSION,
        "lang": lang if lang in LANGS else "en",
        "tier": tier if tier in TIERS else "mid",
        "blue_win": 1 if blue_win else 0,
        "snapshots": [
            {"minute": m, "features": {k: float(snapshots[m][k]) for k in EXTENDED_FEATURES}}
            for m in sorted(snapshots)
        ],
    }


def post_json(url: str, body: dict[str, Any], method: str = "POST", timeout: float = 5.0) -> int:
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (https constant)
        return resp.status


def request_deletion(install_id: str, send: Callable[..., int] = post_json) -> bool:
    """Withdrawal: ask the server to delete every stored game of this install id."""
    try:
        return send(f"{CONTRIB_URL}?install_id={install_id}", {}, method="DELETE") == 200
    except Exception:
        return False


@dataclass
class ContributionRecorder:
    """Fed by Engine on every poll. Records checkpoints, sends once at GameEnd if consented."""

    consented: Callable[[], bool]
    install_id: Callable[[], str]
    lang: Callable[[], str]
    tier: Callable[[], str]
    send: Callable[[dict[str, Any]], Any] = field(default=lambda body: threading.Thread(
        target=lambda: _quiet(post_json, CONTRIB_URL, body), daemon=True).start())
    snapshots: dict[int, dict[str, float]] = field(default_factory=dict)
    sent: bool = False
    _last_time: float = -1.0

    def reset(self) -> None:
        self.snapshots, self.sent, self._last_time = {}, False, -1.0

    def observe(self, raw: dict[str, Any], game_time: float, game_mode: str, features: dict[str, float]) -> bool:
        """Returns True when a game was handed to `send`."""
        if game_time < self._last_time - 30:  # clock went backwards: a new game
            self.reset()
        self._last_time = game_time
        if game_mode != "CLASSIC" or not self.consented():
            return False
        for m in CHECKPOINTS:
            if m not in self.snapshots and game_time >= m * 60 and game_time < m * 60 + 90:
                self.snapshots[m] = {k: float(features[k]) for k in EXTENDED_FEATURES}
        result = game_result(raw)
        if self.sent or result is None or not self.snapshots:
            return False
        team = my_team(raw)
        if team is None:
            return False
        blue_win = (result == "Win") == (team == "blue")
        self.send(build_payload(self.install_id(), self.lang(), self.tier(), self.snapshots, blue_win))
        self.sent = True
        return True


def _quiet(fn: Callable[..., Any], *a: Any, **kw: Any) -> None:
    try:
        fn(*a, **kw)
    except Exception:
        pass  # no retry / no local queue by design (AGENTS §14)
