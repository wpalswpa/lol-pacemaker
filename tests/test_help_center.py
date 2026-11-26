"""AGENTS §22: in-site help center (category › subcategory › article)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import help_content  # noqa: E402


def _embedded(name: str) -> list:
    html = (ROOT / name).read_text(encoding="utf-8")
    m = re.search(r"const HELP = (.*?);\n", html, re.S)
    assert m, f"{name}: HELP data missing"
    return json.loads(m.group(1))


def test_source_is_complete_in_5_languages_with_real_images():
    assert help_content.check() == []
    cats = help_content.HELP
    assert len(cats) == 3 and sum(len(c["subs"]) for c in cats) == 9
    assert sum(len(s["items"]) for c in cats for s in c["subs"]) >= 25
    ids = [a["id"] for c in cats for s in c["subs"] for a in s["items"]]
    assert len(ids) == len(set(ids))


def test_both_pages_embed_the_same_current_source():
    expected = json.loads(json.dumps(help_content.HELP, ensure_ascii=False))
    assert _embedded("docs/index.html") == expected == _embedded("web/index.html")


def test_help_is_rendered_with_textcontent_only():
    html = (ROOT / "web/index.html").read_text(encoding="utf-8")
    body = html[html.index("function renderHelp()"):html.index("function applyLang(lang)")]
    assert "innerHTML" not in body and "insertAdjacentHTML" not in body
    for lang in help_content.LANGS:
        block = html[html.index(f"\n  {lang}: {{"):]
        for key in ("help_search", "help_all", "help_empty", "help_link"):
            assert re.search(rf"\b{key}: \"[^\"]+\"", block.split("\n  }")[0]), (lang, key)


def test_menu_names_in_help_match_the_real_hud_labels():
    """Steps quote HUD menu items; they must be the exact labels the HUD shows in that language."""
    for lang in help_content.LANGS:
        loc = json.loads((ROOT / "locales" / f"{lang}.json").read_text(encoding="utf-8"))
        text = json.dumps(help_content.HELP, ensure_ascii=False)
        for key in ("hud_menu_lock", "hud_menu_reset", "hud_menu_auto", "hud_menu_data"):
            assert loc[key] in text, (lang, key, loc[key])
