"""Zero-server local bridge + lightweight companion HUD HTTP server.

Polls Riot's official local Live Client Data API (127.0.0.1:2999) every 2s,
runs ONNX + shotcaller locally in-process, and serves a compact companion HUD
at http://127.0.0.1:<port>/overlay with zero cloud dependency.
"""
from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from src.engine import Engine, parse_liveclient_data  # noqa: F401  (re-exported for tests/tools)
from src.i18n import Translator
from src.predictor import LocalOnnxPredictor

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


def make_handler(engine: Engine) -> type[BaseHTTPRequestHandler]:
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
                body = json.dumps(engine.snapshot(), ensure_ascii=False).encode("utf-8")
                ctype = "application/json; charset=utf-8"
            else:
                body = OVERLAY_HTML.encode("utf-8")
                ctype = "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self._send_security_headers()
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler


def serve_web_hud(engine: Engine, port: int) -> None:
    """Browser HUD (secondary view). Binds 127.0.0.1 only."""
    stop = threading.Event()
    threading.Thread(target=engine.run, args=(stop,), daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(engine))
    print(f"LoL Pacemaker ({engine.tr.lang}) browser HUD at http://127.0.0.1:{port}/overlay")
    try:
        server.serve_forever()
    finally:
        stop.set()


def main() -> None:
    parser = argparse.ArgumentParser(description="LoL Pacemaker browser HUD (use `python -m src.main` for the overlay)")
    parser.add_argument("--lang", default=None)
    parser.add_argument("--tier", default="mid", choices=["low", "mid", "high"])
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    serve_web_hud(Engine(LocalOnnxPredictor(), Translator(lang=args.lang), tier=args.tier), args.port)


if __name__ == "__main__":
    main()
