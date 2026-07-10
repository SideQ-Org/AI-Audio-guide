"""Supported guide languages — single source of truth (backend side).

Codes are ISO-639-1. faster-whisper accepts these directly (incl. ``zh``), so
STT needs no mapping. The LLM prompt wants a human-readable language name, so
``prompt_language`` maps code -> name for the ``{language}`` placeholder in
``prompts/core.txt``. The client owns the code -> BCP-47 mapping for TTS.
"""

from __future__ import annotations

# code -> name injected into the CORE prompt's "{language}" placeholder.
PROMPT_NAME: dict[str, str] = {
    "en": "English",
    "ru": "русском (Russian)",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "zh": "Chinese",
}

SUPPORTED: frozenset[str] = frozenset(PROMPT_NAME)
FALLBACK = "en"


def normalize(code: str | None) -> str:
    """Map an arbitrary locale/code to a supported code, else fall back to EN."""
    if not code:
        return FALLBACK
    short = code.replace("_", "-").split("-", 1)[0].lower()
    return short if short in SUPPORTED else FALLBACK


def prompt_language(code: str | None) -> str:
    """Human-readable language name for the narration prompt."""
    return PROMPT_NAME[normalize(code)]


# Practical Russian Cyrillic -> Latin romanization. Used as the last-resort title for
# a non-Russian session when OSM has no exonym, so a minor object like "Звонница" is
# shown as "Zvonnitsa" (how the narrator already pronounces it) instead of raw Cyrillic.
_CYR_LAT: dict[str, str] = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    # a few non-Russian Cyrillic letters so Ukrainian/Serbian names degrade gracefully
    "і": "i", "ї": "yi", "є": "ye", "ґ": "g", "ђ": "dj", "ј": "j", "љ": "lj",
    "њ": "nj", "ћ": "c", "џ": "dz", "ў": "u",
}


def _has_cyrillic(s: str) -> bool:
    return any("Ѐ" <= ch <= "ӿ" for ch in s)


def transliterate(s: str) -> str:
    """Romanize Cyrillic to Latin; non-Cyrillic chars pass through unchanged. Source
    case is preserved (an uppercase letter capitalizes its multi-char romanization)."""
    out: list[str] = []
    for ch in s:
        low = ch.lower()
        rep = _CYR_LAT.get(low)
        if rep is None:
            out.append(ch)  # Latin, digits, spaces, punctuation
        elif ch == low or not rep:
            out.append(rep)
        else:
            out.append(rep[0].upper() + rep[1:])  # uppercase source -> "Shch", "Ya"
    return "".join(out)


def display_name(tags: dict[str, str], fallback: str, code: str | None) -> str:
    """Localized display name for a POI / place.

    The raw OSM ``name`` tag is in the LOCAL language (Russian in Moscow, Greek in
    Athens, ...), so a walker who picked English would otherwise see/hear Cyrillic
    titles. Resolution:

    1. ``name:<session-lang>`` — an exact match in the chosen language is always best.
    2. Otherwise the raw local ``name`` for a **Russian** session: Russia is the
       primary deployment region, where the raw tag is already Russian, so a RU
       walker must keep the authentic Cyrillic name (never the English exonym).
    3. For any other (international) session: the English exonym ``name:en``, then the
       official ``int_name``; failing both, a Cyrillic raw name is **romanized** to
       Latin so the title is readable and matches the spoken narration (the narrator
       transliterates proper names anyway). A name already in Latin is kept as-is.

    The ``name:<lang>`` / ``int_name`` tags must be kept on the Place (see ``KEEP_TAGS``
    in geo/categories.py). Compare geocoder ``_name``, which localizes street/city names."""
    lang = normalize(code)
    exact = tags.get(f"name:{lang}")
    if exact:
        return exact
    if lang == "ru":
        return fallback  # raw tag is the authentic Russian name in the home region
    chosen = tags.get("name:en") or tags.get("int_name") or fallback
    return transliterate(chosen) if _has_cyrillic(chosen) else chosen


# --- Spoken-verbatim strings ------------------------------------------------- #
# These reach the user WITHOUT passing through the LLM (the narrator re-expresses
# facts in {language}, but these are emitted as-is), so they MUST be localized.

# Short bridges said when the area material is exhausted and nothing is nearby:
# one is spoken, then the guide goes genuinely quiet. Per language, fallback EN.
_BRIDGES: dict[str, tuple[str, ...]] = {
    "ru": (
        "Идём дальше.",
        "Пройдём дальше, тут пока тихо.",
        "Двигаемся дальше.",
        "Здесь пока тихо, идём дальше.",
    ),
    "en": (
        "Let's move on.",
        "Let's walk on — it's quiet here for now.",
        "Onward.",
        "Quiet here for now — let's keep walking.",
    ),
    "es": (
        "Sigamos.",
        "Sigamos caminando, por ahora hay poco que contar.",
        "Avancemos.",
        "Por aquí está tranquilo, sigamos.",
    ),
    "fr": (
        "Continuons.",
        "Avançons, c'est calme par ici pour l'instant.",
        "Poursuivons.",
        "C'est calme par ici, continuons.",
    ),
    "de": (
        "Gehen wir weiter.",
        "Gehen wir weiter, hier ist es gerade ruhig.",
        "Weiter geht's.",
        "Hier ist es gerade ruhig, gehen wir weiter.",
    ),
    "it": (
        "Andiamo avanti.",
        "Proseguiamo, qui per ora è tranquillo.",
        "Continuiamo.",
        "Qui è tranquillo, proseguiamo.",
    ),
    "pt": (
        "Vamos seguindo.",
        "Vamos em frente, por aqui está calmo por agora.",
        "Seguimos.",
        "Está calmo por aqui, vamos seguindo.",
    ),
    "zh": (
        "我们继续走吧。",
        "继续走吧，这里暂时没什么可说的。",
        "往前走。",
        "这里暂时很安静，我们继续走吧。",
    ),
}

# Shown to the user (as a transient toast) when speech wasn't intelligible.
_STT_UNCLEAR: dict[str, str] = {
    "ru": "Не расслышал — повтори, пожалуйста.",
    "en": "Didn't catch that — please say it again.",
    "es": "No te he entendido, ¿puedes repetir?",
    "fr": "Je n'ai pas bien entendu, peux-tu répéter ?",
    "de": "Das habe ich nicht verstanden — bitte noch einmal.",
    "it": "Non ho capito bene, puoi ripetere?",
    "pt": "Não entendi bem — pode repetir, por favor?",
    "zh": "没有听清，请再说一遍。",
}


# Deterministic one-line "you're passing X" mention, emitted AS-IS (no LLM) as a
# guaranteed floor when the model wrongly returns silence for a close, named object
# the walker is right beside. SIDE is used only when known: left/right arrive only at
# high gaze confidence; ahead/behind are knowable from the GPS course; else "near".
_PASSING_MENTION: dict[str, dict[str, str]] = {
    "ru": {
        "left": "Слева — {name}.",
        "right": "Справа — {name}.",
        "ahead": "Прямо по курсу — {name}.",
        "behind": "Ты только что прошёл {name}.",
        "near": "Тут рядом — {name}.",
    },
    "en": {
        "left": "On your left — {name}.",
        "right": "On your right — {name}.",
        "ahead": "Just ahead — {name}.",
        "behind": "You just passed {name}.",
        "near": "Right here — {name}.",
    },
    "es": {
        "left": "A tu izquierda — {name}.",
        "right": "A tu derecha — {name}.",
        "ahead": "Justo delante — {name}.",
        "behind": "Acabas de pasar {name}.",
        "near": "Aquí al lado — {name}.",
    },
    "fr": {
        "left": "Sur ta gauche — {name}.",
        "right": "Sur ta droite — {name}.",
        "ahead": "Droit devant — {name}.",
        "behind": "Tu viens de passer {name}.",
        "near": "Juste ici — {name}.",
    },
    "de": {
        "left": "Links — {name}.",
        "right": "Rechts — {name}.",
        "ahead": "Direkt voraus — {name}.",
        "behind": "Du bist gerade an {name} vorbei.",
        "near": "Hier nebenan — {name}.",
    },
    "it": {
        "left": "Alla tua sinistra — {name}.",
        "right": "Alla tua destra — {name}.",
        "ahead": "Proprio davanti — {name}.",
        "behind": "Hai appena passato {name}.",
        "near": "Qui accanto — {name}.",
    },
    "pt": {
        "left": "À tua esquerda — {name}.",
        "right": "À tua direita — {name}.",
        "ahead": "Logo à frente — {name}.",
        "behind": "Acabaste de passar {name}.",
        "near": "Aqui ao lado — {name}.",
    },
    "zh": {
        "left": "左边是{name}。",
        "right": "右边是{name}。",
        "ahead": "正前方是{name}。",
        "behind": "你刚经过{name}。",
        "near": "旁边就是{name}。",
    },
}


def passing_mention(code: str | None, name: str, side: str | None) -> str:
    """A deterministic, localized one-line mention of an object the walker is passing.
    Emitted verbatim (no LLM) so a close named object is never dead air, even when the
    model silences it. SIDE keys: left|right|ahead|behind, else a neutral 'near'."""
    table = _PASSING_MENTION.get(normalize(code), _PASSING_MENTION[FALLBACK])
    key = side if side in ("left", "right", "ahead", "behind") else "near"
    return table[key].format(name=name)


def bridges(code: str | None) -> tuple[str, ...]:
    """Spoken-verbatim 'let's move on' bridges in the session language."""
    return _BRIDGES.get(normalize(code), _BRIDGES[FALLBACK])


def stt_unclear(code: str | None) -> str:
    """'Didn't catch that' message in the session language."""
    return _STT_UNCLEAR.get(normalize(code), _STT_UNCLEAR[FALLBACK])


# --- Model-facing cascade strings (steer the LLM; output language is governed by
# core.txt's {language}, so English steering is enough for every non-RU session).
# Russian is kept byte-identical to the original so the tuned RU flow is unchanged.

_LEVEL_LABELS_EN = ("city", "district", "street")
_LEVEL_LABELS: dict[str, tuple[str, str, str]] = {
    "ru": ("город", "район", "улицу"),
}

_AREA_TOPIC_EN = (
    "another non-obvious, atypical fact about the {label} {name} — something "
    "people usually don't know; no platitudes and no repeats"
)
_AREA_TOPIC: dict[str, str] = {
    "ru": (
        "ещё один неочевидный, нетипичный факт про {label} {name} — "
        "то, чего обычно не знают; без банальностей и без повторов"
    ),
}

_STREET_HOOK_EN = "stepping onto {street}"
_STREET_HOOK: dict[str, str] = {
    "ru": "переход на улицу {street}",
}

_AREA_INTRO_TOLD_EN = "area intro"
_AREA_INTRO_TOLD: dict[str, str] = {
    "ru": "вступление в район",
}


def level_labels(code: str | None) -> tuple[str, str, str]:
    """(city, district, street) labels for the cascade, in the session language."""
    return _LEVEL_LABELS.get(normalize(code), _LEVEL_LABELS_EN)


def area_topic(code: str | None, label: str, name: str) -> str:
    """One cascade-beat instruction for the area narrator."""
    tmpl = _AREA_TOPIC.get(normalize(code), _AREA_TOPIC_EN)
    return tmpl.format(label=label, name=name)


def street_hook(code: str | None, street: str) -> str:
    """next_hook baton woven in when the walker steps onto a new street."""
    return _STREET_HOOK.get(normalize(code), _STREET_HOOK_EN).format(street=street)


def area_intro_told(code: str | None) -> str:
    """Internal 'told' ledger marker for the area opener."""
    return _AREA_INTRO_TOLD.get(normalize(code), _AREA_INTRO_TOLD_EN)


# --- Code-level narration guards (backstops over the prompt, which models disobey) --
# Lowercased substrings that mark a listener-directed offer/solicitation ("if you want,
# I'll tell you more"). A trailing sentence containing one — or ending in a question mark
# — is stripped from narration (never from Companion replies). CORE bans these, but the
# model still slips; this is the deterministic net, like the [SILENCE]/HOOK guards.
_SOLICIT_MARKERS: dict[str, tuple[str, ...]] = {
    "ru": (
        "если хотите", "если хочешь", "хотите, расскажу", "хочешь, расскажу",
        "хотите узнать", "хочешь узнать", "расскажу подробнее, если", "давайте ",
    ),
    "en": (
        "if you want", "if you'd like", "if you like", "shall i", "want me to",
        "let me tell you more", "would you like",
    ),
    "es": ("si quieres", "si quiere", "quieres que", "quiere que"),
    "fr": ("si tu veux", "si vous voulez", "veux-tu que", "voulez-vous"),
    "de": ("wenn du willst", "wenn sie wollen", "soll ich", "möchtest du"),
    "it": ("se vuoi", "se vuole", "vuoi che", "vuole che"),
    "pt": ("se quiser", "se você quiser", "quer que"),
    "zh": ("如果你想", "想听", "要我", "要不要"),
}

# Lowercased substrings that mark an UNVERIFIABLE folk attribution ("old-timers say",
# "legend has it") — a fabrication tell the current prompts even reward. A sentence
# containing one is dropped. Kept tight (clearly folkloric) to avoid false positives on
# legitimate prose; non-RU/EN sessions rely on the prompt ban (core.txt). See A3.
_ATTRIBUTION_MARKERS: dict[str, tuple[str, ...]] = {
    "ru": (
        "старожил", "по преданию", "предание гласит", "легенда гласит",
        "гласит легенда", "по легенде", "поговаривают", "молва",
    ),
    "en": (
        "old-timers", "old timers", "legend has it", "as legend", "as the story goes",
        "folklore", "rumor has it", "rumour has it",
    ),
}


# Rotating rhetorical angles for the area monologue so consecutive gap-filler beats
# differ in SHAPE (not just words) — the fix for "однообразные вступления" (A1). Model-
# facing (English steering is enough; the beat is written in {language} by core.txt).
_BEAT_MODES = ("observation", "history", "human", "sensory", "transition")


def beat_mode(index: int) -> str:
    """The rhetorical angle for the area beat at this rotation index."""
    return _BEAT_MODES[index % len(_BEAT_MODES)]


def solicit_markers(code: str | None) -> tuple[str, ...]:
    """Offer/solicitation substrings for the session language (empty tuple if none)."""
    return _SOLICIT_MARKERS.get(normalize(code), _SOLICIT_MARKERS[FALLBACK])


def attribution_markers(code: str | None) -> tuple[str, ...]:
    """Unverifiable-attribution substrings for the session language (empty if none)."""
    return _ATTRIBUTION_MARKERS.get(normalize(code), ())
