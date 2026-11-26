from pathlib import Path


def test_web_landing_contains_all_5_languages_and_safe_textcontent():
    html = (Path(__file__).resolve().parent.parent / "web" / "index.html").read_text(encoding="utf-8")
    for code in ("en", "ko", "ja", "es", "pt"):
        assert f'value="{code}"' in html
        assert f"\n  {code}: {{" in html
    assert "el.textContent = dict[key]" in html
    assert "innerHTML" not in html
    assert "Content-Security-Policy" in html
