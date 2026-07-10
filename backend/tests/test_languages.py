"""Localization helpers for spoken-verbatim + model-facing agent strings.

The bridges and the STT 'didn't catch that' message reach the user WITHOUT going
through the LLM, so they must be in the session language. The cascade strings steer
the LLM (output language is set by core.txt), so Russian stays byte-identical to the
tuned flow and every other language falls back to neutral English.
"""

from app.services.agent import languages as lang


def test_bridges_are_localized_per_language():
    ru = lang.bridges("ru")
    en = lang.bridges("en")
    assert ru and en
    assert ru != en  # genuinely translated, not echoed
    assert "Идём" in ru[0]
    # every supported language resolves to a non-empty tuple
    for code in ("es", "fr", "de", "it", "pt", "zh"):
        assert lang.bridges(code)
    # unknown code falls back to English
    assert lang.bridges("xx") == en


def test_stt_unclear_is_localized():
    assert lang.stt_unclear("ru") == "Не расслышал — повтори, пожалуйста."
    assert lang.stt_unclear("fr").lower().startswith("je n")
    assert lang.stt_unclear("xx") == lang.stt_unclear("en")  # fallback


def test_cascade_strings_ru_identical_others_english():
    # Russian is preserved exactly so the tuned RU flow is unchanged.
    assert lang.level_labels("ru") == ("город", "район", "улицу")
    assert "про город Москва" in lang.area_topic("ru", "город", "Москва")
    assert lang.street_hook("ru", "Тверская") == "переход на улицу Тверская"
    assert lang.area_intro_told("ru") == "вступление в район"

    # Non-Russian sessions steer the LLM in neutral English.
    assert lang.level_labels("fr") == ("city", "district", "street")
    assert "city Paris" in lang.area_topic("fr", "city", "Paris")
    assert lang.street_hook("de", "Hauptstraße") == "stepping onto Hauptstraße"
    assert lang.area_intro_told("es") == "area intro"


def test_display_name_follows_session_language():
    # A Moscow POI: raw name is Russian, with localized name:<lang> tags available.
    tags = {
        "name": "Красная площадь",
        "name:en": "Red Square",
        "name:de": "Roter Platz",
    }
    raw = tags["name"]
    # Session language wins when present...
    assert lang.display_name(tags, raw, "de") == "Roter Platz"
    # ...else the English exonym...
    assert lang.display_name(tags, raw, "fr") == "Red Square"
    # ...and a Cyrillic raw name is romanized as a last resort (no en, no name:<lang>).
    assert lang.display_name({"name": raw}, raw, "en") == "Krasnaya ploshchad"
    # int_name is preferred over a mechanical transliteration.
    assert lang.display_name({"name": raw, "int_name": "Krasnaya Ploshchad"}, raw, "en") \
        == "Krasnaya Ploshchad"
    # A native-language session keeps the original Cyrillic.
    assert lang.display_name(tags, raw, "ru") == "Красная площадь"
    # Unknown/empty code falls back to English (normalize -> en).
    assert lang.display_name(tags, raw, None) == "Red Square"


def test_transliterate_romanizes_cyrillic():
    assert lang.transliterate("Звонница") == "Zvonnitsa"
    assert lang.transliterate("Боровицкие ворота") == "Borovitskie vorota"
    # Latin / digits / punctuation pass through untouched.
    assert lang.transliterate("Cafe 21") == "Cafe 21"
    assert lang.transliterate("Tverskaya") == "Tverskaya"


def test_passing_mention_localized_and_side_keyed():
    # The deterministic floor mention is emitted verbatim, so it must be localized and
    # keyed by SIDE (left/right only arrive at high gaze; else a neutral 'near').
    assert lang.passing_mention("ru", "Звонница", "left") == "Слева — Звонница."
    assert lang.passing_mention("ru", "Звонница", None) == "Тут рядом — Звонница."
    assert "Belfry" in lang.passing_mention("en", "Belfry", "right")
    # an unknown side degrades to the neutral 'near' phrasing
    assert "Звонница" in lang.passing_mention("ru", "Звонница", "sideways")
    # an unknown language falls back to English
    assert lang.passing_mention("xx", "Tower", "ahead") == \
        lang.passing_mention("en", "Tower", "ahead")
