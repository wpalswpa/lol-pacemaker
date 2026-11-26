"""Tk companion HUD: small transparent always-on-top card (thin view over `hud_state` + `engine`).

Drag = move · F8 click-through · F9 hide/show · [ ] opacity · language chip cycles en/ko/ja/es/pt.
Click-through: Windows via WS_EX_TRANSPARENT (ctypes); macOS via NSWindow.ignoresMouseEvents (pyobjc, optional).
Global hotkeys use pynput when available; otherwise the keys only work while the HUD has focus.
"""
from __future__ import annotations

import sys
import threading
import tkinter as tk
from typing import Any, Callable

from src.engine import Engine
from src.hud_state import HUD_WIDTH, HudSettings, save_settings

BG, FG, ACCENT, MUTED, WARN = "#0B1120", "#F8FAFC", "#38BDF8", "#94A3B8", "#FDE68A"
TRANSPARENT_KEY = "#010203"


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
    def __init__(self, engine: Engine, settings: HudSettings, on_settings: Callable[[HudSettings], None] = save_settings) -> None:
        self.engine, self.settings, self._on_settings = engine, settings, on_settings
        self.root = tk.Tk()
        self.root.title("LoL Pacemaker")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.geometry(f"+{settings.x}+{settings.y}")  # size comes from content (fixed-width spacer below)
        self.root.configure(bg=BG)
        self.root.attributes("-alpha", settings.opacity)
        self._drag = (0, 0)
        self._ct_ok = True
        self._hotkeys_global = False
        self._build()
        for seq, fn in (("<F8>", self.toggle_click_through), ("<F9>", self.toggle_visible),
                        ("[", lambda: self.step_opacity(-1)), ("]", lambda: self.step_opacity(1))):
            self.root.bind(seq, lambda _e, f=fn: f())
        self.root.bind("<ButtonPress-1>", self._drag_start)
        self.root.bind("<B1-Motion>", self._drag_move)
        self.root.bind("<ButtonRelease-1>", lambda _e: self._persist())

    # -- layout ---------------------------------------------------------------------------
    def _build(self) -> None:
        pad = dict(bg=BG)
        tk.Frame(self.root, width=HUD_WIDTH, height=1, bg=BG).pack()  # fixes the width, height follows content
        top = tk.Frame(self.root, **pad)
        top.pack(fill="x", padx=10, pady=(8, 2))
        self.win = tk.Label(top, text="--%", fg=ACCENT, font=("Helvetica", 20, "bold"), **pad)
        self.win.pack(side="left")
        self.lang_chip = tk.Label(top, text="EN", fg=FG, bg="#1E293B", font=("Helvetica", 10, "bold"), padx=6, cursor="hand2")
        self.lang_chip.pack(side="right")
        self.lang_chip.bind("<Button-1>", self._cycle_lang)
        for txt, d in (("+", 1), ("−", -1)):
            b = tk.Label(top, text=txt, fg=FG, bg="#1E293B", font=("Helvetica", 10, "bold"), padx=5, cursor="hand2")
            b.pack(side="right", padx=(0, 4))
            b.bind("<Button-1>", lambda _e, d=d: self.step_opacity(d))
        self.phase = tk.Label(self.root, text="", fg=MUTED, font=("Helvetica", 9), anchor="w", **pad)
        self.phase.pack(fill="x", padx=10)
        self.calls: list[tuple[tk.Label, tk.Label]] = []
        for _ in range(3):
            t = tk.Label(self.root, text="", fg=WARN, font=("Helvetica", 11, "bold"), anchor="w", justify="left", wraplength=HUD_WIDTH - 24, **pad)
            d = tk.Label(self.root, text="", fg="#CBD5E1", font=("Helvetica", 9), anchor="w", justify="left", wraplength=HUD_WIDTH - 24, **pad)
            t.pack(fill="x", padx=10, pady=(6, 0))
            d.pack(fill="x", padx=10)
            self.calls.append((t, d))
        self.brief = tk.Label(self.root, text="", fg="#E2E8F0", font=("Helvetica", 9), anchor="w", justify="left", wraplength=HUD_WIDTH - 24, **pad)
        self.brief.pack(fill="x", padx=10, pady=(8, 0))
        self.status = tk.Label(self.root, text="", fg=MUTED, font=("Helvetica", 8), anchor="w", **pad)
        self.status.pack(fill="x", padx=10, pady=(4, 8))

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
        self.settings = self.settings.toggled_visible()
        (self.root.deiconify if self.settings.visible else self.root.withdraw)()

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
    def refresh(self) -> None:
        st = self.engine.snapshot()
        tr = self.engine.tr
        self.lang_chip.config(text=tr.lang.upper())
        if "win_pct" not in st:
            self.win.config(text="--%")
            self.phase.config(text=tr.t("hud_waiting"))
            for t, d in self.calls:
                t.config(text="")
                d.config(text="")
            self.brief.config(text="")
        else:
            self.win.config(text=f"{st['win_pct']}% · {st['state']}")
            self.phase.config(text=f"{st['phase']} · {st['rank_summary']}")
            calls = st.get("calls", [])
            for i, (t, d) in enumerate(self.calls):
                t.config(text=calls[i]["title"] if i < len(calls) else "")
                d.config(text=calls[i]["detail"] if i < len(calls) else "")
            self.brief.config(text=st.get("smart_briefing", ""))
        notes = [tr.t("hud_hint_keys")]
        if not self._hotkeys_global:
            notes.append(tr.t("hud_hotkeys_local_only"))
        if self.settings.click_through:
            notes.append(tr.t("hud_clickthrough_on" if self._ct_ok else "hud_hotkeys_local_only"))
        self.status.config(text="\n".join(notes), wraplength=HUD_WIDTH - 24, justify="left")
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
