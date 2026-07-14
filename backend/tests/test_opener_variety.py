"""Narration variety: the AVOID_OPENERS ban list (so the guide doesn't reopen object
after object the same way) and the widened session-greeting pool (the stale "не спеша"
opener is gone). See narrator.txt / area.txt + languages._GREETING_TAILS."""

from app.services.agent.languages import (
    _GREETING_TAILS,
    greeting,
    recent_openers,
)
from app.services.agent.prompts import build_narrator_user
from app.shared.schemas import (
    GeoPoint,
    NarratorInput,
    Place,
    Significance,
)


def test_recent_openers_extracts_first_words_and_dedups():
    history = [
        "И вот тут начинается самое интересное про эту церковь и её колокольню.",
        "И вот тут стоит старый дом, каких в округе почти не осталось теперь.",
        "Совсем рядом тянется тихая набережная, где когда-то был торговый причал.",
    ]
    openers = recent_openers(history, "ru", words=3)
    # The two "И вот тут" lines collapse to ONE ban entry (case-insensitive dedup)...
    lowered = [o.lower() for o in openers]
    assert lowered.count("и вот тут") == 1
    # ...and the distinct third opener is present too.
    assert any("совсем рядом" in o.lower() for o in openers)


def test_recent_openers_skips_bridges_and_short_lines():
    from app.services.agent.languages import bridges

    bridge = bridges("ru")[0]
    history = [bridge, "Ок.", "Здесь стоит примечательный особняк с богатой историей рода."]
    openers = recent_openers(history, "ru")
    # Neither the verbatim bridge nor the tiny "Ок." seeds an opener.
    assert all(bridge.lower() not in o.lower() for o in openers)
    assert all("ок" != o.lower() for o in openers)
    assert any("здесь стоит" in o.lower() for o in openers)


def test_recent_openers_empty_history():
    assert recent_openers([], "ru") == []


def test_greeting_tail_pool_is_wide_and_destaled():
    ru = _GREETING_TAILS["ru"]
    # We widened the pool well past the original three...
    assert len(ru) >= 8
    # ...and dropped the worn-out "не спеша / не торопясь" openers the user was tired of.
    joined = " ".join(w + " " + wo for w, wo in ru).lower()
    assert "не спеша" not in joined
    assert "не торопясь" not in joined


def test_greeting_varies_across_calls():
    seen = {greeting("ru", place="Тверская", hour=10) for _ in range(40)}
    assert len(seen) >= 4  # random tail => many distinct openers, not one stale line


def test_build_narrator_user_carries_avoid_openers():
    inp = NarratorInput(
        place=Place(id="1", name="Дом", category="building",
                    location=GeoPoint(lat=55.75, lon=37.62)),
        significance=Significance.MEDIUM,
        distance_m=30.0,
        history=["И вот тут стоит старинный дом с необычной судьбой и жильцами."],
        language="ru",
    )
    user = build_narrator_user(inp)
    assert "AVOID_OPENERS" in user
    assert "вот тут" in user.lower()
