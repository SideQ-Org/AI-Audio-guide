"""Unit tests for the reference-free interestingness code-metrics panel (Block 4 A2).

Pure/offline — no network, no keys, no LLM. Part of the regression gate.
"""

from __future__ import annotations

from app.services.agent.interest_metrics import (
    BlurbMetrics,
    CorpusMetrics,
    build_idf,
    cliche_hits,
    distinct_n,
    mtld,
    nidf_specificity,
    novelty_vs_corpus,
    number_density,
    object_repeat_rate,
    score_blurb,
    score_corpus,
    self_repetition,
    speakability,
)


def test_distinct_n_rewards_variety_penalises_repetition():
    same = ["берёзы у реки", "берёзы у реки", "берёзы у реки"]
    varied = ["старый маяк на мысу", "чугунный мост через канал", "усадьба князей Голицыных"]
    assert distinct_n(same, 2) < 0.5
    assert distinct_n(varied, 2) > 0.9
    assert distinct_n([], 2) == 1.0  # empty corpus: nothing repeated


def test_self_repetition_high_for_reworded_repeats():
    reworded = [
        "здесь растут старые берёзы вдоль тихой реки",
        "старые берёзы растут здесь вдоль тихой реки",
    ]
    distinct = [
        "маяк построили в тысяча девятьсот десятом году",
        "мост возвели пленные инженеры совсем в другую эпоху",
    ]
    assert self_repetition(reworded) > self_repetition(distinct)
    assert self_repetition(["один текст"]) == 0.0  # need >=2 blurbs


def test_mtld_richer_text_scores_higher():
    poor = "место место место тут тут гуляют гуляют люди люди тут место"
    rich = "маяк чугунный мост усадьба канал верфь причал колокольня застава арсенал"
    assert mtld(rich) > mtld(poor)


def test_nidf_specificity_in_range_and_rewards_rare_terms():
    corpus = [
        "парк тут гуляют люди",
        "парк тут гуляют собаки",
        "парк тут отдыхают",
        "верфь построила первый броненосец балтийского флота",
    ]
    idf = build_idf(corpus)
    generic = nidf_specificity("парк тут гуляют", idf)
    rare = nidf_specificity("верфь броненосец балтийского флота", idf)
    assert 0.0 <= generic <= 1.0
    assert 0.0 <= rare <= 1.0
    assert rare > generic
    assert nidf_specificity("что угодно", {}) == 0.0  # no table -> 0


def test_number_density_rewards_dates_and_numbers():
    with_dates = "башню возвели в тысяча девятьсот пятом, высота 47 метров"
    plain = "красивое место где приятно побыть немного"
    assert number_density("построена в 1905 году, 12 залов") > 0
    assert number_density(with_dates) >= number_density(plain)
    assert number_density("") == 0.0


def test_number_density_counts_spelled_out_dates():
    # audio: the guide says dates as words, not digits — these must register as concreteness
    assert number_density("в тридцатых годах здесь строили дачи", "ru") > 0
    assert number_density("памятник поставили в девятнадцатом веке", "ru") > 0
    # a lyrical, date-less sentence stays near zero
    lyrical = "тихое место где приятно гулять и дышать свежим воздухом"
    dated = "в тридцатых годах здесь строили дачи"
    assert number_density(dated, "ru") > number_density(lyrical, "ru")
    # a language without a lexicon falls back to the digit regex (no crash)
    assert number_density("built in the thirties", "en") == 0.0


def test_object_repeat_rate_catches_reworded_repeats():
    # same object narrated 3x (different wording), plus one other; area beats are None
    assert object_repeat_rate(["ruins", "ruins", "ruins", "park", None, None]) == 0.5
    assert object_repeat_rate(["a", "b", "c"]) == 0.0
    assert object_repeat_rate([None, None]) == 0.0
    assert object_repeat_rate([]) == 0.0


def test_speakability_prefers_short_sentences():
    short = "Маяк на мысу. Ему больше ста лет. Свет виден за тридцать миль."
    winding = (
        "Этот маяк, который был построен давным-давно на самом краю каменистого мыса "
        "выступающего далеко в холодное море, до сих пор каждую ночь без единого "
        "пропуска зажигает свой яркий свет чтобы корабли идущие издалека не разбились."
    )
    assert speakability(short) > speakability(winding)
    assert speakability("") == 0.0


def test_cliche_hits_catches_ru_filler_and_ignores_other_langs():
    assert cliche_hits("здесь время застыло, всё дышит историей", "ru") >= 2
    # abstract elemental cluster (>=3 element words, no date):
    assert cliche_hits("вода, воздух, огонь и камень сплетаются тут", "ru") >= 1
    assert cliche_hits("маяк построили в 1910 году", "ru") == 0
    assert cliche_hits("time stands still here", "en") == 0  # no blocklist -> judge handles it


def test_novelty_vs_corpus_flags_near_duplicates():
    prior = ["здесь растут старые берёзы вдоль тихой реки за оградой парка"]
    dup = "старые берёзы растут здесь вдоль тихой реки за оградой парка"
    fresh = "маяк построили в тысяча девятьсот десятом году на дальнем мысу"
    assert novelty_vs_corpus(dup, prior) == 0.0
    assert novelty_vs_corpus(fresh, prior) == 1.0
    assert novelty_vs_corpus("что угодно", []) == 1.0  # nothing to repeat yet


def test_score_blurb_and_corpus_shapes():
    corpus = ["маяк на мысу", "мост через канал", "усадьба у реки"]
    idf = build_idf(corpus)
    bm = score_blurb(
        "Маяк построили в 1910 году. Его свет виден за тридцать миль.",
        prior=corpus, idf=idf, language="ru",
    )
    assert isinstance(bm, BlurbMetrics)
    assert 0.0 <= bm.speakability <= 1.0
    assert 0.0 <= bm.novelty <= 1.0
    assert bm.number_density > 0
    assert bm.cliche_hits == 0

    cm = score_corpus(corpus, silence_rate=0.25, place_ids=["a", "a", "b"])
    assert isinstance(cm, CorpusMetrics)
    assert 0.0 <= cm.distinct_2 <= 1.0
    assert cm.silence_rate == 0.25
    assert abs(cm.object_repeat_rate - 1 / 3) < 1e-9  # 'a' repeats once out of 3 named
