"""Tk companion HUD (AGENTS §12, §17, §17-1): a PACE MAKER logo button plus separate in-game panels.

- The logo button is the hub: click it to open a small menu that turns each panel on/off
  (win chance / calls / briefing / tier check), plus DATA, AUTO, language and opacity.
- Every panel is its own borderless window: drag it anywhere, its position is saved.
- Nothing shows on the desktop or in the client lobby; the HUD appears only while a match runs.
- F8 click-through, F9 hide/show all panels (the logo button stays). There is no close button.
- First launch shows the consent window once (AGENTS §14).
"""
from __future__ import annotations

import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from typing import Any, Callable

from src.autostart import is_autostart_enabled, set_autostart
from src.contribute import CONSENT_VERSION, new_install_id, request_deletion
from src.engine import Engine
from src.hud_state import PANELS, HudSettings, save_settings
from src.icon_cache import IconCache

# LoL-harmonized dark navy + PACE MAKER orange palette (AGENTS §0 & §27)
BORDER = "#FF5A1F"
LINE = "#243244"
BG = "#0F1722"
SURFACE = "#152030"
SURFACE_PRI = "#122628"
PILL_BG = "#1B2838"
PILL_GREEN_BG = "#0F2F28"
PILL_RED_BG = "#3A1A24"
PILL_GOLD_BG = "#332914"
FG = "#F8FAFC"
FG_SUB = "#CBD5E1"
MUTED = "#94A3B8"
ACCENT = "#FF5A1F"
ACCENT_SOFT = "#FF7A45"
CYAN = "#22D3EE"
EMERALD = "#34D399"
CORAL = "#F87171"
GOLD = "#FACC15"
VIOLET = "#A78BFA"
BLUE_TEAM = "#60A5FA"
KEY_COLOR = "#010203"  # never used by a visible widget; keyed out on Windows for the round logo corners
BUBBLE_PNG = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "assets" / "branding" / "hud_bubble.png"
PRIVACY_URL = "https://wpalswpa.github.io/lol-pacemaker/privacy.html?lang={lang}"

# OS-native crisp sans + fixed-width tabular numbers (no copyrighted LoL fonts bundled)
UI_FONT = "Apple SD Gothic Neo" if sys.platform == "darwin" else ("Malgun Gothic" if sys.platform == "win32" else "Helvetica")
MONO_FONT = "Menlo" if sys.platform == "darwin" else ("Consolas" if sys.platform == "win32" else "Courier")

# Default layout: top-left corner, away from LoL's scoreboard (top centre), KDA/time (top right),
# ability bar (bottom centre), minimap (bottom right) and chat (bottom left). Unverified in a real game.
MARGIN, LOGO = 16, 64
PANEL_W = {"win": 276, "calls": 324, "brief": 324, "rank": 300, "items": 348, "timers": 220}
DEFAULT_POS = {
    "win": (MARGIN + LOGO + 8, MARGIN),
    "calls": (MARGIN, MARGIN + LOGO + 12),
    "brief": (MARGIN + 334, MARGIN + LOGO + 12),
    "rank": (MARGIN + LOGO + 8 + 286, MARGIN),
    "items": (MARGIN, MARGIN + LOGO + 210),
    "timers": (MARGIN + LOGO + 8 + 286, MARGIN + LOGO + 12),
}


def set_click_through(win: tk.Misc, enabled: bool) -> bool:
    """Best-effort OS-level mouse pass-through for one window (macOS: all app windows)."""
    try:
        if sys.platform == "win32":
            import ctypes

            hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
            GWL_EXSTYLE, WS_EX_LAYERED, WS_EX_TRANSPARENT = -20, 0x80000, 0x20
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style = (style | WS_EX_LAYERED | WS_EX_TRANSPARENT) if enabled else (style & ~WS_EX_TRANSPARENT)
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
            return True
        if sys.platform == "darwin":
            from AppKit import NSApp  # type: ignore[import-not-found]  # pyobjc, optional

            for w in NSApp.windows():
                w.setIgnoresMouseEvents_(enabled)
            return True
    except Exception:
        return False
    return False


def start_global_hotkeys(on_f8: Callable[[], None], on_f9: Callable[[], None]) -> bool:
    """Register F8/F9 globally via pynput. False if unavailable (missing lib / no permission)."""
    try:
        from pynput import keyboard  # type: ignore[import-not-found]

        listener = keyboard.GlobalHotKeys({"<f8>": on_f8, "<f9>": on_f9})
        listener.daemon = True
        listener.start()
        return True
    except Exception:
        return False


def _borderless(win: tk.Misc, alpha: float) -> None:
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    win.attributes("-alpha", alpha)


class Overlay:
    def __init__(
        self,
        engine: Engine,
        settings: HudSettings,
        on_settings: Callable[[HudSettings], None] = save_settings,
        background_mode: bool = True,
        announce: bool = False,
    ) -> None:
        self.engine, self.settings, self._on_settings = engine, settings, on_settings
        self._background_mode = background_mode
        self._announce_until = 0.0  # manual launch: show "waiting" for 3 s so the user knows it runs
        self._announce = announce
        self._autostart_on = is_autostart_enabled()
        self._in_game = False
        self._shown = False
        self._ct_ok = True
        self._hotkeys_global = False
        self._press = (0, 0)
        self._drag = (0, 0)
        self.icons = IconCache()

        self.root = tk.Tk()  # the root window IS the logo button
        self.root.title("PACE MAKER")
        _borderless(self.root, max(0.6, settings.opacity))
        self.root.geometry(f"+{settings.x if settings.x != 40 else MARGIN}+{settings.y if settings.y != 40 else MARGIN}")
        self._build_logo()
        self.panels: dict[str, tk.Toplevel] = {}
        self._build_panels()
        self.menu: tk.Toplevel | None = None

        for seq, fn in (("<F8>", self.toggle_click_through), ("<F9>", self.toggle_visible)):
            self.root.bind_all(seq, lambda _e, f=fn: f())
        self.root.withdraw()
        if announce:
            self._announce_until = time.monotonic() + 3.0

    # -- building -------------------------------------------------------------------------
    def _build_logo(self) -> None:
        key = "systemTransparent" if sys.platform == "darwin" else KEY_COLOR
        try:
            if sys.platform == "win32":
                self.root.wm_attributes("-transparentcolor", KEY_COLOR)
            elif sys.platform == "darwin":
                self.root.wm_attributes("-transparent", True)
        except tk.TclError:
            key = BORDER
        self.root.configure(bg=key)
        try:
            self._logo_img = tk.PhotoImage(file=str(BUBBLE_PNG))
            self.logo = tk.Label(self.root, image=self._logo_img, bg=key, bd=0, cursor="hand2")
        except tk.TclError:  # image missing: same mark as text
            self.logo = tk.Label(self.root, text="PACE\nMAKER", bg=ACCENT, fg="#FFFFFF",
                                 font=("Helvetica", 10, "bold"), padx=8, pady=6, cursor="hand2")
        self.logo.pack()
        self.logo.bind("<ButtonPress-1>", self._logo_press)
        self.logo.bind("<B1-Motion>", lambda e: self._move(self.root, e))
        self.logo.bind("<ButtonRelease-1>", self._logo_release)

    def _default_pos(self, name: str) -> tuple[int, int]:
        """DEFAULT_POS, but the second row starts below the taller of the logo and the win panel (AGENTS §21:
        the win panel is 74 px tall on macOS, so a fixed LOGO-based row overlapped it by 17 px)."""
        x, y = DEFAULT_POS[name]
        win = self.panels.get("win")
        if name in ("calls", "brief") and win is not None:
            win.update_idletasks()
            y = MARGIN + max(LOGO, win.winfo_reqheight()) + 10
        return x, y

    def _panel_window(self, name: str) -> tk.Toplevel:
        win = tk.Toplevel(self.root, bg=BORDER)
        _borderless(win, self.settings.opacity)
        x, y = self.settings.panels_pos.get(name, self._default_pos(name))
        win.geometry(f"+{x}+{y}")
        inner = tk.Frame(win, bg=BG)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Frame(inner, width=PANEL_W[name], height=1, bg=BG).pack()  # fixed width
        win.bind("<ButtonPress-1>", lambda e: self._grab(win, e))
        win.bind("<B1-Motion>", lambda e: self._move(win, e))
        win.bind("<ButtonRelease-1>", lambda _e, n=name: self._save_pos(n))
        win.withdraw()
        win.inner = inner  # type: ignore[attr-defined]
        return win

    def _build_panels(self) -> None:
        w = PANEL_W

        # 1. Win chance panel: big tabular %, state pill, live clock, gold/deaths pills, dual-color gauge bar
        p = self.panels["win"] = self._panel_window("win")
        top_row = tk.Frame(p.inner, bg=BG)
        top_row.pack(fill="x", padx=11, pady=(8, 3))
        self.win_lbl = tk.Label(top_row, text="--%", fg=FG, bg=BG, font=(MONO_FONT, 19, "bold"), anchor="w")
        self.win_lbl.pack(side="left")
        self.state_pill = tk.Label(top_row, text="", fg=EMERALD, bg=PILL_GREEN_BG, font=(UI_FONT, 9, "bold"), padx=7, pady=2)
        self.state_pill.pack(side="left", padx=(8, 0))
        self.clock_lbl = tk.Label(top_row, text="", fg=MUTED, bg=BG, font=(MONO_FONT, 9, "bold"), anchor="e")
        self.clock_lbl.pack(side="right")

        meta_row = tk.Frame(p.inner, bg=BG)
        meta_row.pack(fill="x", padx=11, pady=(0, 6))
        self.phase_lbl = tk.Label(meta_row, text="", fg=FG_SUB, bg=PILL_BG, font=(UI_FONT, 8, "bold"), padx=6, pady=1)
        self.phase_lbl.pack(side="left")
        self.gold_pill = tk.Label(meta_row, text="", fg=GOLD, bg=PILL_GOLD_BG, font=(MONO_FONT, 8, "bold"), padx=6, pady=1)
        self.gold_pill.pack(side="left", padx=(5, 0))
        self.deaths_pill = tk.Label(meta_row, text="", fg=FG_SUB, bg=PILL_BG, font=(MONO_FONT, 8), padx=6, pady=1)
        self.deaths_pill.pack(side="right")

        self.win_gauge = tk.Canvas(p.inner, width=w["win"] - 22, height=7, bg=BG, highlightthickness=0)
        self.win_gauge.pack(padx=11, pady=(0, 9))
        self._draw_win_gauge(50.0)

        # 2. Calls panel: numbered circular priority badges (1/2/3) + crisp bold title & muted detail
        p = self.panels["calls"] = self._panel_window("calls")
        self.call_cards: list[tuple[tk.Frame, tk.Label, tk.Label]] = []
        box = tk.Frame(p.inner, bg=BG)
        box.pack(fill="x", padx=8, pady=8)
        badge_cols = (GOLD, CYAN, VIOLET)
        for idx in range(3):
            c_bg = SURFACE_PRI if idx == 0 else SURFACE
            accent_c = badge_cols[idx]
            outer = tk.Frame(box, bg=c_bg, highlightbackground=LINE, highlightthickness=1)
            tk.Frame(outer, bg=accent_c, width=3).pack(side="left", fill="y")
            badge_cv = tk.Canvas(outer, width=22, height=22, bg=c_bg, highlightthickness=0)
            badge_cv.pack(side="left", padx=(7, 2), pady=7, anchor="n")
            badge_cv.create_oval(2, 2, 20, 20, fill=accent_c, outline="")
            badge_cv.create_text(11, 11, text=str(idx + 1), fill="#0B0F16", font=(MONO_FONT, 9, "bold"))
            body = tk.Frame(outer, bg=c_bg)
            body.pack(side="left", fill="both", expand=True, padx=(4, 8), pady=6)
            t = tk.Label(body, text="", fg=accent_c, bg=c_bg, font=(UI_FONT, 10, "bold"),
                         anchor="w", justify="left", wraplength=w["calls"] - 64)
            d = tk.Label(body, text="", fg=FG_SUB, bg=c_bg, font=(UI_FONT, 9), anchor="w", justify="left",
                         wraplength=w["calls"] - 64)
            t.pack(fill="x")
            d.pack(fill="x", pady=(2, 0))
            self.call_cards.append((outer, t, d))

        # 3. Briefing panel
        p = self.panels["brief"] = self._panel_window("brief")
        bh = tk.Frame(p.inner, bg=BG)
        bh.pack(fill="x", padx=11, pady=(8, 2))
        tk.Label(bh, text="●", fg=ACCENT, bg=BG, font=(UI_FONT, 8)).pack(side="left")
        self.brief_hdr = tk.Label(bh, text="", fg=MUTED, bg=BG, font=(UI_FONT, 8, "bold"))
        self.brief_hdr.pack(side="left", padx=(4, 0))
        self.brief_lbl = tk.Label(p.inner, text="", fg=FG, bg=BG, font=(UI_FONT, 9), anchor="w",
                                  justify="left", wraplength=w["brief"] - 22)
        self.brief_lbl.pack(fill="x", padx=11, pady=(2, 9))

        # 4. Tier check panel
        p = self.panels["rank"] = self._panel_window("rank")
        rh = tk.Frame(p.inner, bg=BG)
        rh.pack(fill="x", padx=11, pady=(8, 2))
        tk.Label(rh, text="◆", fg=EMERALD, bg=BG, font=(UI_FONT, 8)).pack(side="left")
        self.rank_hdr = tk.Label(rh, text="", fg=MUTED, bg=BG, font=(UI_FONT, 8, "bold"))
        self.rank_hdr.pack(side="left", padx=(4, 0))
        self.rank_lbl = tk.Label(p.inner, text="", fg=EMERALD, bg=BG, font=(UI_FONT, 9, "bold"), anchor="w",
                                 justify="left", wraplength=w["rank"] - 22)
        self.rank_lbl.pack(fill="x", padx=11, pady=(2, 9))

        # 5. Item gold comparison panel (AGENTS §27: public 2999 allPlayers items + Data Dragon champ icons)
        p = self.panels["items"] = self._panel_window("items")
        ih = tk.Frame(p.inner, bg=BG)
        ih.pack(fill="x", padx=10, pady=(8, 3))
        coin = tk.Canvas(ih, width=16, height=16, bg=BG, highlightthickness=0)
        coin.pack(side="left")
        coin.create_oval(1, 1, 15, 15, fill=GOLD, outline="")
        coin.create_text(8, 8, text="G", fill="#0B0F16", font=(MONO_FONT, 8, "bold"))
        self.items_title = tk.Label(ih, text="", fg=FG, bg=BG, font=(UI_FONT, 9, "bold"))
        self.items_title.pack(side="left", padx=(5, 0))
        self.items_totals = tk.Label(ih, text="", fg=MUTED, bg=BG, font=(MONO_FONT, 8, "bold"))
        self.items_totals.pack(side="right")
        tk.Frame(p.inner, bg=LINE, height=1).pack(fill="x", padx=10, pady=(2, 4))

        self.item_rows: list[dict[str, Any]] = []
        rows_box = tk.Frame(p.inner, bg=BG)
        rows_box.pack(fill="x", padx=8, pady=(0, 8))
        for lane_tag in ("TOP", "JGL", "MID", "ADC", "SUP"):
            rf = tk.Frame(rows_box, bg=BG)
            rf.pack(fill="x", pady=1)
            role_l = tk.Label(rf, text=lane_tag, fg=MUTED, bg=BG, font=(MONO_FONT, 8, "bold"), width=3, anchor="w")
            role_l.pack(side="left", padx=(2, 3))
            b_ic = tk.Label(rf, text="", fg=FG, bg=PILL_BG, font=(MONO_FONT, 7, "bold"), width=2)
            b_ic.pack(side="left")
            b_nm = tk.Label(rf, text="-", fg=FG, bg=BG, font=(UI_FONT, 8), width=7, anchor="w")
            b_nm.pack(side="left", padx=(3, 2))
            b_g = tk.Label(rf, text="0G", fg=MUTED, bg=BG, font=(MONO_FONT, 8), width=6, anchor="e")
            b_g.pack(side="left")
            r_ic = tk.Label(rf, text="", fg=FG, bg=PILL_BG, font=(MONO_FONT, 7, "bold"), width=2)
            r_ic.pack(side="left", padx=(6, 0))
            r_nm = tk.Label(rf, text="-", fg=FG, bg=BG, font=(UI_FONT, 8), width=7, anchor="w")
            r_nm.pack(side="left", padx=(3, 2))
            diff_l = tk.Label(rf, text="+0G", fg=EMERALD, bg=BG, font=(MONO_FONT, 8, "bold"), width=6, anchor="e")
            diff_l.pack(side="right", padx=(2, 2))
            self.item_rows.append({"frame": rf, "b_ic": b_ic, "b_nm": b_nm, "b_g": b_g, "r_ic": r_ic, "r_nm": r_nm, "diff": diff_l})

        # 6. Objective respawn timers panel (AGENTS §27: public 2999 DragonKill/BaronKill events only)
        p = self.panels["timers"] = self._panel_window("timers")
        th = tk.Frame(p.inner, bg=BG)
        th.pack(fill="x", padx=10, pady=(8, 4))
        self.timers_title = tk.Label(th, text="", fg=MUTED, bg=BG, font=(UI_FONT, 8, "bold"), anchor="w")
        self.timers_title.pack(side="left")
        t_row = tk.Frame(p.inner, bg=BG)
        t_row.pack(fill="x", padx=10, pady=(0, 9))

        # Dragon emblem + timer
        d_box = tk.Frame(t_row, bg=SURFACE, padx=7, pady=5)
        d_box.pack(side="left", fill="x", expand=True, padx=(0, 4))
        d_cv = tk.Canvas(d_box, width=16, height=16, bg=SURFACE, highlightthickness=0)
        d_cv.pack(side="left")
        d_cv.create_polygon(8, 1, 15, 8, 8, 15, 1, 8, fill=CYAN, outline="")
        self.dragon_lbl = tk.Label(d_box, text="05:00", fg=FG, bg=SURFACE, font=(MONO_FONT, 10, "bold"))
        self.dragon_lbl.pack(side="right")

        # Baron emblem + timer
        b_box = tk.Frame(t_row, bg=SURFACE, padx=7, pady=5)
        b_box.pack(side="left", fill="x", expand=True, padx=(4, 0))
        b_cv = tk.Canvas(b_box, width=16, height=16, bg=SURFACE, highlightthickness=0)
        b_cv.pack(side="left")
        b_cv.create_polygon(2, 13, 4, 4, 8, 9, 12, 4, 14, 13, fill=VIOLET, outline="")
        self.baron_lbl = tk.Label(b_box, text="20:00", fg=FG, bg=SURFACE, font=(MONO_FONT, 10, "bold"))
        self.baron_lbl.pack(side="right")

    def _draw_win_gauge(self, pct: float) -> None:
        cv = self.win_gauge
        cv.delete("all")
        gw = PANEL_W["win"] - 22
        ratio = max(0.05, min(0.95, pct / 100.0))
        fill_w = int(gw * ratio)
        bar_c = CYAN if pct >= 55 else (CORAL if pct <= 45 else ACCENT)
        cv.create_rectangle(0, 1, gw, 6, fill="#3D1C26", outline="")
        cv.create_rectangle(0, 1, fill_w, 6, fill=bar_c, outline="")
        cv.create_line(gw // 2, 0, gw // 2, 7, fill="#64748B", width=1)

    def _set_champ_icon(self, lbl: tk.Label, key: str, name: str, team_color: str) -> None:
        img = self.icons.get_tk_image("champ", key, size=18, on_ready=lambda: self.root.after(0, self._render_once)) if key else None
        if img is not None:
            lbl.config(image=img, text="", width=18, height=18, bg=BG)
            lbl._img_ref = img  # type: ignore[attr-defined]
        else:
            lbl.config(image="", text=(name[:1] or "?").upper(), fg=team_color, bg=PILL_BG, width=2, height=1)

    # -- the logo menu ----------------------------------------------------------------------
    def _logo_press(self, e: Any) -> None:
        self._press = (e.x_root, e.y_root)
        self._grab(self.root, e)

    def _logo_release(self, e: Any) -> None:
        if abs(e.x_root - self._press[0]) < 5 and abs(e.y_root - self._press[1]) < 5:
            self._toggle_menu()  # a click, not a drag
        elif not self.settings.locked:
            self.settings = self.settings.moved(self.root.winfo_x(), self.root.winfo_y())
            self._on_settings(self.settings)

    def _toggle_menu(self) -> None:
        if self.menu is not None:
            self.menu.destroy()
            self.menu = None
            self._sync_windows()
            return
        tr = self.engine.tr
        m = self.menu = tk.Toplevel(self.root, bg=BORDER)
        _borderless(m, 0.97)
        m.geometry(f"+{self.root.winfo_x()}+{self.root.winfo_y() + self.root.winfo_height() + 6}")
        inner = tk.Frame(m, bg=BG)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Label(inner, text=tr.t("hud_menu_title"), fg=MUTED, bg=BG, font=("Helvetica", 9, "bold"),
                 anchor="w").pack(fill="x", padx=12, pady=(10, 4))

        def row(text: str, on: bool, action: Callable[[], None]) -> None:
            lbl = tk.Label(inner, text=("✓  " if on else "     ") + text, fg=FG if on else MUTED, bg=BG,
                           font=("Helvetica", 11, "bold" if on else "normal"), anchor="w", padx=12, pady=4, cursor="hand2")
            lbl.pack(fill="x")
            lbl.bind("<Button-1>", lambda _e: (action(), self._toggle_menu(), self._toggle_menu()))

        for name in PANELS:
            row(tr.t(f"hud_panel_{name}"), self.settings.panels_on.get(name, False), lambda n=name: self._toggle_panel(n))
        tk.Frame(inner, bg="#232A38", height=1).pack(fill="x", padx=12, pady=6)
        row(tr.t("hud_menu_data"), self.settings.share_data, lambda: self._set_sharing(not self.settings.share_data))
        row(tr.t("hud_menu_auto"), self._autostart_on, self._toggle_autostart)
        row(f"{tr.t('hud_menu_lang')}: {tr.lang.upper()}", True, self._cycle_lang)
        row(tr.t("hud_menu_lock"), self.settings.locked, self._toggle_locked)
        row(tr.t("hud_menu_reset"), False, self.reset_layout)
        op = tk.Frame(inner, bg=BG)
        op.pack(fill="x", padx=12, pady=(4, 4))
        tk.Label(op, text=f"{tr.t('hud_menu_opacity')} {int(self.settings.opacity * 100)}%", fg=FG_SUB, bg=BG,
                 font=("Helvetica", 10)).pack(side="left")
        for sym, d in (("+", 1), ("−", -1)):
            b = tk.Label(op, text=sym, fg=FG, bg=PILL_BG, font=("Helvetica", 11, "bold"), padx=8, cursor="hand2")
            b.pack(side="right", padx=(4, 0))
            b.bind("<Button-1>", lambda _e, dd=d: (self.step_opacity(dd), self._toggle_menu(), self._toggle_menu()))
        tk.Label(inner, text=tr.t("hud_hint_keys"), fg=MUTED, bg=BG, font=("Helvetica", 8), wraplength=250,
                 justify="left", anchor="w").pack(fill="x", padx=12, pady=(4, 10))
        self._show(m)
        self._sync_windows()

    # -- actions --------------------------------------------------------------------------
    def _grab(self, win: tk.Misc, e: Any) -> None:
        self._drag = (e.x_root - win.winfo_x(), e.y_root - win.winfo_y())

    def _move(self, win: tk.Misc, e: Any) -> None:
        if self.settings.locked:  # AGENTS §21: position lock
            return
        win.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def _save_pos(self, name: str) -> None:
        if self.settings.locked:
            return
        w = self.panels[name]
        self.settings = self.settings.with_panel_pos(name, w.winfo_x(), w.winfo_y())
        self._on_settings(self.settings)

    def _toggle_locked(self) -> None:
        self.settings = self.settings.toggled_locked()
        self._on_settings(self.settings)

    def reset_layout(self) -> None:
        """AGENTS §21: put the logo and every panel back to the default layout and save it."""
        self.settings = self.settings.with_layout_reset()
        self.root.geometry(f"+{MARGIN}+{MARGIN}")
        for name, w in self.panels.items():
            x, y = self._default_pos(name)
            w.geometry(f"+{x}+{y}")
        self._on_settings(self.settings)

    def _toggle_panel(self, name: str) -> None:
        self.settings = self.settings.with_panel_toggled(name)
        self._on_settings(self.settings)
        self._sync_windows()

    def _set_sharing(self, share: bool) -> None:
        """Opt in (new random id) or out (ask the server to delete, then forget the id)."""
        old_id = self.settings.install_id
        if share:
            self.settings = self.settings.with_consent(CONSENT_VERSION, True, old_id or new_install_id())
        else:
            if old_id:
                threading.Thread(target=request_deletion, args=(old_id,), daemon=True).start()
            self.settings = self.settings.with_consent(CONSENT_VERSION, False, "")
        self._on_settings(self.settings)

    def _toggle_autostart(self) -> None:
        target = not self._autostart_on
        if set_autostart(target):
            self._autostart_on = target

    def _cycle_lang(self) -> None:
        self.settings = self.settings.next_lang(self.engine.tr.lang)
        self.engine.set_lang(self.settings.lang or "en")
        self._on_settings(self.settings)

    def step_opacity(self, direction: int) -> None:
        self.settings = self.settings.with_opacity_step(direction)
        for w in self.panels.values():
            w.attributes("-alpha", self.settings.opacity)
        self._on_settings(self.settings)

    def toggle_click_through(self) -> None:
        self.root.after(0, self._toggle_ct)

    def _toggle_ct(self) -> None:
        self.settings = self.settings.toggled_click_through()
        targets = [self.root] if sys.platform == "darwin" else list(self.panels.values())
        self._ct_ok = all([set_click_through(w, self.settings.click_through) for w in targets])
        self._on_settings(self.settings)

    def toggle_visible(self) -> None:
        """F9: hide/show every panel; the logo button stays so nothing is lost."""
        self.root.after(0, self._toggle_all)

    def _toggle_all(self) -> None:
        self.settings = self.settings.toggled_collapsed()
        self._on_settings(self.settings)
        self._sync_windows()

    # -- consent (AGENTS §14) ---------------------------------------------------------------
    def _ask_consent(self) -> None:
        """One-time consent window. Share is OFF by default; autostart is ON by default."""
        tr = self.engine.tr
        win = tk.Toplevel(self.root)
        win.title(f"PACE MAKER - {tr.t('consent_title')}")
        win.attributes("-topmost", True)
        win.configure(bg=BG, padx=18, pady=16)
        win.resizable(False, False)
        wrap = 420
        tk.Label(win, text=tr.t("consent_title"), fg=FG, bg=BG, font=("Helvetica", 14, "bold")).pack(anchor="w")
        tk.Label(win, text=tr.t("consent_body"), fg=FG_SUB, bg=BG, wraplength=wrap, justify="left").pack(anchor="w", pady=(6, 10))
        share = tk.BooleanVar(value=False)
        auto = tk.BooleanVar(value=True)
        for var, key in ((share, "consent_share"), (auto, "consent_autostart")):
            tk.Checkbutton(win, text=tr.t(key), variable=var, fg=FG, bg=BG, selectcolor=SURFACE,
                           activebackground=BG, activeforeground=FG, wraplength=wrap, justify="left").pack(anchor="w")
            if key == "consent_share":
                tk.Label(win, text=tr.t("consent_share_detail"), fg=MUTED, bg=BG, wraplength=wrap - 24,
                         justify="left", font=("Helvetica", 9)).pack(anchor="w", padx=(24, 0), pady=(0, 8))
        link = tk.Label(win, text=tr.t("consent_privacy"), fg=ACCENT_SOFT, bg=BG, cursor="hand2", font=("Helvetica", 10, "underline"))
        link.pack(anchor="w", pady=(8, 4))
        link.bind("<Button-1>", lambda _e: __import__("webbrowser").open(PRIVACY_URL.format(lang=tr.lang)))
        tk.Label(win, text=tr.t("consent_ready"), fg=EMERALD, bg=BG, wraplength=wrap, justify="left").pack(anchor="w", pady=(4, 0))

        def done() -> None:
            self._set_sharing(bool(share.get()))
            if set_autostart(bool(auto.get())):
                self._autostart_on = bool(auto.get())
            win.destroy()

        # A Label, not tk.Button: macOS Tk ignores Button bg, which rendered a grey button.
        ok = tk.Label(win, text=tr.t("consent_ok"), bg=ACCENT, fg="#FFFFFF", padx=22, pady=7,
                      font=("Helvetica", 11, "bold"), cursor="hand2")
        ok.pack(anchor="e", pady=(12, 0))
        ok.bind("<Button-1>", lambda _e: done())
        win.protocol("WM_DELETE_WINDOW", done)  # closing = defaults (share off), never a silent opt-in
        # The root is withdrawn outside a match; a child of a withdrawn root stays unmapped on macOS,
        # so map this window explicitly and bring it forward (seen 2026-11-26).
        win.deiconify()
        win.lift()
        win.focus_force()

    # -- render ---------------------------------------------------------------------------
    def _show(self, win: tk.Misc) -> None:
        win.deiconify()
        if sys.platform == "darwin":  # macOS Tk restores the title bar after withdraw/deiconify
            win.overrideredirect(False)
            win.overrideredirect(True)
        win.attributes("-topmost", True)

    def _sync_windows(self) -> None:
        """Show the logo during a match (or the 3-second launch notice); panels by their own switches."""
        announcing = time.monotonic() < self._announce_until
        show = self._in_game or announcing
        if show and not self._shown:
            self._show(self.root)
        elif not show and self._shown:
            self.root.withdraw()
            if self.menu is not None:
                self.menu.destroy()
                self.menu = None
        self._shown = show
        for name, w in self.panels.items():
            # panels step aside while the menu is open: macOS cannot reorder topmost windows (seen 2026-11-26)
            want = show and self.menu is None and not self.settings.collapsed and (
                self.settings.panels_on.get(name, False) if self._in_game else name == "win")
            mapped = bool(w.winfo_ismapped())
            if want and not mapped:
                self._show(w)
            elif not want and mapped:
                w.withdraw()

    def _render_once(self) -> None:
        st = self.engine.snapshot()
        tr = self.engine.tr
        self._in_game = "win_pct" in st
        if not self._in_game:
            self.win_lbl.config(text="PACE MAKER", fg=ACCENT)
            self.state_pill.config(text="", bg=BG)
            self.clock_lbl.config(text="")
            self.phase_lbl.config(text=tr.t("consent_ready"), bg=BG)
            self.gold_pill.config(text="", bg=BG)
            self.deaths_pill.config(text="", bg=BG)
            self._draw_win_gauge(50.0)
        else:
            pct = float(st.get("win_pct", 50.0))
            self.win_lbl.config(text=f"{pct:.1f}%", fg=FG)
            good = pct >= 52.0
            bad = pct <= 48.0
            self.state_pill.config(
                text=str(st.get("state", "")),
                fg=EMERALD if good else (CORAL if bad else GOLD),
                bg=PILL_GREEN_BG if good else (PILL_RED_BG if bad else PILL_GOLD_BG),
            )
            self.phase_lbl.config(text=str(st.get("phase", "")), bg=PILL_BG)
            self._draw_win_gauge(pct)

            ex = st.get("hud_extras") or {}
            gsec = int(ex.get("game_time_sec", 0))
            self.clock_lbl.config(text=f"{gsec // 60:02d}:{gsec % 60:02d}" if gsec > 0 else "")
            gdiff = int(ex.get("team_item_gold_diff", 0))
            self.gold_pill.config(
                text=f"{'+' if gdiff >= 0 else ''}{gdiff:,}G",
                fg=EMERALD if gdiff >= 0 else CORAL,
                bg=PILL_GREEN_BG if gdiff >= 0 else PILL_RED_BG,
            )
            ad, ed = int(ex.get("ally_dead", 0)), int(ex.get("enemy_dead", 0))
            self.deaths_pill.config(text=f"☠ {ad}:{ed}", bg=PILL_BG)

            self.brief_hdr.config(text=tr.t("hud_panel_brief"))
            self.brief_lbl.config(text=st.get("smart_briefing", ""))
            self.rank_hdr.config(text=tr.t("hud_panel_rank"))
            self.rank_lbl.config(text=st.get("rank_summary", ""))

            calls = st.get("calls", [])
            for i, (outer, t, d) in enumerate(self.call_cards):
                if i < len(calls):
                    t.config(text=calls[i]["title"])
                    d.config(text=calls[i]["detail"])
                    outer.pack(fill="x", pady=(0, 6))
                else:
                    outer.pack_forget()

            # Item gold comparison panel
            self.items_title.config(text=tr.t("hud_panel_items"))
            cmp_data = ex.get("item_compare") or {}
            bt, rt = int(cmp_data.get("blue_total", 0)), int(cmp_data.get("red_total", 0))
            self.items_totals.config(text=f"{tr.t('hud_team_blue')} {bt:,}G  vs  {rt:,}G")
            for idx, row_w in enumerate(self.item_rows):
                rows_d = cmp_data.get("rows") or []
                if idx < len(rows_d):
                    rd = rows_d[idx]
                    b, r = rd["blue"], rd["red"]
                    self._set_champ_icon(row_w["b_ic"], b.get("key", ""), b.get("name", ""), BLUE_TEAM)
                    self._set_champ_icon(row_w["r_ic"], r.get("key", ""), r.get("name", ""), CORAL)
                    row_w["b_nm"].config(text=str(b.get("name", "-"))[:7])
                    row_w["b_g"].config(text=f"{int(b.get('gold', 0)):,}G")
                    row_w["r_nm"].config(text=str(r.get("name", "-"))[:7])
                    df = int(rd.get("diff", 0))
                    row_w["diff"].config(text=f"{'+' if df >= 0 else ''}{df:,}G", fg=EMERALD if df >= 0 else CORAL)
                    hl = SURFACE if (b.get("is_me") or r.get("is_me")) else BG
                    row_w["frame"].config(bg=hl)

            # Objective timers panel
            self.timers_title.config(text=tr.t("hud_panel_timers"))
            tm = ex.get("obj_timers") or {}
            ds, bs = int(tm.get("dragon_sec", 0)), int(tm.get("baron_sec", 0))
            self.dragon_lbl.config(
                text=tr.t("hud_live_ready") if ds == 0 else f"{ds // 60:02d}:{ds % 60:02d}",
                fg=EMERALD if ds == 0 else FG,
            )
            self.baron_lbl.config(
                text=tr.t("hud_live_ready") if bs == 0 else f"{bs // 60:02d}:{bs % 60:02d}",
                fg=EMERALD if bs == 0 else FG,
            )
        self._sync_windows()

    def refresh_once(self) -> None:
        self._render_once()

    def refresh(self) -> None:
        self._render_once()
        self.root.after(1000, self.refresh)

    def run(self) -> None:
        self._hotkeys_global = start_global_hotkeys(self.toggle_click_through, self.toggle_visible)
        stop = threading.Event()
        threading.Thread(target=self.engine.run, args=(stop,), daemon=True).start()
        self.refresh()
        if self.settings.consent_version < CONSENT_VERSION:
            self._ask_consent()
        try:
            self.root.mainloop()
        finally:
            stop.set()
