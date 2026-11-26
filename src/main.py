"""LoL Pacemaker entry point: companion overlay (default) or browser HUD (`--web`)."""
from __future__ import annotations

import argparse
import threading

from src.engine import Engine
from src.hud_state import TIERS, load_settings, save_settings
from src.i18n import Translator
from src.predictor import LocalOnnxPredictor
from src.updater import check_for_update


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="LoL Pacemaker — free, zero-server LoL companion")
    ap.add_argument("--lang", default=None, help="en | ko | ja | es | pt (default: saved choice, else OS locale)")
    ap.add_argument("--tier", default=None, choices=TIERS, help="your tier bucket for baselines: low=Iron-Gold, mid=Plat-Emerald, high=Diamond+")
    ap.add_argument("--web", action="store_true", help="serve the browser HUD instead of the overlay window")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--no-update", action="store_true", help="skip the model update check")
    ap.add_argument("--background", action="store_true", help="start hidden and auto-show only while a LoL match is active")
    args = ap.parse_args(argv)

    settings = load_settings()
    if args.lang:
        settings.lang = args.lang
    if args.tier:
        settings.tier = args.tier

    if not args.no_update:  # background; a new model is used from the next launch
        threading.Thread(target=check_for_update, daemon=True).start()

    engine = Engine(LocalOnnxPredictor(), Translator(lang=settings.lang), tier=settings.tier)
    if args.web:
        from src.app import serve_web_hud

        serve_web_hud(engine, args.port)
        return 0

    save_settings(settings)
    from src.overlay import Overlay  # imports tkinter only when the GUI is requested

    Overlay(engine, settings, background_mode=args.background).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
