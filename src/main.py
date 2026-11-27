"""LoL Pacemaker entry point: companion overlay (default) or browser HUD (`--web`)."""
from __future__ import annotations

import argparse
import socket
import threading

from src.contribute import ContributionRecorder
from src.engine import Engine
from src.hud_state import TIERS, load_settings, save_settings
from src.i18n import Translator
from src.predictor import LocalOnnxPredictor
from src.updater import check_for_update

INSTANCE_PORT = 47291


def acquire_single_instance(port: int = INSTANCE_PORT) -> socket.socket | None:
    """Bind 127.0.0.1:<port> as a single-instance lock; if already running, send WAKE and return None."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        srv.bind(("127.0.0.1", port))
        srv.listen(4)
        return srv
    except OSError:
        srv.close()
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5) as c:
                c.sendall(b"WAKE")
        except OSError:
            pass
        return None


def start_wake_listener(srv: socket.socket, on_wake: callable) -> None:
    """Background listener that invokes `on_wake` whenever a second desktop launch pings WAKE."""
    def _loop() -> None:
        while True:
            try:
                conn, _ = srv.accept()
                with conn:
                    data = conn.recv(16)
                    if b"WAKE" in data:
                        on_wake()
            except OSError:
                break

    threading.Thread(target=_loop, daemon=True).start()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="LoL Pacemaker — free, zero-server LoL companion")
    ap.add_argument("--lang", default=None, help="en | ko | ja | es | pt (default: saved choice, else English)")
    ap.add_argument("--tier", default=None, choices=TIERS, help="your tier bucket for baselines: low=Iron-Gold, mid=Plat-Emerald, high=Diamond+")
    ap.add_argument("--web", action="store_true", help="serve the browser HUD instead of the overlay window")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--no-update", action="store_true", help="skip the model update check")
    ap.add_argument("--background", action="store_true", help="silent start (login/installer): no 3-second waiting notice")
    args = ap.parse_args(argv)

    settings = load_settings()
    if args.lang:
        settings.lang = args.lang
    if args.tier:
        settings.tier = args.tier

    if not args.no_update:  # background; a new model is used from the next launch
        threading.Thread(target=check_for_update, daemon=True).start()

    current = {"settings": lambda: settings}  # swapped to the overlay's live settings below
    recorder = ContributionRecorder(  # AGENTS §14: does nothing unless the user opted in
        consented=lambda: current["settings"]().share_data,
        install_id=lambda: current["settings"]().install_id,
        lang=lambda: engine.tr.lang,
        tier=lambda: engine.tier,
    )
    engine = Engine(
        LocalOnnxPredictor(), Translator(lang=settings.lang or "en"), tier=settings.tier,
        on_poll=lambda raw, snap: recorder.observe(raw, snap.game_time, snap.game_mode, snap.scoreboard.to_feature_dict()),
    )
    if args.web:
        from src.app import serve_web_hud

        serve_web_hud(engine, args.port)
        return 0

    save_settings(settings)
    lock_sock = acquire_single_instance()
    if lock_sock is None:
        return 0  # already running; WAKE was sent so the existing app pops up its status/menu

    from src.overlay import Overlay  # imports tkinter only when the GUI is requested

    # AGENTS §17/§28: the HUD only shows during a match, however it was launched.
    # A manual desktop launch (no --background) shows a 5-second waiting notice so the user can open settings or quit.
    overlay = Overlay(engine, settings, background_mode=True, announce=not args.background)
    start_wake_listener(lock_sock, overlay.wake)
    current["settings"] = lambda: overlay.settings
    try:
        overlay.run()
    finally:
        lock_sock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
