import json
from pathlib import Path

from src.i18n import Translator


def test_all_locales_match_english_keys():
    locales_dir = Path(__file__).resolve().parent.parent / "locales"
    en_keys = set(json.loads((locales_dir / "en.json").read_text(encoding="utf-8")).keys())
    for path in locales_dir.glob("*.json"):
        keys = set(json.loads(path.read_text(encoding="utf-8")).keys())
        assert keys == en_keys, f"{path.name} keys mismatch: missing={en_keys - keys}, extra={keys - en_keys}"


def test_unknown_locale_falls_back_to_english():
    tr = Translator(lang="fr_FR")
    assert tr.lang == "en"
    assert tr.t("app_title") == "LoL Pacemaker"
