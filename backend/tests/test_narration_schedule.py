"""NarrationScheduler — sentence-level delivery, boundary weaving, pause/resume, priority."""

from app.services.agent.languages import resume_connective
from app.services.agent.narration_schedule import NarrationScheduler
from app.services.agent.orchestrator import OrchestratorOutput
from app.shared.schemas import GeoPoint, Significance


def _narr(text, place_id=None, sig=None):
    return OrchestratorOutput(
        state="narrating", kind="narration", text=text, place_id=place_id, significance=sig
    )


def test_delivers_one_sentence_per_frame():
    s = NarrationScheduler("ru")
    s.set_current(_narr("Первое. Второе. Третье."))
    assert s.next_frame().text == "Первое."
    assert s.next_frame().text == "Второе."
    assert s.next_frame().text == "Третье."
    assert s.next_frame() is None  # exhausted


def test_object_woven_at_boundary_then_line_resumes_from_cursor():
    s = NarrationScheduler("ru")
    s.set_current(_narr("Первое. Второе. Третье."))
    assert s.next_frame().text == "Первое."  # one sentence spoken...
    # a place enters the bubble -> pause the line, weave the object in
    assert s.pause_current(GeoPoint(lat=55.0, lon=37.0)) is True
    s.set_current(_narr("Вот музей.", place_id="p", sig="MEDIUM"))
    assert s.next_frame().text == "Вот музей."
    assert s.next_frame() is None  # object done
    # resume the paused line (still nearby): a connective, then the REMAINING sentences
    assert s.resume(GeoPoint(lat=55.0, lon=37.0), 300.0) is True
    assert s.next_frame().text == resume_connective("ru")
    assert s.next_frame().text == "Второе."  # not "Первое" — cursor preserved
    assert s.next_frame().text == "Третье."


def test_resume_discards_a_line_we_walked_away_from():
    s = NarrationScheduler("ru")
    s.set_current(_narr("Первое. Второе."))
    s.next_frame()
    s.pause_current(GeoPoint(lat=55.0, lon=37.0))
    # ~11 km away -> the paused line is stale, don't resume it
    assert s.resume(GeoPoint(lat=55.1, lon=37.0), 300.0) is False


def test_resume_connective_rotates_across_weaves():
    # A walk with several weave-ins must not repeat the same connective verbatim (the
    # "затёртые связки по кругу" the narrator prompt bans) — it rotates by resume index.
    s = NarrationScheduler("ru")
    seen = []
    for i in range(3):
        s.set_current(_narr("Первое предложение здесь. Второе предложение здесь."))
        s.next_frame()  # speak the first sentence
        s.pause_current(GeoPoint(lat=55.0, lon=37.0))
        s.set_current(_narr("Вот объект.", place_id=f"p{i}", sig="MEDIUM"))
        s.next_frame()  # object done
        assert s.resume(GeoPoint(lat=55.0, lon=37.0), 300.0) is True
        seen.append(s.next_frame().text)  # the connective
        s.next_frame()  # the remaining sentence
    assert seen == [resume_connective("ru", 0), resume_connective("ru", 1),
                    resume_connective("ru", 2)]
    assert len(set(seen)) == 3  # three distinct connectives in a row


def test_priority_area_yields_object_outranks_lower():
    s = NarrationScheduler()
    # an area line (no place) always yields to an object
    s.set_current(_narr("Про район."))
    assert s.current_outranks(Significance.LOW) is False
    # a HIGH object outranks a LOW newcomer -> finish current, cover newcomer after
    s.set_current(_narr("Собор.", place_id="p", sig="HIGH"))
    assert s.current_outranks(Significance.LOW) is True
    # a LOW object yields to a HIGH newcomer -> newcomer inserts
    s.set_current(_narr("Кафе.", place_id="p2", sig="LOW"))
    assert s.current_outranks(Significance.HIGH) is False
