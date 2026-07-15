"""Backstop that strips empty poetic/elemental filler ("время застыло", "дышит историей",
abstract вода/воздух/огонь/камень) from narration — a "nothing to say" tell over the CORE ban."""

from __future__ import annotations

from app.services.agent.narrator import split_hook, strip_cliche_filler


def test_strips_time_stood_still() -> None:
    text = "Тут старая застройка. Здесь время застыло, и всё дышит историей."
    out = strip_cliche_filler(text, "ru")
    assert out == "Тут старая застройка."
    assert "застыло" not in out and "дышит" not in out


def test_strips_abstract_elemental_listing() -> None:
    text = "Красивый двор. Здесь встречаются вода, воздух, огонь и камень."
    out = strip_cliche_filler(text, "ru")
    assert out == "Красивый двор."


def test_keeps_single_element_with_real_fact() -> None:
    # A single element word in a genuine fact must survive (fountain / stone monument).
    text = "В центре двора — каменный фонтан, вода в нём бьёт с прошлого века."
    out = strip_cliche_filler(text, "ru")
    assert out == text


def test_keeps_plain_factual_sentence() -> None:
    text = "Дом построили в начале прошлого века для рабочих завода."
    assert strip_cliche_filler(text, "ru") == text


def test_wired_into_split_hook() -> None:
    # The single choke point every narration path goes through must apply the filter.
    spoken, _hook = split_hook("Старый квартал. Место пропитано атмосферой вечности.", "ru")
    assert spoken == "Старый квартал."


def test_non_ru_untouched() -> None:
    text = "Time stood still here."
    assert strip_cliche_filler(text, "en") == text
