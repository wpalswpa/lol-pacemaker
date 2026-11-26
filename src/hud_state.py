"""HUD settings and window state: pure logic (no GUI imports) so it is fully testable."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from src.updater import home_dir

LANGS = ("en", "ko", "ja", "es", "pt")
TIERS = ("low", "mid", "high")
OPACITY_MIN, OPACITY_MAX, OPACITY_STEP = 0.3, 1.0, 0.1
HUD_WIDTH = 340


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
        )

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
