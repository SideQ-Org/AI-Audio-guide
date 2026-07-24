"""Reference-free "interestingness" code-metrics panel (Block 4 Part A2).

Deterministic, dependency-light (stdlib only), ~free to run on every narrated blurb.
These are the cheap layer under the LLM judge (interest_judge.py): the judge is the
multilingual anchor, this panel is what lets us drop most judge calls once calibrated.

Design constraints baked in:
- **8 languages.** Concreteness/Flesch-style metrics are English-tuned and are NOT used
  here. Everything below is language-agnostic (n-gram diversity, type-token richness,
  number/among-token density, phrase length) — the parts that need language go to the
  judge. The one language-specific signal is ``cliche_hits`` (reuses the narrator's RU
  blocklist), which simply returns 0 for languages without a blocklist.
- **Non-monotonic axes** (NIDF specificity, and surprisal once added) are returned RAW
  here; the inverted-U transform that rewards the middle lives in interest_score.py
  (Part A4), so this module stays a pure feature extractor.
- **Reuse, don't reinvent:** novelty rides ``is_near_duplicate`` (the same Jaccard/
  containment the walk memory already uses), cliché rides ``_CLICHE_FILLER_MARKERS``.

Heavier ML signals from the design (surprisal via a local distilGPT-2, spaCy NER density)
are deliberately left out of this first cut to keep the quality-worker image light; they
can be layered on behind a flag. Number/date density already approximates NER-for-facts
cheaply, and the judge covers the semantic gaps.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from app.services.agent.narrator import (
    _CLICHE_FILLER_MARKERS,
    _ELEMENTS,
    split_sentences,
)
from app.shared.memory import is_near_duplicate

_WORD_RE = re.compile(r"\w+", re.UNICODE)
# A year (1500-2099) or any standalone number (incl. decimals) — cheap concreteness proxy.
_NUMBER_RE = re.compile(r"\b(?:1[5-9]\d\d|20\d\d|\d+(?:[.,]\d+)?)\b")
_YEAR_RE = re.compile(r"\b(?:1[5-9]\d\d|20\d\d)\b")

# Numbers/dates SPOKEN AS WORDS — this is audio, so the guide says "в тридцатых годах", not
# "1930"; a pure digit regex reads ~0 concreteness on perfectly concrete prose (found on real
# prod walks). Per-language (RU is prod); other languages have no lexicon here and rely on the
# digit regex + the judge's semantic specificity axis. Stems match inflected forms; \b keeps
# "век" from firing inside "человек".
_SPELLED_NUMBER_RE: dict[str, re.Pattern] = {
    "ru": re.compile(
        r"\b(?:двадцат|тридцат|сороков|пятидесят|шестидесят|семидесят|восьмидесят|девяност)\w*\b"
        r"|\bвек[аеуио]?\w*\b|\bстолети\w*\b|\bтысяч\w*\b"
        r"|\b(?:одиннадцат|двенадцат|тринадцат|четырнадцат|пятнадцат|шестнадцат|семнадцат"
        r"|восемнадцат|девятнадцат|десят|сотн|сот)\w*\b",
        re.IGNORECASE | re.UNICODE,
    ),
}


def _spelled_number_hits(text: str, language: str) -> int:
    pat = _SPELLED_NUMBER_RE.get((language or "").split("-")[0].lower())
    return len(pat.findall(text or "")) if pat else 0


# Discourse/spatial connectives a blurb OPENS with when it links smoothly to the walk instead of
# starting a fresh disconnected card ("а вот и…", "чуть дальше…", "напротив…"). Per-language (RU is
# prod); other languages have no lexicon → transition_rate returns 0 and leans on the walk judge.
# Matched at the blurb start (after stripping quotes/spaces), case-insensitive.
_TRANSITION_MARKERS: dict[str, tuple[str, ...]] = {
    "ru": (
        "а вот", "а вон", "а здесь", "а тут", "вот и", "вот здесь", "чуть дальше", "чуть впереди",
        "чуть правее", "чуть левее", "напротив", "рядом", "здесь же", "тут же", "по соседству",
        "впереди", "справа", "слева", "перед нами", "перед вами", "за спиной", "позади",
        "тем временем", "кстати", "к слову", "а ещё", "а еще", "как и", "как та", "как тот",
        "как и та", "как и тот", "неподалёку", "неподалеку", "дальше по", "а прямо",
    ),
}

# Back-reference markers: a blurb that CALLS BACK to something told earlier ("как та церковь ранее",
# "тот самый мост", "мы уже видели…"). Paired with an earlier-object category match in callbacks.
_CALLBACK_MARKERS: dict[str, tuple[str, ...]] = {
    "ru": (
        "как та", "как тот", "как и та", "как и тот", "как и эта", "как и этот", "тот самый",
        "та самая", "то самое", "мы уже", "уже видели", "уже проходили", "как ранее", "ранее",
        "помнишь", "помните", "как и прежд", "как прежде", "снова", "опять же", "как у",
    ),
}


def _lex_for(mapping: dict[str, tuple[str, ...]], language: str) -> tuple[str, ...]:
    return mapping.get((language or "").split("-")[0].lower(), ())


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower())


def _ngrams(tokens: list[str], n: int) -> list[tuple[str, ...]]:
    if len(tokens) < n:
        return []
    return [tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]


# --------------------------------------------------------------------------- #
# corpus-level diversity (across the whole walk) — "опять про берёзы"
# --------------------------------------------------------------------------- #
def distinct_n(texts: list[str], n: int = 2) -> float:
    """Fraction of DISTINCT n-grams across the corpus (Li et al. 2016). 1.0 = every
    n-gram unique, →0 = highly repetitive. Returns 1.0 for an empty/too-short corpus
    (nothing repeated yet)."""
    grams: list[tuple[str, ...]] = []
    for t in texts:
        grams.extend(_ngrams(_tokens(t), n))
    if not grams:
        return 1.0
    return len(set(grams)) / len(grams)


def self_repetition(texts: list[str], n: int = 3) -> float:
    """Mean pairwise n-gram overlap between blurbs (a cheap self-BLEU stand-in). Higher =
    the walk keeps rewording the same thing. 0.0 for <2 blurbs. This is a PENALTY axis
    (bigger is worse), returned raw for interest_score to weight."""
    grams = [set(_ngrams(_tokens(t), n)) for t in texts]
    grams = [g for g in grams if g]
    if len(grams) < 2:
        return 0.0
    total = 0.0
    pairs = 0
    for i in range(len(grams)):
        for j in range(i + 1, len(grams)):
            union = grams[i] | grams[j]
            if union:
                total += len(grams[i] & grams[j]) / len(union)
                pairs += 1
    return total / pairs if pairs else 0.0


# --------------------------------------------------------------------------- #
# cross-object coherence (across CONSECUTIVE blurbs) — "seamlessness / связность / arc"
# These score the WALK as a connected narrative, not each blurb in isolation. All
# reference-free; RU lexicons now (other languages fall back to the walk-level judge).
# --------------------------------------------------------------------------- #
# A blurb often opens with a bare conjunction before the real connective ("А чуть дальше…",
# "И вот…") — strip one leading conjunction so the marker match still fires.
_LEAD_CONJ = ("а ", "и ", "но ", "да ", "ну ", "вот ")


def _opens_with(text: str, markers: tuple[str, ...]) -> bool:
    """True if the blurb's first sentence starts with one of ``markers`` (quotes/space-stripped),
    tolerating a single leading conjunction ("А чуть дальше…" matches "чуть дальше")."""
    head = (text or "").lstrip("\"'«»„“ \t\n—-–").lower()
    heads = [head]
    for c in _LEAD_CONJ:
        if head.startswith(c):
            heads.append(head[len(c):])
            break
    return any(h.startswith(m) for h in heads for m in markers)


def transition_rate(texts: list[str], language: str = "ru") -> float:
    """Fraction of blurbs (after the first) that OPEN with a spatial/discourse connective — a cheap
    proxy for smooth linking ("а вот и…", "чуть дальше…") vs an abrupt fresh card. 0.0 for <2 blurbs
    or a language with no lexicon. This is a MILD positive; capped downstream so connective-spam
    (which the cliché/speakability gates catch) can't run it up."""
    markers = _lex_for(_TRANSITION_MARKERS, language)
    if not markers or len(texts) < 2:
        return 0.0
    hits = sum(1 for t in texts[1:] if _opens_with(t, markers))
    return hits / (len(texts) - 1)


def _content_tokens(text: str) -> set[str]:
    """Content words for adjacency overlap: length-≥4 tokens (a language-agnostic stopword proxy —
    RU/EN function words are mostly short), lowercased."""
    return {w for w in _tokens(text) if len(w) >= 4}


def adjacent_cohesion(texts: list[str]) -> float:
    """Mean Jaccard of CONTENT tokens between CONSECUTIVE blurbs — the connective tissue of a theme.
    Returned RAW (non-monotonic): interest_score applies the inverted-U so a moderate shared thread
    is rewarded while 0 (disjoint cards) and ~1 (rewording the same thing) are both penalised. 0.0
    for <2 blurbs."""
    sets = [_content_tokens(t) for t in texts]
    pairs = [(sets[i], sets[i + 1]) for i in range(len(sets) - 1) if sets[i] and sets[i + 1]]
    if not pairs:
        return 0.0
    total = 0.0
    for a, b in pairs:
        union = a | b
        total += len(a & b) / len(union) if union else 0.0
    return total / len(pairs)


def callback_rate(texts: list[str], categories: list[str | None]) -> float:
    """Fraction of blurbs (after the first) that CALL BACK to an earlier object of the same category
    AND carry a back-reference marker ("как та церковь ранее", "тот самый мост") — the integration /
    arc signal the director's callbacks aim for. Needs per-blurb ``categories`` aligned to ``texts``
    (present in the worker). 0.0 without categories, a lexicon, or <2 blurbs."""
    lang_markers = _CALLBACK_MARKERS.get("ru")  # RU-only for now; matches the lexicons above
    if not lang_markers or len(texts) < 2 or not categories:
        return 0.0
    seen_cats: set[str] = set()
    total = hits = 0
    for text, cat in zip(texts, categories, strict=False):
        low = (text or "").lower()
        if total >= 1:  # only blurbs with a prior object can call back
            if cat and cat in seen_cats and any(m in low for m in lang_markers):
                hits += 1
        if cat:
            seen_cats.add(cat)
        total += 1
    denom = max(1, total - 1)
    return hits / denom


# --------------------------------------------------------------------------- #
# within-blurb richness / specificity
# --------------------------------------------------------------------------- #
def mtld(text: str, threshold: float = 0.72) -> float:
    """Measure of Textual Lexical Diversity (McCarthy & Jarvis) — robust to length unlike
    raw TTR. Averaged forward+backward. Returns the token count for very short texts (no
    factor completed). Higher = richer vocabulary within the blurb."""
    toks = _tokens(text)
    if len(toks) < 10:
        return float(len(set(toks)))

    def _factors(seq: list[str]) -> float:
        factors = 0.0
        types: set[str] = set()
        count = 0
        for w in seq:
            types.add(w)
            count += 1
            ttr = len(types) / count
            if ttr <= threshold:
                factors += 1
                types, count = set(), 0
        if count > 0:  # partial trailing factor
            ttr = len(types) / count
            factors += (1 - ttr) / (1 - threshold)
        return factors or 1.0

    fwd = len(toks) / _factors(toks)
    bwd = len(toks) / _factors(list(reversed(toks)))
    return (fwd + bwd) / 2


def build_idf(corpus: list[str]) -> dict[str, float]:
    """IDF table over a reference corpus: ``log(R / (1 + df))``. Used by ``nidf`` to tell a
    generic label ("парк, тут гуляют") from a rare, concrete term. Build once per corpus."""
    docs = [set(_tokens(t)) for t in corpus if t]
    r = len(docs) or 1
    df: Counter[str] = Counter()
    for d in docs:
        df.update(d)
    return {w: math.log(r / (1 + c)) for w, c in df.items()}


def nidf_specificity(text: str, idf: dict[str, float], cap_percentile: float = 0.95) -> float:
    """Mean IDF of the blurb's tokens, normalised to 0-1 against the corpus IDF range, with
    the top capped so a single junk-rare token can't spike it (See et al. 2019). NON-
    MONOTONIC in spirit (extreme rarity ≠ good) — returned RAW; interest_score applies the
    inverted-U. 0.0 when the IDF table is empty."""
    if not idf:
        return 0.0
    toks = [w for w in _tokens(text) if w in idf]
    if not toks:
        return 0.0
    vals = sorted(idf.values())
    lo = vals[0]
    hi = vals[min(len(vals) - 1, int(len(vals) * cap_percentile))]
    span = (hi - lo) or 1.0
    mean_idf = sum(idf[w] for w in toks) / len(toks)
    return max(0.0, min(1.0, (mean_idf - lo) / span))


def number_density(text: str, language: str = "ru") -> float:
    """Numbers + dates per token — a concreteness/facts proxy (≈ NER density for what matters
    to us). Counts BOTH digits AND spoken-as-words dates/eras ("в тридцатых годах", "в XIX
    веке") via a per-language lexicon — critical because this is audio and the guide speaks
    numbers as words, so a digit-only count reads ~0 on concrete prose. NOTE: rewards INVENTED
    specifics too, so it is only trustworthy paired with the groundedness gate."""
    toks = _tokens(text)
    if not toks:
        return 0.0
    hits = len(_NUMBER_RE.findall(text or "")) + _spelled_number_hits(text, language)
    return hits / len(toks)


# --------------------------------------------------------------------------- #
# audio suitability
# --------------------------------------------------------------------------- #
def speakability(text: str, long_sentence_words: int = 24) -> float:
    """1.0 = easy to say aloud, →0 = long winding sentences hard to follow in audio.
    Language-agnostic: mean words/sentence + a penalty for any over-long sentence. (No
    syllable counting — that is English-tuned.)"""
    sents = [s for s in split_sentences(text or "") if s.strip()]
    if not sents:
        return 0.0
    lengths = [len(_tokens(s)) for s in sents]
    mean_len = sum(lengths) / len(lengths)
    # comfortable audio phrase ≈ 12 words; degrade toward 0 by ~28 words.
    len_score = max(0.0, min(1.0, 1 - (mean_len - 12) / 16))
    over = sum(1 for n in lengths if n > long_sentence_words) / len(lengths)
    return max(0.0, len_score * (1 - 0.5 * over))


# --------------------------------------------------------------------------- #
# invariant guards (cliché / novelty) — feed the hard-gates in interest_score
# --------------------------------------------------------------------------- #
def cliche_hits(text: str, language: str) -> int:
    """Count empty-poetic-filler markers (reuses the narrator's RU blocklist) plus abstract
    elemental clusters (≥3 element words in one date-less sentence). 0 for languages with no
    blocklist — the judge covers those. A PENALTY signal (also a hard-gate input)."""
    markers = _CLICHE_FILLER_MARKERS.get((language or "").split("-")[0].lower())
    if not markers or not text:
        return 0
    hits = 0
    for s in split_sentences(text):
        low = s.lower()
        hits += sum(1 for m in markers if m in low)
        if not _YEAR_RE.search(s) and sum(1 for e in _ELEMENTS if e in low) >= 3:
            hits += 1
    return hits


def novelty_vs_corpus(text: str, prior: list[str], *, threshold: float = 0.82) -> float:
    """1.0 = this blurb is fresh against everything told so far, 0.0 = a near-duplicate
    (rides ``is_near_duplicate`` — the same Jaccard/containment the walk memory uses)."""
    if not text or not prior:
        return 1.0
    return 0.0 if is_near_duplicate(text, prior, threshold=threshold) else 1.0


# --------------------------------------------------------------------------- #
# panel assembly
# --------------------------------------------------------------------------- #
@dataclass
class BlurbMetrics:
    """Per-blurb feature vector. Positive axes (bigger better): specificity, number_density,
    speakability, novelty, mtld. Penalty axes (bigger worse): self_repetition, cliche_hits.
    Non-monotonic (raw here): specificity. interest_score normalises + weights + gates."""

    specificity: float      # nidf, raw (inverted-U applied downstream)
    number_density: float
    speakability: float
    novelty: float
    mtld: float
    cliche_hits: int


def score_blurb(
    text: str,
    *,
    prior: list[str],
    idf: dict[str, float],
    language: str,
) -> BlurbMetrics:
    """Extract the per-blurb code-metric vector. ``prior`` = blurbs told earlier this walk
    (for novelty), ``idf`` = a ``build_idf`` table over the reference corpus."""
    return BlurbMetrics(
        specificity=nidf_specificity(text, idf),
        number_density=number_density(text, language),
        speakability=speakability(text),
        novelty=novelty_vs_corpus(text, prior),
        mtld=mtld(text),
        cliche_hits=cliche_hits(text, language),
    )


def object_repeat_rate(place_ids: list[str | None]) -> float:
    """Fraction of blurbs whose OBJECT was already narrated earlier this walk — catches
    "опять про руины" independent of wording, which lexical novelty (reworded ⇒ low Jaccard)
    misses. Uses place identity (id/name), the same signal ``WalkMemory.objects`` tracks. 0.0
    when nothing repeats. Ambient/unnamed blurbs (``None``) are ignored (area beats, etc.)."""
    seen: set[str] = set()
    total = repeats = 0
    for pid in place_ids:
        if not pid:
            continue
        total += 1
        if pid in seen:
            repeats += 1
        else:
            seen.add(pid)
    return repeats / total if total else 0.0


@dataclass
class CorpusMetrics:
    """Whole-walk metrics: lexical diversity + repetition + object-level repetition + a
    silence rate the caller supplies (from walklog counters — "шёл и молчал"), plus the
    cross-object coherence signals (transitions / adjacency / callbacks)."""

    distinct_1: float
    distinct_2: float
    distinct_3: float
    self_repetition: float
    silence_rate: float
    object_repeat_rate: float = 0.0
    # cross-object coherence (raw; interest_score.walk_coherence blends/transforms these)
    transition_rate: float = 0.0
    adjacent_cohesion: float = 0.0
    callback_rate: float = 0.0


def score_corpus(
    texts: list[str],
    *,
    silence_rate: float = 0.0,
    place_ids: list[str | None] | None = None,
    categories: list[str | None] | None = None,
    language: str = "ru",
) -> CorpusMetrics:
    """Whole-walk diversity/repetition + coherence. ``silence_rate`` is passed in (ticks with no
    narration ÷ total ticks). ``place_ids`` (per-blurb object identity, aligned to ``texts``)
    enables object-level repeat detection; ``categories`` (aligned to ``texts``) enables the
    callback signal. Omit either for text-only corpora. Coherence is computed over the given
    ``texts`` — pass only NON-silent blurbs so silence can't masquerade as smoothness."""
    return CorpusMetrics(
        distinct_1=distinct_n(texts, 1),
        distinct_2=distinct_n(texts, 2),
        distinct_3=distinct_n(texts, 3),
        self_repetition=self_repetition(texts),
        silence_rate=max(0.0, min(1.0, silence_rate)),
        object_repeat_rate=object_repeat_rate(place_ids) if place_ids else 0.0,
        transition_rate=transition_rate(texts, language),
        adjacent_cohesion=adjacent_cohesion(texts),
        callback_rate=callback_rate(texts, categories) if categories else 0.0,
    )


# --------------------------------------------------------------------------- #
# fact ranking (feed the narrator the MOST interesting facts first)
# --------------------------------------------------------------------------- #
def fact_interest(fact: str, language: str = "ru") -> float:
    """A cheap, deterministic interestingness score for ONE atomic fact. Reuses the
    panel's signals: concreteness (numbers/dates incl. spoken-as-words), proper names,
    specific vocabulary — minus cliché filler and unspeakable length. Used to ORDER
    facts so the narrator builds a blurb around the best material instead of whatever
    sentence the source happened to put first."""
    f = (fact or "").strip()
    if not f:
        return -1.0
    toks = _tokens(f)
    if not toks:
        return -1.0
    score = 0.0
    # Concreteness: dates/numbers (spoken forms included) are the strongest signal.
    score += 3.0 * min(number_density(f, language), 0.34)
    # Proper names mid-sentence (people/places) — capitalized tokens beyond the first.
    words = f.split()
    proper = sum(
        1 for w in words[1:] if w[:1].isupper() and not w.isupper() and len(w) > 2
    )
    score += 0.35 * min(proper, 3)
    # Specific vocabulary: longer average token ≈ terms, names, materials (cheap NIDF
    # stand-in that needs no corpus).
    avg_len = sum(len(t) for t in toks) / len(toks)
    score += max(0.0, min(0.5, (avg_len - 5.0) * 0.15))
    # Penalties: cliché filler and hard-to-speak length.
    score -= 0.8 * cliche_hits(f, language)
    if len(toks) > 30:
        score -= 0.4
    if len(f) < 25:
        score -= 0.5  # too thin to build a story around
    return score


def rank_facts(
    facts: list[str], language: str = "ru", *, top_k: int | None = None
) -> list[str]:
    """Order atomic facts by interestingness, best first (stable for equal scores).
    ``top_k`` caps the list — the narrator works best with a few strong facts, not a
    wall of mediocre ones."""
    ranked = sorted(
        facts, key=lambda f: fact_interest(f, language), reverse=True
    )
    return ranked[:top_k] if top_k else ranked
