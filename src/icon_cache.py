"""Async Data Dragon icon cache for in-game HUD panels (AGENTS §27).

Downloads champion/item PNGs on a background daemon thread into ~/.cache/lol-pacemaker/icons/
so the Tk GUI thread never blocks on network I/O. Falls back gracefully offline.
"""
from __future__ import annotations

import re
import threading
import urllib.request
from pathlib import Path
from typing import Callable

DDRAGON_VER = "16.23.1"
_SAFE_KEY = re.compile(r"^[A-Za-z0-9_]+$")

# Normalize Riot Live Client display names / special keys to Data Dragon champion IDs
_CHAMP_ALIAS = {
    "wukong": "MonkeyKing",
    "monkeyking": "MonkeyKing",
    "fiddlesticks": "Fiddlesticks",
    "nunu&willump": "Nunu",
    "nunu": "Nunu",
    "renataglasc": "Renata",
    "renata": "Renata",
    "drmundo": "DrMundo",
    "jarvaniv": "JarvanIV",
    "leesin": "LeeSin",
    "masteryi": "MasterYi",
    "missfortune": "MissFortune",
    "tahmkench": "TahmKench",
    "twistedfate": "TwistedFate",
    "xinzhao": "XinZhao",
    "aurelionsol": "AurelionSol",
    "kogmaw": "KogMaw",
    "reksai": "RekSai",
    "chogath": "Chogath",
    "khazix": "Khazix",
    "velkoz": "Velkoz",
    "kaisa": "Kaisa",
    "belveth": "Belveth",
    "ksante": "KSante",
    "leblanc": "Leblanc",
}


def normalize_champ_key(raw_name: str) -> str:
    """Best-effort map from rawChampionName or championName to Data Dragon file key."""
    s = str(raw_name or "").strip()
    if "_" in s:
        s = s.rsplit("_", 1)[-1]
    cleaned = re.sub(r"[^A-Za-z0-9]", "", s)
    if not cleaned:
        return ""
    alias = _CHAMP_ALIAS.get(cleaned.lower())
    return alias if alias else cleaned


class IconCache:
    """Thread-safe local disk + Tk PhotoImage cache for Data Dragon champion/item icons."""

    def __init__(self, cache_dir: Path | None = None, ddragon_ver: str = DDRAGON_VER) -> None:
        self.cache_dir = cache_dir or (Path.home() / ".cache" / "lol-pacemaker" / "icons")
        self.ddragon_ver = ddragon_ver
        self._lock = threading.Lock()
        self._in_flight: set[tuple[str, str]] = set()
        self._failed: set[tuple[str, str]] = set()
        self._tk_imgs: dict[tuple[str, str, int], object] = {}

    def _file_path(self, kind: str, key: str) -> Path:
        return self.cache_dir / kind / f"{key}.png"

    def _url(self, kind: str, key: str) -> str:
        folder = "champion" if kind == "champ" else "item"
        return f"https://ddragon.leagueoflegends.com/cdn/{self.ddragon_ver}/img/{folder}/{key}.png"

    def get_cached_path(self, kind: str, key: str) -> Path | None:
        if kind not in ("champ", "item") or not _SAFE_KEY.match(key):
            return None
        p = self._file_path(kind, key)
        return p if p.exists() and p.stat().st_size > 100 else None

    def request_async(self, kind: str, key: str, on_ready: Callable[[], None] | None = None) -> None:
        if kind not in ("champ", "item") or not _SAFE_KEY.match(key):
            return
        if self.get_cached_path(kind, key) is not None:
            return
        pair = (kind, key)
        with self._lock:
            if pair in self._in_flight or pair in self._failed:
                return
            self._in_flight.add(pair)

        def _worker() -> None:
            ok = False
            try:
                p = self._file_path(kind, key)
                p.parent.mkdir(parents=True, exist_ok=True)
                req = urllib.request.Request(self._url(kind, key), headers={"User-Agent": "LoLPacemaker/0.2"})
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    data = resp.read()
                if len(data) > 100 and data[:4] == b"\x89PNG":
                    tmp = p.with_suffix(".tmp")
                    tmp.write_bytes(data)
                    tmp.replace(p)
                    ok = True
            except Exception:
                ok = False
            finally:
                with self._lock:
                    self._in_flight.discard(pair)
                    if not ok:
                        self._failed.add(pair)
            if ok and on_ready is not None:
                try:
                    on_ready()
                except Exception:
                    pass

        threading.Thread(target=_worker, daemon=True).start()

    def get_tk_image(self, kind: str, key: str, size: int = 20, on_ready: Callable[[], None] | None = None) -> object | None:
        """Return a cached Tk PhotoImage resized to `size` px, or None while fetching asynchronously."""
        cache_key = (kind, key, size)
        if cache_key in self._tk_imgs:
            return self._tk_imgs[cache_key]
        path = self.get_cached_path(kind, key)
        if path is None:
            self.request_async(kind, key, on_ready=on_ready)
            return None
        try:
            import tkinter as tk

            try:
                from PIL import Image, ImageTk  # type: ignore[import-not-found]

                im = Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS)
                photo: object = ImageTk.PhotoImage(im)
            except Exception:
                raw_img = tk.PhotoImage(file=str(path))
                w = max(1, raw_img.width())
                factor = max(1, round(w / max(1, size)))
                photo = raw_img.subsample(factor, factor) if factor > 1 else raw_img
            self._tk_imgs[cache_key] = photo
            return photo
        except Exception:
            return None
