from pathlib import Path


def test_web_landing_contains_all_5_languages_and_safe_textcontent():
    html = (Path(__file__).resolve().parent.parent / "web" / "index.html").read_text(encoding="utf-8")
    for code in ("en", "ko", "ja", "es", "pt"):
        assert f'value="{code}"' in html
        assert f"\n  {code}: {{" in html
    assert "el.textContent = dict[key]" in html
    assert "innerHTML" not in html
    assert "Content-Security-Policy" in html


def _html(name: str) -> str:
    return (Path(__file__).resolve().parent.parent / name / "index.html").read_text(encoding="utf-8")


def test_theme_toggle_tokens_persist_and_stay_in_sync():
    docs, web = _html("docs"), _html("web")
    assert docs == web
    for needle in (
        'id="theme-toggle"',
        'pacemaker_theme',
        ':root[data-theme="dark"]',
        ':root:not([data-theme="light"])',
        "prefers-color-scheme: dark",
        "color-scheme: dark",
    ):
        assert needle in docs
    # first-paint script runs before the stylesheet so the theme never flashes
    assert docs.index("pacemaker_theme") < docs.index("<style>")
    # accessible name exists in all 5 languages
    for code in ("en", "ko", "ja", "es", "pt"):
        assert f'  {code}: "' in docs.split("const THEME_LABEL")[1].split("};")[0] or f"{code}:" in docs
    assert "innerHTML" not in docs


def test_no_hardcoded_brand_orange_left():
    assert "#ff5a1f" not in _html("docs").lower()
