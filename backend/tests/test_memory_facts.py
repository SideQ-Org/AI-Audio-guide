"""Fact-level paraphrase dedup (is_fact_duplicate) — the tester-found «ниши на первых
этажах» repeat: the same real-world claim, re-fetched under a different area scope,
comes back reworded by the web distiller and must still be recognised as told. Fixture
pairs below are tuned empirically: paraphrases MUST match, distinct same-topic facts
MUST NOT (over-matching would silence genuinely new information)."""

from app.shared.memory import WalkMemory, is_fact_duplicate

# Paraphrase pairs: the SAME claim in two independent LLM wordings (street-scoped vs
# district-scoped web fetch). Every pair must be caught.
_PARAPHRASES = [
    (
        "В домах вдоль улицы на первых этажах сохранились характерные ниши, "
        "где раньше размещались лавки и мастерские.",
        "Особенность района — ниши на первых этажах старых домов: в них когда-то "
        "работали торговые лавки.",
    ),
    (
        "Улица возникла в конце девятнадцатого века вдоль железной дороги.",
        "Район сложился в конце девятнадцатого столетия, когда сюда пришла железная дорога.",
    ),
    (
        "Название посёлку дала усадьба Мысово, стоявшая на берегу залива.",
        "Своё имя эти места получили от усадьбы Мысово у самого залива.",
    ),
    (
        "Здесь строили дирижабли — от этого пошло и название города.",
        "Город назван в честь дирижаблестроительной верфи, работавшей здесь.",
    ),
]

# Distinct facts about the SAME street/district — must all pass as new (the matcher may
# not buy anti-repeat at the price of silencing real information).
_DISTINCT = [
    "В домах вдоль улицы на первых этажах сохранились характерные ниши, "
    "где раньше размещались лавки и мастерские.",
    "В конце улицы стоит водонапорная башня начала прошлого века.",
    "Местная школа выпустила двух известных авиаконструкторов.",
    "Вдоль южной стороны тянется липовая аллея, высаженная к юбилею города.",
    "Трамвайная линия пришла сюда позже, чем в соседние районы.",
]


def test_paraphrases_are_caught():
    for a, b in _PARAPHRASES:
        assert is_fact_duplicate(b, [a]), f"paraphrase slipped: {b!r} vs {a!r}"
        assert is_fact_duplicate(a, [b]), f"paraphrase slipped (reversed): {a!r}"


def test_distinct_facts_are_not_over_matched():
    for i, f in enumerate(_DISTINCT):
        others = _DISTINCT[:i] + _DISTINCT[i + 1:]
        assert not is_fact_duplicate(f, others), f"over-match: {f!r}"


def test_new_facts_filters_cross_fetch_paraphrase():
    """The live path: fetch #1's atom is told; fetch #2 (new area scope) returns the
    same claim reworded — new_facts must drop it, and keep a genuinely new one."""
    mem = WalkMemory()
    first, second = _PARAPHRASES[0]
    mem.mark_facts_told([first])
    fresh = "В конце улицы стоит водонапорная башня начала прошлого века."
    out = mem.new_facts([second, fresh])
    assert out == [fresh]


def test_spoken_wording_extends_the_ledger():
    """Marking the narrator's own sentence lets a later paraphrase match against the
    SPOKEN wording even when it is far from the raw atom."""
    mem = WalkMemory()
    spoken = ("Обрати внимание на ниши в первых этажах — когда-то в них "
              "торговали лавочники.")
    mem.mark_facts_told([spoken])
    refetched = ("Особенность района — ниши на первых этажах старых домов: "
                 "в них когда-то работали торговые лавки.")
    assert mem.new_facts([refetched]) == []


def test_short_facts_never_flagged_as_paraphrase():
    # Too little signal for stem/trigram judgement — only the verbatim path may fire.
    assert not is_fact_duplicate("Старый парк.", ["Совсем другой факт про площадь."])
