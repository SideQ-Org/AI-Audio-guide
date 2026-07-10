"""WalkMemory — the phase-1 narrative-memory substrate: whole-walk anti-repeat,
topic-level dedup, and object recall."""

from app.shared.memory import WalkMemory, is_near_duplicate


def test_is_repeat_spans_the_whole_walk_not_a_window():
    m = WalkMemory()
    first = "Этот старый маяк построили в девятнадцатом веке на скалистом мысу у входа в бухту."
    m.record_narration(first)
    # Bury it under many unrelated later narrations (would fall out of an 18-line window).
    for i in range(40):
        m.record_narration(f"Другая история {i} про мост, рынок и трамвайное депо неподалёку.")
    # The line resurfacing verbatim is still caught — the corpus is the whole walk.
    assert m.is_repeat(first)
    # Something genuinely new is not flagged.
    assert not m.is_repeat("Впереди начинается парк с прудом, где зимой заливают каток.")


def test_object_recall():
    m = WalkMemory()
    assert not m.recalled_object("way/42")
    m.record_object("way/42")
    m.record_object("way/42")  # idempotent
    assert m.recalled_object("way/42")
    assert m.object_ids == ["way/42"]


def test_short_lines_never_flagged():
    m = WalkMemory()
    m.record_narration("Пройдём дальше.")
    assert not m.is_repeat("Пройдём дальше.")  # < 6 tokens -> never a repeat
    assert not is_near_duplicate("Тут рядом — маяк.", ["Тут рядом — маяк."])
