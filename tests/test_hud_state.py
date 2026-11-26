import json

from src.hud_state import LANGS, OPACITY_MAX, OPACITY_MIN, HudSettings, load_settings, save_settings


def test_opacity_is_clamped_both_ways():
    s = HudSettings(opacity=0.35)
    assert s.with_opacity_step(-1).opacity == OPACITY_MIN
    assert s.with_opacity_step(-1).with_opacity_step(-1).opacity == OPACITY_MIN
    top = HudSettings(opacity=0.95).with_opacity_step(1)
    assert top.opacity == OPACITY_MAX and top.with_opacity_step(1).opacity == OPACITY_MAX


def test_language_cycles_through_all_five_and_wraps():
    s, seen = HudSettings(), []
    cur = "en"
    for _ in range(6):
        s = s.next_lang(cur)
        cur = s.lang
        seen.append(cur)
    assert seen[:5] == ["ko", "ja", "es", "pt", "en"] and seen[5] == "ko" and set(LANGS) == {"en", "ko", "ja", "es", "pt"}


def test_toggles_are_pure_and_reversible():
    s = HudSettings()
    assert s.toggled_click_through().click_through and not s.click_through
    assert s.toggled_click_through().toggled_click_through().click_through is False
    assert s.toggled_visible().visible is False and s.toggled_visible().toggled_visible().visible
    assert s.moved(5, 6).x == 5 and s.x == 40


def test_settings_roundtrip_and_hidden_state_not_persisted_as_startup_state():
    save_settings(HudSettings(x=100, y=200, opacity=0.5, lang="ja", tier="high", click_through=True, visible=False))
    s = load_settings()
    assert (s.x, s.y, s.opacity, s.lang, s.tier, s.click_through) == (100, 200, 0.5, "ja", "high", True)
    assert s.visible is True  # a HUD saved while hidden must not start invisible


def test_corrupt_or_hostile_settings_are_repaired(tmp_path):
    p = tmp_path / "s.json"
    for payload in ("{broken", "[]", json.dumps({"x": "abc", "y": -500, "opacity": 99, "lang": "xx", "tier": "z", "extra": 1}), ""):
        p.write_text(payload)
        s = load_settings(p, screen_w=1920, screen_h=1080)
        assert 0 <= s.x <= 1860 and 0 <= s.y <= 1020
        assert OPACITY_MIN <= s.opacity <= OPACITY_MAX and s.lang in (None, *LANGS) and s.tier in ("low", "mid", "high")
    assert load_settings(tmp_path / "missing.json").opacity == 0.9
