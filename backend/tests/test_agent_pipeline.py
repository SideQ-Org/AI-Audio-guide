import asyncio
from pathlib import Path

from app.services.agent.companion import HeuristicCompanion
from app.services.agent.narrator import LLMNarrator, TemplateNarrator
from app.services.agent.pipeline import TextPipeline
from app.services.agent.scorer import HeuristicScorer, LLMScorer
from app.services.enrichment.enricher import MockEnricher
from app.services.geo.discovery import Discovery
from app.services.geo.providers import StaticPlaceProvider
from app.services.llm.client import FakeLLM
from app.services.llm.router import Role
from app.shared.schemas import (
    Address,
    Candidate,
    CompanionInput,
    GazeConfidence,
    GeoPoint,
    Heading,
    NarratorFlags,
    NarratorInput,
    Pace,
    Place,
    ScorerInput,
    Significance,
)
from sim.routes import RED_SQUARE
from sim.walk import walk

FIX = Path(__file__).parent / "fixtures"


def _candidate(pid, name, category, weight, dist=10.0, facts=None) -> Candidate:
    return Candidate(
        place=Place(id=pid, name=name, category=category, location=GeoPoint(lat=1, lon=2)),
        distance_m=dist,
        type_weight=weight,
        in_gaze_cone=True,
        gaze_confidence=GazeConfidence.LOW,
        facts_available=facts is not None,
        facts_snippet=facts,
    )


def test_warm_narration_populates_the_pregeneration_cache():
    """Pre-generating the passing narration for an object you're approaching fills the
    cache keyed (place_id, lang), so step() can speak it instantly on arrival."""
    async def run():
        pipeline = TextPipeline(
            HeuristicScorer(), TemplateNarrator(),
            MockEnricher({"p": "Большой музей девятнадцатого века."}), enrich_top_k=3,
        )
        cand = _candidate("p", "Музей", "museum", 0.6, dist=120, facts=None)
        await pipeline.warm_narration(
            cand, seen=[], history=[], address=Address(), heading=Heading(),
            pace=Pace.SLOW, preferences=None, language="ru", theme=None, told=[],
            next_hook=None,
        )
        return pipeline._narr_cache

    cache = asyncio.run(run())
    assert ("p", "ru") in cache
    assert cache[("p", "ru")][0]  # non-empty pre-generated text


def test_step_uses_and_pops_the_pregeneration_cache():
    """step() speaks the pre-generated blurb (no LLM call) and removes it — one-shot."""
    async def run():
        pipeline = TextPipeline(HeuristicScorer(), TemplateNarrator(), MockEnricher({}))
        pipeline._narr_cache[("p", "ru")] = ("Готовый рассказ про музей.", "хук")
        near = _candidate("p", "Музей", "museum", 0.6, dist=30, facts=None)
        out = await pipeline.step([near], seen=[], history=[], language="ru", passing=True)
        return out, pipeline._narr_cache

    out, cache = asyncio.run(run())
    assert out.text == "Готовый рассказ про музей."  # spoke the cached text...
    assert ("p", "ru") not in cache  # ...and popped it (one-shot)


def test_template_narrator_silence_when_nothing_new():
    n = TemplateNarrator()
    inp = NarratorInput(
        place=Place(id="p", name="X", category="park", location=GeoPoint(lat=1, lon=2)),
        significance=Significance.LOW,
        distance_m=5,
        flags=NarratorFlags(nothing_new=True),
    )
    assert asyncio.run(n.narrate(inp)) == ""


def test_template_narrator_no_repeat():
    n = TemplateNarrator()
    inp = NarratorInput(
        place=Place(id="p", name="Музей", category="museum", location=GeoPoint(lat=1, lon=2)),
        significance=Significance.HIGH,
        facts="Большой музей.",
        distance_m=5,
        history=["Музей рядом, интересное место."],
    )
    assert asyncio.run(n.narrate(inp)) == ""  # name already in history


def test_heuristic_scorer_skips_blocked_category():
    scorer = HeuristicScorer()
    from app.shared.schemas import ControlPatch

    out = asyncio.run(
        scorer.score(
            ScorerInput(
                candidates=[
                    _candidate("shop1", "ГУМ", "shop", 0.25),
                    _candidate("mus1", "Музей", "museum", 0.9, facts="факт"),
                ],
                preferences=ControlPatch(skip_categories=["shop"]),
            )
        )
    )
    assert out.next == "mus1"
    sig = {s.place_id: s.significance for s in out.scored}
    assert sig["shop1"] is Significance.SKIP


def test_llm_scorer_with_fake():
    fake = FakeLLM(
        json_response={
            "scored": [{"place_id": "p1", "significance": "HIGH", "reason": "x"}],
            "next": "p1",
            "expand_radius": False,
        }
    )
    out = asyncio.run(
        LLMScorer(fake).score(ScorerInput(candidates=[_candidate("p1", "X", "museum", 0.9)]))
    )
    assert out.next == "p1"
    assert out.scored[0].significance is Significance.HIGH


def test_llm_narrator_normalizes_silence_sentinel():
    fake = FakeLLM(text_response="[SILENCE]")
    inp = NarratorInput(
        place=Place(id="p", name="X", category="park", location=GeoPoint(lat=1, lon=2)),
        significance=Significance.LOW,
        distance_m=5,
    )
    assert asyncio.run(LLMNarrator(fake).narrate(inp)) == ""


def test_companion_heuristic_skips_shops():
    out = asyncio.run(
        HeuristicCompanion().respond(CompanionInput(user_message="пропускай магазины"))
    )
    assert out.control_patch is not None
    assert "shop" in out.control_patch.skip_categories


def test_pipeline_walk_offline_no_repeats():
    async def run() -> list[str]:
        provider = StaticPlaceProvider.from_json(FIX / "places_red_square.json")
        discovery = Discovery(provider)
        pipeline = TextPipeline(
            HeuristicScorer(),
            TemplateNarrator(),
            MockEnricher.from_json(FIX / "facts_red_square.json"),
        )
        seen: list[str] = []
        history: list[str] = []
        narrated_places: list[str] = []
        for step in walk(RED_SQUARE, speed_mps=1.3, step_s=8.0):
            result = await discovery.discover_adaptive(step.position, step.heading, seen, 80.0)
            out = await pipeline.step(
                result.candidates, seen=seen, history=history, heading=step.heading
            )
            if out.text and out.place:
                narrated_places.append(out.place.id)
                history.append(out.text)
                seen.append(out.place.id)
        return narrated_places

    narrated = asyncio.run(run())
    # several distinct landmarks, and never the same place twice (dedup holds)
    assert len(narrated) >= 3
    assert len(narrated) == len(set(narrated))


def test_fake_llm_roles_callable():
    fake = FakeLLM(text_response="hi")
    assert asyncio.run(fake.complete_text(Role.NARRATOR, "s", "u")) == "hi"


# -- elaborate (latch onto a place when nothing new is nearby) ----------------
def test_elaborate_flag_bypasses_repeat_guard():
    n = LLMNarrator(FakeLLM(text_response="кстати, ещё одна деталь"))
    place = Place(id="p", name="Парк", category="park", location=GeoPoint(lat=1, lon=2))
    inp = NarratorInput(
        place=place, significance=Significance.MEDIUM, facts="факт", distance_m=0,
        history=["Парк. уже рассказывал про него"],
        flags=NarratorFlags(elaborate=True),
    )
    # name is in HISTORY, but elaborate=True must NOT silence it
    assert asyncio.run(n.narrate(inp)) == "кстати, ещё одна деталь"


def test_repeat_guard_silences_without_elaborate():
    n = LLMNarrator(FakeLLM(text_response="повтор"))
    place = Place(id="p", name="Парк", category="park", location=GeoPoint(lat=1, lon=2))
    inp = NarratorInput(
        place=place, significance=Significance.MEDIUM, facts="факт", distance_m=0,
        history=["Парк. уже рассказывал"], flags=NarratorFlags(),
    )
    assert asyncio.run(n.narrate(inp)) == ""  # repeat guard fires


def test_split_hook_parses_and_strips():
    from app.services.agent.narrator import split_hook

    assert split_hook("Рассказ тут.\nHOOK: дальше к реке") == ("Рассказ тут.", "дальше к реке")
    assert split_hook("Просто текст") == ("Просто текст", None)
    assert split_hook("") == ("", None)
    spoken, hook = split_hook("Начало.\nHOOK: связка\n")
    assert spoken == "Начало." and hook == "связка"
    # inline HOOK (model put it on the SAME line as the last sentence) must still strip
    assert split_hook("…память о прошлом. HOOK: а вот дальше") == (
        "…память о прошлом.", "а вот дальше"
    )
    # the [SILENCE] sentinel must be normalized away even when the model appends a HOOK
    # to it (the leak that made the client TTS literally say "[SILENCE]").
    assert split_hook("[SILENCE]\nHOOK: дальше к реке") == ("", "дальше к реке")
    assert split_hook("[SILENCE] HOOK: дальше") == ("", "дальше")
    assert split_hook("[SILENCE]") == ("", None)


def test_split_hook_strips_localized_hook_label():
    # A4: the model sometimes translates/renames the HOOK label ("Крючок:", "Связка —")
    # on its own trailing line; the ASCII matcher misses it, so it used to be spoken.
    from app.services.agent.narrator import split_hook

    spoken, hook = split_hook("Старый маяк у входа в порт.\nКрючок: к набережной")
    assert spoken == "Старый маяк у входа в порт." and hook == "к набережной"
    spoken, hook = split_hook("Тихая улочка.\nСВЯЗКА — дальше к площади")
    assert spoken == "Тихая улочка." and hook == "дальше к площади"


def test_split_hook_desolicits_listener_offers():
    # A2: narration must never solicit; a trailing question/offer to the listener is
    # stripped (CORE bans it, but the model slips).
    from app.services.agent.narrator import split_hook

    spoken, _ = split_hook("Тут старая мельница. Если хотите, расскажу подробнее.")
    assert spoken == "Тут старая мельница."
    spoken, _ = split_hook("Красивый вид на реку. Хотите узнать больше?")
    assert spoken == "Красивый вид на реку."
    # a legitimate factual sentence with no solicit is untouched
    spoken, _ = split_hook("Мост построили в прошлом веке.")
    assert spoken == "Мост построили в прошлом веке."


def test_split_hook_strips_unverifiable_attributions():
    # A3: unverifiable folk attributions ("старожилы рассказывали", "легенда гласит")
    # are a fabrication tell — drop the sentence.
    from app.services.agent.narrator import split_hook

    spoken, _ = split_hook(
        "Здесь стоит старая усадьба. Местные старожилы рассказывали, что тут был клад."
    )
    assert spoken == "Здесь стоит старая усадьба."
    spoken, _ = split_hook("Тихий двор. Легенда гласит, что тут гулял поэт.")
    assert spoken == "Тихий двор."


def test_split_hook_companion_language_and_english_guards():
    # guards are language-aware; English offers/attributions are also caught.
    from app.services.agent.narrator import split_hook

    spoken, _ = split_hook("An old lighthouse. Want me to tell you more?", "en")
    assert spoken == "An old lighthouse."
    spoken, _ = split_hook("A quiet square. Legend has it a king slept here.", "en")
    assert spoken == "A quiet square."


def test_narrator_user_threads_continue_from_and_in_view():
    # A1: CONTINUE_FROM = last 1-2 spoken paragraphs (positive continuity signal,
    # distinct from HISTORY). A5: in_view is threaded into HEADING.
    import json

    from app.services.agent.prompts import build_narrator_user

    inp = NarratorInput(
        place=Place(id="p", name="Маяк", category="lighthouse",
                    location=GeoPoint(lat=0, lon=0)),
        significance=Significance.MEDIUM,
        distance_m=20.0,
        side="ahead",
        in_view=True,
        history=["Первый абзац.", "Второй абзац.", "Третий абзац."],
    )
    payload = json.loads(build_narrator_user(inp))
    assert payload["CONTINUE_FROM"] == ["Второй абзац.", "Третий абзац."]
    assert payload["HEADING"]["in_view"] is True


def test_area_user_threads_beat_mode_and_continue_from():
    import json

    from app.services.agent.prompts import build_area_user
    from app.shared.schemas import AreaInput

    inp = AreaInput(history=["a", "b", "c"], beat_mode="sensory")
    payload = json.loads(build_area_user(inp))
    assert payload["BEAT_MODE"] == "sensory"
    assert payload["CONTINUE_FROM"] == ["b", "c"]


def test_beat_mode_rotates_distinct_angles():
    from app.services.agent.languages import beat_mode

    modes = [beat_mode(i) for i in range(5)]
    assert len(set(modes)) == 5, "five distinct rhetorical angles in one cycle"
    assert beat_mode(5) == beat_mode(0), "rotation wraps"


def test_narrator_sampling_hotter_than_companion():
    # A1: narration roles get a higher temperature + anti-repetition penalties than the
    # baseline text roles, to break templated openings/connectors.
    from app.services.llm.client import OpenAICompatLLM

    narr = OpenAICompatLLM._sampling_for(Role.NARRATOR)
    comp = OpenAICompatLLM._sampling_for(Role.COMPANION)
    assert narr["temperature"] >= comp["temperature"]
    assert narr.get("frequency_penalty", 0) > 0 and narr.get("presence_penalty", 0) > 0
    assert "frequency_penalty" not in comp  # baseline roles: temperature only


def test_pipeline_step_extracts_next_hook_and_strips_it():
    # the Narrator's trailing HOOK: line must be stripped from speech and surfaced
    # as StepResult.next_hook (the baton woven into the next paragraph).
    narrator = LLMNarrator(FakeLLM(text_response="Старый маяк у входа в порт.\nHOOK: к набережной"))
    pipe = TextPipeline(HeuristicScorer(), narrator, MockEnricher({}))
    cand = _candidate("m", "Маяк", "lighthouse", 0.8)
    out = asyncio.run(pipe.step([cand], seen=[], history=[]))
    assert out.text == "Старый маяк у входа в порт."  # HOOK line gone from speech
    assert out.next_hook == "к набережной"


def test_pipeline_elaborate_uses_cached_facts():
    pipe = TextPipeline(
        HeuristicScorer(),
        LLMNarrator(FakeLLM(text_response=lambda role, system, user: user)),
        MockEnricher({}),
    )
    pipe.cache.put("p", "факт о месте")
    place = Place(id="p", name="Место", category="historic", location=GeoPoint(lat=1, lon=2))
    text = asyncio.run(pipe.elaborate(place, Significance.MEDIUM, history=[]))
    assert "факт о месте" in text  # cached facts reach the narrator


# -- deterministic floor mention (a close object is never dead air) ------------
def test_pipeline_step_floors_silenced_passing_object():
    # DeepSeek sometimes ignores "passing -> never silent"; for a close named object
    # the pipeline must still emit a deterministic one-line mention.
    pipe = TextPipeline(HeuristicScorer(), LLMNarrator(FakeLLM(text_response="[SILENCE]")),
                        MockEnricher({}))
    cand = _candidate("m", "Маяк", "lighthouse", 0.8)
    out = asyncio.run(pipe.step([cand], seen=[], history=[], passing=True))
    assert out.place is not None and out.place.id == "m"
    assert out.text and "Маяк" in out.text  # forced floor mention names the object


def test_pipeline_step_no_floor_when_not_passing():
    pipe = TextPipeline(HeuristicScorer(), LLMNarrator(FakeLLM(text_response="[SILENCE]")),
                        MockEnricher({}))
    cand = _candidate("m", "Маяк", "lighthouse", 0.8)
    out = asyncio.run(pipe.step([cand], seen=[], history=[], passing=False))
    assert out.text == ""  # not passing -> the model's silence stands


def test_pipeline_step_silence_with_hook_never_leaks():
    # Regression: with the HOOK feature on, the model returns "[SILENCE]\nHOOK: ..." for
    # a silent object; normalize() ran before the hook was stripped, so a bare
    # "[SILENCE]" reached the client TTS. The spoken text must be empty, not the sentinel.
    pipe = TextPipeline(
        HeuristicScorer(),
        LLMNarrator(FakeLLM(text_response="[SILENCE]\nHOOK: дальше к реке")),
        MockEnricher({}),
    )
    cand = _candidate("m", "Скамейка", "bench", 0.2)
    out = asyncio.run(pipe.step([cand], seen=[], history=[], passing=False))
    assert out.text == ""  # sentinel normalized away, hook stripped
    assert "SILENCE" not in out.text


def test_pipeline_step_no_floor_when_already_told():
    pipe = TextPipeline(HeuristicScorer(), LLMNarrator(FakeLLM(text_response="[SILENCE]")),
                        MockEnricher({}))
    cand = _candidate("m", "Маяк", "lighthouse", 0.8)
    out = asyncio.run(pipe.step([cand], seen=[], history=["Старый Маяк у входа в порт."],
                                passing=True))
    assert out.text == ""  # already named in history -> no repeat floor mention


def test_pipeline_floors_low_ambient_but_not_low_commercial():
    # B4/P13: a visible ORDINARY ambient place (park/common/hospital...) is named even at
    # LOW significance ("объясни, что я вижу") — but a LOW commercial place stays quiet
    # (no ad-speak). weight 0.4 -> LOW for both; only the category differs.
    pipe = TextPipeline(HeuristicScorer(), LLMNarrator(FakeLLM(text_response="[SILENCE]")),
                        MockEnricher({}))
    ambient = _candidate("a", "Тихий сквер", "common", 0.4)
    out = asyncio.run(pipe.step([ambient], seen=[], history=[], passing=True))
    assert out.text and "Тихий сквер" in out.text  # LOW ambient -> floor names it

    shop = _candidate("s", "Кофейня У Дома", "cafe", 0.3)
    out = asyncio.run(pipe.step([shop], seen=[], history=[], passing=True))
    assert out.text == ""  # LOW commercial -> stays silent (no ad-speak)


def test_visible_rank_prefers_in_cone_without_skipping_much_closer():
    # B2: an object in the gaze cone ranks ahead of one behind at similar distance, but a
    # genuinely much closer object still wins.
    from app.services.agent.orchestrator import Orchestrator

    ahead = _candidate("ahead", "Церковь", "place_of_worship", 0.8, dist=60.0)
    behind = _candidate("behind", "Дом", "building", 0.15, dist=50.0)
    behind = behind.model_copy(update={"in_gaze_cone": False})
    very_close = _candidate("close", "Фонтан", "fountain", 0.45, dist=8.0)
    very_close = very_close.model_copy(update={"in_gaze_cone": False})
    ranked = sorted([behind, ahead, very_close], key=Orchestrator._visible_rank)
    assert ranked[0].place.id == "close"     # much closer wins outright
    assert ranked[1].place.id == "ahead"     # visible-ahead beats the nearer-but-behind


def test_classify_new_discovery_types():
    # B3: the lead's missing object types now classify sensibly (not -> "building" SKIP).
    from app.services.geo.categories import classify

    assert classify({"building": "manor", "name": "X"})[0] == "manor"
    assert classify({"historic": "manor"})[0] == "manor"
    assert classify({"amenity": "school", "name": "X"})[0] == "school"
    assert classify({"amenity": "hospital"})[0] == "hospital"
    assert classify({"club": "motorcycle", "name": "X"})[0] == "club"
    assert classify({"ruins": "stable"})[0] == "ruins"
    assert classify({"building": "stable"})[0] == "farm"
    # all rank above a generic building (0.15) so they actually surface / narrate
    for tags in ({"building": "manor"}, {"amenity": "school"}, {"club": "yes"}):
        assert classify(tags)[1] > classify({"building": "yes"})[1]


def test_pick_name_falls_back_to_localized_tags():
    # B3: an object named only name:ru / int_name used to be dropped as nameless.
    from app.services.geo.providers import _pick_name

    assert _pick_name({"name": "Главное"}) == "Главное"
    assert _pick_name({"name:ru": "Усадьба"}) == "Усадьба"
    assert _pick_name({"int_name": "Manor"}) == "Manor"
    assert _pick_name({"name:fr": "Manoir"}) == "Manoir"
    assert _pick_name({"highway": "residential"}) is None
