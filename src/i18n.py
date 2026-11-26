"""Locale loader and translator for LoL Shotcaller."""
from __future__ import annotations

import json
import locale
from pathlib import Path
from typing import Any

LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"
DEFAULT_LANG = "en"


def detect_system_lang() -> str:
    """Detect OS locale and map to a supported language code (default: 'en')."""
    try:
        loc, _ = locale.getlocale()
    except ValueError:
        loc = None
    if not loc:
        return DEFAULT_LANG
    prefix = loc.split("_")[0].split("-")[0].lower()
    if (LOCALES_DIR / f"{prefix}.json").exists():
        return prefix
    return DEFAULT_LANG


class Translator:
    """Loads `<lang>.json` with safe fallback to `en.json`."""

    def __init__(self, lang: str | None = None, locales_dir: Path | None = None) -> None:
        base = locales_dir or LOCALES_DIR
        self.fallback = json.loads((base / f"{DEFAULT_LANG}.json").read_text(encoding="utf-8"))
        chosen = (lang or detect_system_lang()).lower()
        target = base / f"{chosen}.json"
        if target.exists():
            self.lang = chosen
            self.table = json.loads(target.read_text(encoding="utf-8"))
        else:
            self.lang = DEFAULT_LANG
            self.table = self.fallback

    def t(self, key: str, **params: Any) -> str:
        """Translate `key` and format optional placeholders safely."""
        template = self.table.get(key) or self.fallback.get(key, key)
        if not params:
            return template
        resolved = {
            k: (self.t(v[4:]) if isinstance(v, str) and v.startswith("key:") else v)
            for k, v in params.items()
        }
        return template.format(**resolved)
