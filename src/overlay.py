"""Tk companion HUD: PACE MAKER dark orange-black card overlay (thin view over `hud_state` + `engine`).

Drag = move · F8 click-through · F9 hide/show · [ ] opacity · AUTO chip toggles OS login autostart ·
language chip cycles en/ko/ja/es/pt.
When started with `--background`, stays hidden until a LoL match starts and hides again when it ends.
"""
from __future__ import annotations

import sys
import threading
import tkinter as tk
from typing import Any, Callable

from src.autostart import is_autostart_enabled, set_autostart
from src.engine import Engine
from src.hud_state import HUD_WIDTH, HudSettings, save_settings

# PACE MAKER dark orange-black palette
BORDER = "#FF5A1F"
BG = "#0E1117"
SURFACE = "#151A24"
SURFACE_PRI = "#102420"
PILL_BG = "#161B26"
PILL_GREEN_BG = "#0F2922"
FG = "#F8FAFC"
FG_SUB = "#CBD5E1"
MUTED = "#94A3B8"
ACCENT = "#FF5A1F"
ACCENT_SOFT = "#FF7A45"
EMERALD = "#34D399"


def set_click_through(root: tk.Tk, enabled: bool) -> bool:
    """Best-effort OS-level mouse pass-through. Returns True if the OS call succeeded."""
    try:
        if sys.platform == "win32":
            import ctypes

            hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
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


class Overlay:
    def __init__(
        self,
        engine: Engine,
        settings: HudSettings,
        on_settings: Callable[[HudSettings], None] = save_settings,
        background_mode: bool = False,
    ) -> None:
        self.engine, self.settings, self._on_settings = engine, settings, on_settings
        self._background_mode = background_mode
        self._manual_vis_override = False
        self._in_game = False
        self._autostart_on = is_autostart_enabled()

        self.root = tk.Tk()
        self.root.title("PACE MAKER — LoL Pacemaker")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.geometry(f"+{settings.x}+{settings.y}")
        self.root.configure(bg=BORDER)
        self.root.attributes("-alpha", settings.opacity)
        self._drag = (0, 0)
        self._ct_ok = True
        self._hotkeys_global = False
        self._build()
        for seq, fn in (
            ("<F8>", self.toggle_click_through),
            ("<F9>", self.toggle_visible),
            ("[", lambda: self.step_opacity(-1)),
            ("]", lambda: self.step_opacity(1)),
        ):
            self.root.bind(seq, lambda _e, f=fn: f())
        self.root.bind("<ButtonPress-1>", self._drag_start)
        self.root.bind("<B1-Motion>", self._drag_move)
        self.root.bind("<ButtonRelease-1>", lambda _e: self._persist())

        if self._background_mode:
            self.settings = self.settings.toggled_visible()
            self.root.withdraw()

    # -- layout ---------------------------------------------------------------------------
    def _build(self) -> None:
        # 1px orange outer border frame wrapping the dark card
        card = tk.Frame(self.root, bg=BG)
        card.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Frame(card, width=HUD_WIDTH, height=1, bg=BG).pack()  # fixed width spacer

        # Header row: big orange win-rate on left, chips on right
        top = tk.Frame(card, bg=BG)
        top.pack(fill="x", padx=12, pady=(10, 6))
        self.win = tk.Label(top, text="--%", fg=ACCENT, bg=BG, font=("Helvetica", 19, "bold"))
        self.win.pack(side="left")

        # Right-side controls: lang chip, AUTO chip, opacity +/-
        self.lang_chip = tk.Label(
            top, text="EN", fg=ACCENT_SOFT, bg=PILL_BG,
            font=("Helvetica", 9, "bold"), padx=7, pady=2,
            highlightthickness=1, highlightbackground=ACCENT, cursor="hand2",
        )
        self.lang_chip.pack(side="right")
        self.lang_chip.bind("<Button-1>", self._cycle_lang)

        self.auto_chip = tk.Label(
            top, text="AUTO", fg=MUTED, bg=PILL_BG,
            font=("Helvetica", 8, "bold"), padx=6, pady=2,
            highlightthickness=1, highlightbackground="#2A3142", cursor="hand2",
        )
        self.auto_chip.pack(side="right", padx=(0, 5))
        self.auto_chip.bind("<Button-1>", self._toggle_autostart)

        self.phase_chip = tk.Label(
            top, text="", fg=FG_SUB, bg=PILL_BG,
            font=("Helvetica", 9, "bold"), padx=7, pady=2,
            highlightthickness=1, highlightbackground="#2A3142",
        )
        self.phase_chip.pack(side="right", padx=(0, 5))

        # Tier rank pill (emerald banner)
        self.rank_wrap = tk.Frame(card, bg=PILL_GREEN_BG, highlightthickness=1, highlightbackground="#1B4D3E")
        self.rank_wrap.pack(fill="x", padx=12, pady=(2, 6))
        self.rank_lbl = tk.Label(
            self.rank_wrap, text="", fg=EMERALD, bg=PILL_GREEN_BG,
            font=("Helvetica", 9, "bold"), anchor="w", justify="left", wraplength=HUD_WIDTH - 36,
        )
        self.rank_lbl.pack(fill="x", padx=9, pady=5)

        # Smart briefing line
        self.brief = tk.Label(
            card, text="", fg=FG_SUB, bg=BG,
            font=("Helvetica", 9), anchor="w", justify="left", wraplength=HUD_WIDTH - 28,
        )
        self.brief.pack(fill="x", padx=12, pady=(0, 6))

        # 3 Shotcall cards with colored left accent bar
        self.cards_box = tk.Frame(card, bg=BG)
        self.cards_box.pack(fill="x", padx=12, pady=(0, 4))
        self.call_cards: list[tuple[tk.Frame, tk.Label, tk.Label]] = []
        for idx in range(3):
            is_top = idx == 0
            c_bg = SURFACE_PRI if is_top else SURFACE
            bar_col = EMERALD if is_top else ACCENT
            title_col = EMERALD if is_top else ACCENT_SOFT

            outer = tk.Frame(self.cards_box, bg=c_bg, highlightthickness=1, highlightbackground="#232A38")
            bar = tk.Frame(outer, bg=bar_col, width=3)
            bar.pack(side="left", fill="y")
            body = tk.Frame(outer, bg=c_bg)
            body.pack(side="left", fill="both", expand=True, padx=8, pady=6)
            t = tk.Label(
                body, text="", fg=title_col, bg=c_bg,
                font=("Helvetica", 10, "bold"), anchor="w", justify="left", wraplength=HUD_WIDTH - 52,
            )
            d = tk.Label(
                body, text="", fg=FG_SUB, bg=c_bg,
                font=("Helvetica", 9), anchor="w", justify="left", wraplength=HUD_WIDTH - 52,
            )
            t.pack(fill="x")
            d.pack(fill="x", pady=(2, 0))
            self.call_cards.append((outer, t, d))

        # Footer: hotkey hints + subtle opacity controls
        foot = tk.Frame(card, bg=BG)
        foot.pack(fill="x", padx=12, pady=(4, 9))
        self.status = tk.Label(
            foot, text="", fg=MUTED, bg=BG,
            font=("Helvetica", 8), anchor="center", justify="center", wraplength=HUD_WIDTH - 32,
        )
        self.status.pack(fill="x")
        self._style_auto_chip()

    def _style_auto_chip(self) -> None:
        if self._autostart_on:
            self.auto_chip.config(fg=EMERALD, bg=PILL_GREEN_BG, highlightbackground=EMERALD)
        else:
            self.auto_chip.config(fg=MUTED, bg=PILL_BG, highlightbackground="#2A3142")

    # -- actions --------------------------------------------------------------------------
    def _apply(self, new: HudSettings) -> None:
        self.settings = new
        self.root.attributes("-alpha", new.opacity)
        self._persist()

    def _persist(self) -> None:
        self.settings = self.settings.moved(self.root.winfo_x(), self.root.winfo_y())
        self._on_settings(self.settings)

    def step_opacity(self, direction: int) -> None:
        self._apply(self.settings.with_opacity_step(direction))

    def toggle_click_through(self) -> None:
        self.root.after(0, self._toggle_ct)

    def _toggle_ct(self) -> None:
        self.settings = self.settings.toggled_click_through()
        self._ct_ok = set_click_through(self.root, self.settings.click_through)
        self._on_settings(self.settings)

    def toggle_visible(self) -> None:
        self.root.after(0, self._toggle_vis)

    def _toggle_vis(self) -> None:
        self._manual_vis_override = True
        self.settings = self.settings.toggled_visible()
        (self.root.deiconify if self.settings.visible else self.root.withdraw)()

    def _toggle_autostart(self, _e: Any = None) -> None:
        target = not self._autostart_on
        if set_autostart(target):
            self._autostart_on = target
            self._style_auto_chip()
            self.refresh()

    def _cycle_lang(self, _e: Any = None) -> None:
        self.settings = self.settings.next_lang(self.engine.tr.lang)
        self.engine.set_lang(self.settings.lang or "en")
        self._persist()
        self.refresh()

    def _drag_start(self, e: Any) -> None:
        self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def _drag_move(self, e: Any) -> None:
        self.root.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    # -- render ---------------------------------------------------------------------------
    def _sync_auto_visibility(self, in_game: bool) -> None:
        if not self._background_mode:
            return
        if in_game != self._in_game:
            self._in_game = in_game
            self._manual_vis_override = False
        if self._manual_vis_override:
            return
        if in_game and not self.settings.visible:
            self.settings = self.settings.toggled_visible()
            self.root.deiconify()
        elif not in_game and self.settings.visible:
            self.settings = self.settings.toggled_visible()
            self.root.withdraw()

    def refresh(self) -> None:
        st = self.engine.snapshot()
        tr = self.engine.tr
        in_game = "win_pct" in st
        self._sync_auto_visibility(in_game)

        self.lang_chip.config(text=tr.lang.upper())
        if not in_game:
            self.win.config(text="PACE MAKER")
            self.phase_chip.config(text="")
            self.rank_lbl.config(text=tr.t("hud_waiting"))
            self.brief.config(text=tr.t("hud_auto_on" if self._autostart_on else "hud_auto_off"))
            for outer, t, d in self.call_cards:
                t.config(text="")
                d.config(text="")
                outer.pack_forget()
        else:
            self.win.config(text=f"{st['win_pct']}% · {st['state']}")
            self.phase_chip.config(text=st.get("phase", ""))
            self.rank_lbl.config(text=st.get("rank_summary", ""))
            self.brief.config(text=st.get("smart_briefing", ""))
            calls = st.get("calls", [])
            for i, (outer, t, d) in enumerate(self.call_cards):
                if i < len(calls):
                    t.config(text=calls[i]["title"])
                    d.config(text=calls[i]["detail"])
                    outer.pack(fill="x", pady=(0, 6))
                else:
                    outer.pack_forget()

        notes = [tr.t("hud_hint_keys")]
        if not self._hotkeys_global:
            notes.append(tr.t("hud_hotkeys_local_only"))
        if self.settings.click_through:
            notes.append(tr.t("hud_clickthrough_on" if self._ct_ok else "hud_hotkeys_local_only"))
        self.status.config(text="\n".join(notes))
        self.root.after(1000, self.refresh)

    def run(self) -> None:
        self._hotkeys_global = start_global_hotkeys(self.toggle_click_through, self.toggle_visible)
        stop = threading.Event()
        threading.Thread(target=self.engine.run, args=(stop,), daemon=True).start()
        self.refresh()
        try:
            self.root.mainloop()
        finally:
            stop.set()
