"""HUD settings and window state: pure logic (no GUI imports) so it is fully testable."""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from src.updater import home_dir

LANGS = ("en", "ko", "ja", "es", "pt")
TIERS = ("low", "mid", "high")
OPACITY_MIN, OPACITY_MAX, OPACITY_STEP = 0.3, 1.0, 0.1
HUD_WIDTH = 340
# AGENTS §17-1 / §27: separate in-game panels; optional panels start off to keep the screen clear
PANELS = ("win", "calls", "brief", "rank", "items", "timers")
PANEL_DEFAULT_ON = {"win": True, "calls": True, "brief": False, "rank": False, "items": False, "timers": False}
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def settings_path() -> Path:
    return home_dir() / "settings.json"


@dataclass
class HudSettings:
    x: int = 40
    y: int = 40
    opacity: float = 0.9
    lang: str | None = None  # None = follow OS locale
    tier: str = "mid"
    click_through: bool = False
    visible: bool = True
    consent_version: int = 0   # AGENTS §14: 0 = never asked; < CONSENT_VERSION -> ask again
    share_data: bool = False   # opt-in anonymous game-stat contribution (default off)
    install_id: str = ""       # random UUID4 created only when the user opts in
    collapsed: bool = False    # AGENTS §17-1: F9 hides every panel (the logo button stays)
    panels_on: dict = field(default_factory=lambda: dict(PANEL_DEFAULT_ON))
    panels_pos: dict = field(default_factory=dict)  # name -> [x, y]; missing = default top-left layout
    locked: bool = False       # AGENTS §21: True = dragging does not move the logo or panels

    def sanitized(self, screen_w: int = 3840, screen_h: int = 2160) -> "HudSettings":
        """Clamp every field to a safe range (a corrupt/hand-edited file must not hide the HUD off-screen)."""
        def _int(v: object, d: int) -> int:
            try:
                return int(v)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return d

        try:
            op = float(self.opacity)
        except (TypeError, ValueError):
            op = 0.9
        return HudSettings(
            x=max(0, min(screen_w - 60, _int(self.x, 40))),
            y=max(0, min(screen_h - 60, _int(self.y, 40))),
            opacity=round(max(OPACITY_MIN, min(OPACITY_MAX, op)), 2),
            lang=self.lang if self.lang in LANGS else None,
            tier=self.tier if self.tier in TIERS else "mid",
            click_through=bool(self.click_through),
            visible=True,  # never start hidden: F9 state is per session
            consent_version=max(0, _int(self.consent_version, 0)),
            share_data=bool(self.share_data) and _UUID.match(str(self.install_id or "")) is not None,
            install_id=str(self.install_id) if _UUID.match(str(self.install_id or "")) else "",
            collapsed=bool(self.collapsed),
            panels_on={k: bool((self.panels_on or {}).get(k, PANEL_DEFAULT_ON[k])) for k in PANELS},
            panels_pos={k: [max(0, min(screen_w - 60, _int(v[0], 0))), max(0, min(screen_h - 40, _int(v[1], 0)))]
                        for k, v in (self.panels_pos or {}).items()
                        if k in PANELS and isinstance(v, (list, tuple)) and len(v) == 2},
            locked=bool(self.locked),
        )

    def with_panel_toggled(self, name: str) -> "HudSettings":
        on = {**self.panels_on, name: not self.panels_on.get(name, PANEL_DEFAULT_ON.get(name, False))}
        return HudSettings(**{**asdict(self), "panels_on": on})

    def with_panel_pos(self, name: str, x: int, y: int) -> "HudSettings":
        return HudSettings(**{**asdict(self), "panels_pos": {**self.panels_pos, name: [int(x), int(y)]}})

    def toggled_locked(self) -> "HudSettings":
        return HudSettings(**{**asdict(self), "locked": not self.locked})

    def with_layout_reset(self) -> "HudSettings":
        """AGENTS §21: logo and every panel back to the default top-left layout (other settings kept)."""
        default = HudSettings()
        return HudSettings(**{**asdict(self), "x": default.x, "y": default.y, "panels_pos": {}})

    def toggled_collapsed(self) -> "HudSettings":
        return HudSettings(**{**asdict(self), "collapsed": not self.collapsed})

    def with_consent(self, version: int, share: bool, install_id: str) -> "HudSettings":
        return HudSettings(**{**asdict(self), "consent_version": version, "share_data": share, "install_id": install_id})

    def with_opacity_step(self, direction: int) -> "HudSettings":
        new = round(max(OPACITY_MIN, min(OPACITY_MAX, self.opacity + direction * OPACITY_STEP)), 2)
        return HudSettings(**{**asdict(self), "opacity": new})

    def toggled_click_through(self) -> "HudSettings":
        return HudSettings(**{**asdict(self), "click_through": not self.click_through})

    def toggled_visible(self) -> "HudSettings":
        return HudSettings(**{**asdict(self), "visible": not self.visible})

    def next_lang(self, current: str) -> "HudSettings":
        nxt = LANGS[(LANGS.index(current) + 1) % len(LANGS)] if current in LANGS else LANGS[0]
        return HudSettings(**{**asdict(self), "lang": nxt})

    def moved(self, x: int, y: int) -> "HudSettings":
        return HudSettings(**{**asdict(self), "x": x, "y": y})


def load_settings(path: Path | None = None, screen_w: int = 3840, screen_h: int = 2160) -> HudSettings:
    p = path or settings_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        known = {k: v for k, v in data.items() if k in HudSettings.__dataclass_fields__}
        return HudSettings(**known).sanitized(screen_w, screen_h)
    except (OSError, ValueError, TypeError, AttributeError):
        return HudSettings().sanitized(screen_w, screen_h)


def save_settings(s: HudSettings, path: Path | None = None) -> None:
    p = path or settings_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(s)), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass  # settings are a convenience; never crash the HUD over them
