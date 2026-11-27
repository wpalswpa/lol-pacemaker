import re
from pathlib import Path


def _html(name: str) -> str:
    return (Path(__file__).resolve().parent.parent / name / "index.html").read_text(encoding="utf-8")


def test_web_landing_contains_all_5_languages_and_safe_textcontent():
    html = _html("web")
    for code in ("en", "ko", "ja", "es", "pt"):
        assert f'value="{code}"' in html
        assert f"\n  {code}: {{" in html
    assert "el.textContent = dict[key]" in html
    assert "innerHTML" not in html
    assert "Content-Security-Policy" in html


def test_theme_toggle_tokens_persist_and_stay_in_sync():
    docs, web = _html("docs"), _html("web")
    assert docs == web
    for needle in (
        'id="theme-toggle"',
        "pacemaker_theme",
        ':root[data-theme="dark"]',
        ':root:not([data-theme="light"])',
        "prefers-color-scheme: dark",
        "color-scheme: dark",
    ):
        assert needle in docs
    # first-paint script runs before the stylesheet so the theme never flashes
    assert docs.index("pacemaker_theme") < docs.index("<style>")
    for code in ("en", "ko", "ja", "es", "pt"):
        assert f'  {code}: "' in docs.split("const THEME_LABEL")[1].split("};")[0]
    assert "innerHTML" not in docs


def test_portal_structure_has_header_search_and_region_pills():
    docs = _html("docs")
    for needle in ('id="hdr-search-form"', 'id="region-bar"', 'class="workspace-grid"', "LoLPacemaker-Setup.exe"):
        assert needle in docs
    # Immutable Core (AGENTS.md §0): preserve PACE MAKER signature orange in dark & light themes
    assert "#ff5a1f" in docs.lower() and "#ea4c14" in docs.lower()


def test_match_lookup_uses_the_worker_contract():
    """The page and worker/riot-proxy.js must agree (a mismatch shipped `invalid_riot_id_format`)."""
    root = Path(__file__).resolve().parent.parent
    html = (root / "docs" / "index.html").read_text(encoding="utf-8")
    worker = (root / "worker" / "riot-proxy.js").read_text(encoding="utf-8")
    assert 'url.searchParams.get("riot_id")' in worker and "riot_id: `" in worker and "durationMin" in worker
    assert '"/?riot_id=" + encodeURIComponent(gameName + "#" + tagLine)' in html
    assert "data.riot_id" in html and "m.durationMin" in html
    assert "/api/matches" not in html and "data.error ||" not in html


def test_three_menus_english_default_and_contact_inside_the_site():
    """AGENTS §9/§16/§17: three menus, English unless chosen, support never routes to GitHub issues."""
    html = _html("docs")
    links = re.findall(r'data-view-link="(\w+)"', html)
    assert links == ["home", "history", "support"]
    assert "navigator.language" not in html and 'return "en";' in html
    assert "github.com/wpalswpa/lol-pacemaker/issues" not in html
    assert 'WORKER_URL + "/v1/support"' in html and "openHelpArticle" in html
    assert "guide_step" not in html  # mock-up guide images were replaced by real captures
    for img in ("app_consent.png", "app_hud.png", "app_hud_folded.png"):
        assert f"./images/{img}" in html
        assert (Path(__file__).resolve().parent.parent / "docs" / "images" / img).exists()
    for key in ("ct_send", "chat_handoff", "gm2_d", "gw1_d"):
        assert html.count(f"{key}:") == 5  # every language has the string


def test_show_view_safe_for_oauth_hash():
    """OAuth callbacks return #auth_token=... which is an invalid CSS selector."""
    assert "document.querySelector(location.hash)" not in _html("docs")
