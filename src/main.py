"""LoL Pacemaker entry point: companion overlay (default) or browser HUD (`--web`)."""
from __future__ import annotations

import argparse
import threading

from src.contribute import ContributionRecorder
from src.engine import Engine
from src.hud_state import TIERS, load_settings, save_settings
from src.i18n import Translator
from src.predictor import LocalOnnxPredictor
from src.updater import check_for_update


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
    from src.overlay import Overlay  # imports tkinter only when the GUI is requested

    # AGENTS §17: the HUD only shows during a match, however it was launched.
    # A manual launch (no --background) shows a 3-second "waiting" notice first.
    overlay = Overlay(engine, settings, background_mode=True, announce=not args.background)
    current["settings"] = lambda: overlay.settings
    overlay.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
