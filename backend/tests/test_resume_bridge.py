"""Resume bridges after a question / un-pause: phrase banks + scheduler relevance logic.

Pure-logic coverage (no I/O) for the pieces the producer relies on: tour_bridge rotation/modes
and NarrationScheduler.pause_current/top_paused/resumable/resume(add_connective)/drop_paused.
"""

from __future__ import annotations

from app.services.agent.languages import resume_connective, tour_bridge
from app.services.agent.narration_schedule import NarrationScheduler
from app.services.agent.orchestrator import OrchestratorOutput
from app.shared.schemas import GeoPoint


def _line(text: str, place_id: str | None = None) -> OrchestratorOutput:
    return OrchestratorOutput(
        state="narrating", kind="narration", text=text, place_id=place_id
    )


# --- phrase banks ------------------------------------------------------------------------- #


def test_tour_bridge_modes_and_rotation() -> None:
    cont = [tour_bridge("ru", i, "continue") for i in range(9)]
    onward = [tour_bridge("ru", i, "onward") for i in range(9)]
    # the two moods are distinct phrase sets
    assert not (set(cont) & set(onward))
    # rotation gives variety, then wraps
    assert len(set(cont)) > 1
    assert tour_bridge("ru", 0, "continue") == tour_bridge("ru", 9, "continue")
    # every language resolves (falls back to English for an unknown code), both moods
    for lang in ("ru", "en", "es", "fr", "de", "it", "pt", "zh", "xx"):
        assert tour_bridge(lang, 0, "continue")
        assert tour_bridge(lang, 0, "onward")


# --- scheduler relevance ------------------------------------------------------------------ #

HERE = GeoPoint(lat=55.75, lon=37.61)
FAR = GeoPoint(lat=55.76, lon=37.61)  # ~1.1 km north — well past any resume radius


def _park(sched: NarrationScheduler, text: str, at: GeoPoint, place_id=None) -> None:
    sched.set_current(_line(text, place_id))
    sched.next_frame()  # speak the first sentence, leaving the rest to park
    assert sched.pause_current(at) is True


def test_top_paused_and_resumable_within_radius() -> None:
    sched = NarrationScheduler("ru")
    _park(sched, "Первое. Второе. Третье.", HERE, place_id="way/1")
    top = sched.top_paused()
    assert top is not None and top.is_object
    # standing where we paused -> still relevant
    assert sched.resumable(HERE, 70.0) is True
    # walked far past the object -> no longer relevant
    assert sched.resumable(FAR, 70.0) is False


def test_resume_without_connective_leaves_line_verbatim() -> None:
    sched = NarrationScheduler("ru")
    _park(sched, "Альфа. Бета. Гамма.", HERE)
    assert sched.resume(HERE, 200.0, add_connective=False) is True
    # no weave connective was spliced in — the next sentence is the real next one
    assert sched.next_frame().text == "Бета."
    # contrast: the default path DOES prepend a connective
    sched2 = NarrationScheduler("ru")
    _park(sched2, "Альфа. Бета. Гамма.", HERE)
    assert sched2.resume(HERE, 200.0) is True
    assert sched2.next_frame().text == resume_connective("ru", 0)


def test_drop_paused_discards_stale_lines() -> None:
    sched = NarrationScheduler("ru")
    _park(sched, "Один. Два. Три.", HERE)
    sched.drop_paused()
    assert sched.top_paused() is None
    assert sched.resumable(HERE, 999.0) is False
