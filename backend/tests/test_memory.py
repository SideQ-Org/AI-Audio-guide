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


def test_new_facts_drops_told_and_reworded():
    # Fact-level dedup is LEXICAL (token overlap): it catches a fact re-stated with the same
    # words reordered / lightly changed — the common way the model repeats from one facts blob.
    # (Full semantic paraphrase with different words is a later embeddings phase.)
    m = WalkMemory()
    batch1 = [
        "Здесь вдоль всей улицы росли высокие старые берёзы.",
        "Улицу проложили в тысяча девятьсот пятом году при заводе.",
    ]
    new1 = m.new_facts(batch1)
    assert new1 == batch1  # first beat: everything is new
    m.mark_facts_told(new1)
    new2 = m.new_facts([
        "Вдоль всей улицы здесь росли старые высокие берёзы.",  # same words reordered -> dropped
        "Позже на этой улице построили большую кирпичную школу.",  # new -> kept
    ])
    assert new2 == ["Позже на этой улице построили большую кирпичную школу."]


def test_new_facts_dedups_within_one_batch():
    m = WalkMemory()
    out = m.new_facts([
        "Старая мельница у реки молола зерно для всей округи.",
        "Старая мельница молола у реки зерно для всей округи.",  # same words reordered
    ])
    assert len(out) == 1
