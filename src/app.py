"""Zero-server local bridge + lightweight companion HUD HTTP server.

Polls Riot's official local Live Client Data API (127.0.0.1:2999) every 2s,
runs ONNX + shotcaller locally in-process, and serves a compact companion HUD
at http://127.0.0.1:<port>/overlay with zero cloud dependency.
"""
from __future__ import annotations

import argparse
import json
import ssl
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from src.i18n import Translator
from src.predictor import LocalOnnxPredictor, ScoreboardState
from src.strategy_core import Lane, LiveSnapshot, evaluate_snapshot

RIOT_LOCAL_URL = "https://127.0.0.1:2999/liveclientdata/allgamedata"
HEAL_ITEMS = {3072, 3153, 3142, 6676, 6630, 3065, 6617, 3116}
TANK_ITEMS = {3068, 3075, 3083, 3143, 3742, 6665, 3084}
VALID_LANES: set[str] = {"TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"}


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


def _has_smite(player: dict[str, Any]) -> bool:
    spells = player.get("summonerSpells") or {}
    for slot in ("summonerSpellOne", "summonerSpellTwo"):
        raw_name = str((spells.get(slot) or {}).get("rawDisplayName") or "").lower()
        disp_name = str((spells.get(slot) or {}).get("displayName") or "").lower()
        if "smite" in raw_name or "smite" in disp_name or "강타" in disp_name:
            return True
    return False


def parse_liveclient_data(raw: dict[str, Any]) -> LiveSnapshot:
    """Convert Riot 2999 allgamedata JSON into a normalized LiveSnapshot."""
    game_time = float((raw.get("gameData") or {}).get("gameTime") or 0.0)
    players = raw.get("allPlayers") or []
    active = raw.get("activePlayer") or {}
    active_name = active.get("summonerName") or active.get("riotIdGameName") or ""

    my_side = "blue"
    for p in players:
        pname = p.get("riotIdGameName") or p.get("summonerName") or ""
        if active_name and pname == active_name:
            my_side = "red" if p.get("team") == "CHAOS" else "blue"
            break

    blue_kills = red_kills = blue_assists = red_assists = blue_cs = red_cs = 0
    blue_levels: list[int] = []
    red_levels: list[int] = []
    ally_alive = enemy_alive = 0
    enemy_max_respawn = 0.0
    ally_jg_dead = enemy_jg_dead = 0.0
    enemy_heal = enemy_tank = False
    ally_carry_lane: Lane | None = None
    ally_carry_kills = -1

    for p in players:
        is_blue = p.get("team", "ORDER") == "ORDER"
        scores = p.get("scores") or {}
        kills = _safe_int((scores.get("kills")), 0)
        lvl = max(1, min(18, _safe_int(p.get("level"), 1)))
        is_dead = bool(p.get("isDead"))
        respawn = max(0.0, _safe_float(p.get("respawnTimer"), 0.0)) if is_dead else 0.0
        pos = str(p.get("position") or "").upper()
        item_ids = {
            _safe_int(it.get("itemID"), 0)
            for it in (p.get("items") or [])
            if isinstance(it, dict) and _safe_int(it.get("itemID"), 0) > 0
        }

        if is_blue:
            blue_kills += kills
            blue_assists += int(scores.get("assists") or 0)
            blue_cs += int(scores.get("creepScore") or 0)
            blue_levels.append(lvl)
        else:
            red_kills += kills
            red_assists += int(scores.get("assists") or 0)
            red_cs += int(scores.get("creepScore") or 0)
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

    blue_fb = 0
    blue_drag = red_drag = blue_herald = red_herald = blue_tower = red_tower = 0
    blue_grubs = red_grubs = 0
    blue_names = {
        p.get("summonerName") or p.get("riotIdGameName")
        for p in players
        if p.get("team", "ORDER") == "ORDER"
    }
    for ev in ((raw.get("events") or {}).get("Events") or []):
        ename = ev.get("EventName")
        killer = ev.get("KillerName")
        if ename == "FirstBlood":
            blue_fb = 1 if ev.get("Recipient") in blue_names else 0
        elif ename == "DragonKill":
            if killer in blue_names:
                blue_drag += 1
            else:
                red_drag += 1
        elif ename == "HeraldKill":
            if killer in blue_names:
                blue_herald += 1
            else:
                red_herald += 1
        elif ename == "HordeKill":
            if killer in blue_names:
                blue_grubs += 1
            else:
                red_grubs += 1
        elif ename == "TurretKilled":
            tname = str(ev.get("TurretName") or "")
            if "T2" in tname:
                blue_tower += 1
            elif "T1" in tname:
                red_tower += 1

    sb = ScoreboardState(
        my_side=my_side,
        blue_first_blood=blue_fb,
        blue_kills=blue_kills,
        red_kills=red_kills,
        blue_assists=blue_assists,
        red_assists=red_assists,
        blue_cs=blue_cs,
        red_cs=red_cs,
        blue_avg_level=(sum(blue_levels) / len(blue_levels)) if blue_levels else 1.0,
        red_avg_level=(sum(red_levels) / len(red_levels)) if red_levels else 1.0,
        blue_dragons=blue_drag,
        red_dragons=red_drag,
        blue_heralds=blue_herald,
        red_heralds=red_herald,
        blue_towers=blue_tower,
        red_towers=red_tower,
    )
    ally_drag = blue_drag if my_side == "blue" else red_drag
    enemy_drag = red_drag if my_side == "blue" else blue_drag
    ally_grubs = blue_grubs if my_side == "blue" else red_grubs

    return LiveSnapshot(
        game_time=game_time,
        scoreboard=sb,
        ally_alive=ally_alive or 5,
        enemy_alive=enemy_alive,
        enemy_max_respawn=enemy_max_respawn,
        ally_jungler_dead_sec=ally_jg_dead,
        enemy_jungler_dead_sec=enemy_jg_dead,
        ally_dragons=ally_drag,
        enemy_dragons=enemy_drag,
        ally_grubs=ally_grubs,
        ally_carry_lane=ally_carry_lane,
        ally_carry_kills=max(0, ally_carry_kills),
        my_current_gold=float(active.get("currentGold") or 0.0),
        enemy_has_heavy_heal=enemy_heal,
        enemy_has_heavy_tank=enemy_tank,
    )


OVERLAY_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>LoL Pacemaker Companion</title>
<style>
  body { margin: 0; background: transparent; font-family: system-ui, -apple-system, sans-serif; color: #F8FAFC; }
  .card {
    width: 310px; background: rgba(11, 17, 32, 0.88); border: 1.5px solid #38BDF8;
    border-radius: 12px; padding: 12px 14px; box-shadow: 0 8px 24px rgba(0,0,0,0.55);
  }
  .top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
  .win { font-size: 22px; font-weight: 800; color: #38BDF8; }
  .badge { font-size: 11px; padding: 2px 8px; border-radius: 999px; background: #1E293B; color: #E2E8F0; }
  .call { background: rgba(30,41,59,0.85); border-left: 3px solid #F59E0B; padding: 7px 9px; border-radius: 6px; margin-top: 6px; }
  .call-t { font-size: 12.5px; font-weight: 700; color: #FDE68A; }
  .call-d { font-size: 11px; color: #CBD5E1; margin-top: 2px; line-height: 1.35; }
  .hint { font-size: 10px; color: #94A3B8; margin-top: 8px; text-align: center; }
</style>
</head>
<body>
<div class="card">
  <div class="top">
    <span class="win" id="win">--%</span>
    <span class="badge" id="phase">Waiting for LoL...</span>
  </div>
  <div id="calls"></div>
  <div class="hint" id="hint"></div>
</div>
<script>
async function tick() {
  try {
    const r = await fetch('/state');
    const d = await r.json();
    if (!d.win_pct) return;
    document.getElementById('win').textContent = d.win_pct + '% · ' + d.state;
    document.getElementById('phase').textContent = d.phase;
    document.getElementById('hint').textContent = d.labels.hint_keys;
    const box = document.getElementById('calls');
    box.replaceChildren();
    for (const c of (d.calls || [])) {
      const wrap = document.createElement('div');
      wrap.className = 'call';
      const t = document.createElement('div');
      t.className = 'call-t';
      t.textContent = c.title;
      const dt = document.createElement('div');
      dt.className = 'call-d';
      dt.textContent = c.detail;
      wrap.append(t, dt);
      box.appendChild(wrap);
    }
  } catch (_) {}
}
setInterval(tick, 2000);
tick();
</script>
</body>
</html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="LoL Pacemaker zero-server companion")
    parser.add_argument("--lang", default=None, help="Language code (en, ko)")
    parser.add_argument("--port", type=int, default=8787, help="Local HUD port")
    args = parser.parse_args()

    predictor = LocalOnnxPredictor()
    tr = Translator(lang=args.lang)
    state_lock = threading.Lock()
    latest_state: dict[str, Any] = {"status": "waiting"}

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    def poller() -> None:
        while True:
            try:
                req = urllib.request.Request(RIOT_LOCAL_URL)
                with urllib.request.urlopen(req, context=ssl_ctx, timeout=1.5) as resp:
                    raw = json.loads(resp.read().decode("utf-8"))
                snap = parse_liveclient_data(raw)
                result = evaluate_snapshot(snap, predictor, tr)
                with state_lock:
                    latest_state.clear()
                    latest_state.update(result)
            except Exception:
                pass
            time.sleep(2.0)

    threading.Thread(target=poller, daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def _send_security_headers(self) -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'",
            )

        def do_GET(self) -> None:
            if self.path.startswith("/state"):
                with state_lock:
                    body = json.dumps(latest_state, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._send_security_headers()
                self.end_headers()
                self.wfile.write(body)
            else:
                body = OVERLAY_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self._send_security_headers()
                self.end_headers()
                self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"LoL Pacemaker ({tr.lang}) running locally at http://127.0.0.1:{args.port}/overlay")
    server.serve_forever()


if __name__ == "__main__":
    main()
