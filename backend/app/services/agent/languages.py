"""Supported guide languages — single source of truth (backend side).

Codes are ISO-639-1. faster-whisper accepts these directly (incl. ``zh``), so
STT needs no mapping. The LLM prompt wants a human-readable language name, so
``prompt_language`` maps code -> name for the ``{language}`` placeholder in
``prompts/core.txt``. The client owns the code -> BCP-47 mapping for TTS.
"""

from __future__ import annotations

import random

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


# Minimum length for a line to seed CONTINUE_FROM — terse floor/bridge one-liners below
# this are noise ("continue this voice" from "Пройдём дальше." reads limp).
_CONTINUE_MIN_CHARS = 24


def clean_continuation(history: list[str], code: str | None, *, n: int = 2) -> list[str]:
    """The last `n` SUBSTANTIVE narration lines, for the CONTINUE_FROM seed. Drops the terse
    connectives that pollute raw `history[-n:]` — the verbatim 'let's move on' bridges and
    short floor one-liners — so the narrator continues from real prose, not a stub."""
    bset = set(bridges(code))
    substantive = [
        h for h in history
        if h.strip() not in bset and len(h.strip()) >= _CONTINUE_MIN_CHARS
    ]
    return substantive[-n:]


# A warm, instant opener spoken the moment a walk starts — no LLM, so the tour begins
# immediately while discovery/geocode/the area intro load in the background (the area
# intro, which names the place properly, follows on the next tick).
# Session opener = a time-of-day greeting + a varied tail (a quick preview intro). Spoken
# the moment a walk starts, after geolocation is loaded. The tail's "{place}" is the
# district/city when known, else the no-place variant (avoids awkward grammar). The opener
# rotates by time of day and the tail is picked at random, so it's different each walk.
_GREETING_OPENERS: dict[str, dict[str, str]] = {
    "ru": {"morning": "Доброе утро!", "day": "Добрый день!",
           "evening": "Добрый вечер!", "night": "Доброй ночи!"},
    "en": {"morning": "Good morning!", "day": "Good afternoon!",
           "evening": "Good evening!", "night": "Hello!"},
    "es": {"morning": "¡Buenos días!", "day": "¡Buenas tardes!",
           "evening": "¡Buenas noches!", "night": "¡Hola!"},
    "fr": {"morning": "Bonjour !", "day": "Bonjour !",
           "evening": "Bonsoir !", "night": "Bonsoir !"},
    "de": {"morning": "Guten Morgen!", "day": "Guten Tag!",
           "evening": "Guten Abend!", "night": "Hallo!"},
    "it": {"morning": "Buongiorno!", "day": "Buon pomeriggio!",
           "evening": "Buonasera!", "night": "Ciao!"},
    "pt": {"morning": "Bom dia!", "day": "Boa tarde!",
           "evening": "Boa noite!", "night": "Olá!"},
    "zh": {"morning": "早上好！", "day": "下午好！", "evening": "晚上好！", "night": "您好！"},
}

# Tail = (with-place, without-place). Picked at random for variety.
_GREETING_TAILS: dict[str, list[tuple[str, str]]] = {
    "ru": [
        ("Начинаем прогулку — {place}. Осмотрюсь и расскажу, чем тут интересно.",
         "Рада пройтись с тобой. Осмотрюсь и расскажу, чем интересны эти места."),
        ("Мы с тобой — {place}. Дай гляну по сторонам и введу в курс.",
         "Пройдёмся не спеша. Дай гляну по сторонам и введу в курс."),
        ("Прогуляемся — {place}. Сейчас осмотрюсь и покажу, что вокруг любопытного.",
         "Прогуляемся не торопясь. Сейчас осмотрюсь и покажу, что вокруг любопытного."),
    ],
    "en": [
        ("We're near {place}. Let me look around and preview the walk for you.",
         "Glad to walk with you. Let me look around and preview the walk."),
        ("Our start is {place}. Give me a moment to look around and set the scene.",
         "Let's take it slow. Give me a moment to look around and set the scene."),
    ],
    "es": [
        ("Estamos cerca de {place}. Echo un vistazo y te adelanto lo que veremos.",
         "Me alegra pasear contigo. Echo un vistazo y te adelanto lo que veremos."),
        ("Empezamos por {place}. Un momento, miro alrededor y te pongo en situación.",
         "Vamos con calma. Un momento, miro alrededor y te pongo en situación."),
    ],
    "fr": [
        ("Nous sommes près de {place}. Je regarde autour et je t'en fais un aperçu.",
         "Ravie de marcher avec toi. Je regarde autour et je t'en fais un aperçu."),
        ("On démarre à {place}. Un instant, je regarde autour et je te situe.",
         "Allons-y tranquillement. Un instant, je regarde autour et je te situe."),
    ],
    "de": [
        ("Wir sind nahe {place}. Ich schaue mich um und gebe dir kurz einen Überblick.",
         "Schön, mit dir zu gehen. Ich schaue mich um und gebe dir einen Überblick."),
        ("Wir starten bei {place}. Moment, ich sehe mich um und ordne alles ein.",
         "Lass es uns ruhig angehen. Moment, ich sehe mich um und ordne alles ein."),
    ],
    "it": [
        ("Siamo vicino a {place}. Do un'occhiata e ti anticipo cosa vedremo.",
         "Che bello passeggiare con te. Do un'occhiata e ti anticipo cosa vedremo."),
        ("Partiamo da {place}. Un attimo, mi guardo intorno e ti oriento.",
         "Andiamo con calma. Un attimo, mi guardo intorno e ti oriento."),
    ],
    "pt": [
        ("Estamos perto de {place}. Vou olhar em volta e já te dou uma prévia.",
         "Que bom caminhar com você. Vou olhar em volta e já te dou uma prévia."),
        ("Começamos por {place}. Um instante, olho em volta e te situo.",
         "Vamos com calma. Um instante, olho em volta e te situo."),
    ],
    "zh": [
        ("我们就在{place}附近。我看看四周，先给您简单介绍这次散步。",
         "很高兴与您同行。我看看四周，先给您简单介绍这次散步。"),
        ("这次从{place}出发。稍等，我看看周围，给您理理头绪。",
         "我们慢慢走。稍等，我看看周围，给您理理头绪。"),
    ],
}


def _time_of_day(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "day"
    if 18 <= hour < 23:
        return "evening"
    return "night"


def greeting(code: str | None, place: str | None = None, hour: int | None = None) -> str:
    """A varied session opener: a time-of-day greeting ('Доброе утро!' …) + a randomly
    picked tail that names the district/city when known. `hour` is the walker's LOCAL hour
    (0-23); None => a neutral 'day' opener."""
    lang = normalize(code)
    openers = _GREETING_OPENERS.get(lang, _GREETING_OPENERS[FALLBACK])
    opener = openers[_time_of_day(hour if hour is not None else 12)]
    with_place, without_place = random.choice(
        _GREETING_TAILS.get(lang, _GREETING_TAILS[FALLBACK])
    )
    tail = with_place.format(place=place) if place else without_place
    return f"{opener} {tail}"


# Woven back into a narration we paused to slip an object in (spoken before the remaining
# sentences resume) — so the return doesn't feel like a jump-cut. Several variants per
# language, rotated by call index (like `beat_mode`) so a walk with many weave-ins doesn't
# repeat the same connective verbatim — that repetition is exactly what the narrator prompt
# bans ("затёртые связки по кругу").
_RESUME_CONNECTIVES: dict[str, tuple[str, ...]] = {
    "ru": ("Так вот, вернёмся.", "На чём я остановилась…", "Ну, продолжим."),
    "en": ("So, back to what I was saying.", "Anyway, where was I…", "Right, let's carry on."),
    "es": ("Bueno, volvamos a lo que decía.", "¿Por dónde iba…", "En fin, sigamos."),
    "fr": ("Bref, revenons à ce que je disais.", "Où en étais-je…", "Bon, continuons."),
    "de": ("Also, zurück zu dem, was ich sagte.", "Wo war ich…", "Gut, weiter geht's."),
    "it": ("Dunque, torniamo a quello che dicevo.", "Dov'ero rimasta…", "Bene, andiamo avanti."),
    "pt": ("Então, voltando ao que eu dizia.", "Onde é que eu ia…", "Enfim, vamos continuar."),
    "zh": ("好，我们接着刚才说的。", "刚才说到哪了……", "好，继续吧。"),
}


def resume_connective(code: str | None, index: int = 0) -> str:
    """Spoken before the remaining sentences of a narration we paused to weave an object in.
    `index` rotates through the per-language variants (`% len`) so repeated resumes vary."""
    variants = _RESUME_CONNECTIVES.get(normalize(code), _RESUME_CONNECTIVES[FALLBACK])
    return variants[index % len(variants)]


# Higher-level "let's get back to the tour" bridges spoken after a voice question OR after
# un-pausing — one short line before the guide picks the tour back up. Two moods, chosen by
# whether what we paused is still relevant (see NarrationScheduler): `continue` returns to the
# SAME topic ("на чём мы остановились…"); `onward` moves on to fresh nearby material because we
# walked past the old one. Many variants per language, rotated by call index so they don't get
# repetitive/annoying. Feminine self-reference to match the voice (see [[assistant-gender]]).
_TOUR_BRIDGES: dict[str, dict[str, tuple[str, ...]]] = {
    "ru": {
        "continue": (
            "Так, на чём мы остановились… да, продолжаем.",
            "Ну что, вернёмся к нашей прогулке.",
            "Итак, продолжим с того же места.",
            "Хорошо, возвращаюсь к тому, о чём рассказывала.",
            "Так, о чём это я… а, да.",
            "Ладно, продолжаем экскурсию.",
            "Ну, продолжим начатое.",
            "Так, снова к нашей теме.",
            "Возвращаемся туда, где прервались.",
        ),
        "onward": (
            "Ну что, идём дальше.",
            "Так, продолжаем прогулку — тут вокруг тоже интересно.",
            "Ладно, двигаемся дальше.",
            "А мы тем временем идём дальше.",
            "Хорошо, посмотрим, что тут вокруг.",
            "Итак, продолжаем — впереди ещё есть на что взглянуть.",
            "Ну, идём дальше по маршруту.",
            "Так, а теперь — о том, что вокруг нас сейчас.",
            "Продолжим прогулку.",
        ),
    },
    "en": {
        "continue": (
            "So, where were we… right, let's carry on.",
            "Anyway, back to our walk.",
            "Okay, let me pick up where I left off.",
            "Right, back to what I was telling you.",
            "So, let's continue where we stopped.",
        ),
        "onward": (
            "Anyway, let's keep going.",
            "Okay, moving on — there's plenty around us too.",
            "Right, let's carry on with the walk.",
            "So, let's see what's around here now.",
            "Let's keep strolling.",
        ),
    },
    "es": {
        "continue": (
            "Bueno, ¿por dónde íbamos… ah, sí, seguimos.",
            "En fin, volvamos a nuestro paseo.",
            "Vale, retomo lo que te contaba.",
            "Sigamos donde lo dejamos.",
            "Bien, continuamos con lo de antes.",
        ),
        "onward": (
            "Bueno, sigamos adelante.",
            "Vale, continuamos — por aquí también hay cosas interesantes.",
            "Seguimos con el paseo.",
            "Veamos qué hay por aquí ahora.",
            "Continuemos caminando.",
        ),
    },
    "fr": {
        "continue": (
            "Alors, où en étions-nous… ah oui, on continue.",
            "Bref, revenons à notre balade.",
            "Bon, je reprends où je m'étais arrêtée.",
            "Reprenons là où on s'est arrêtés.",
            "Bien, continuons sur notre sujet.",
        ),
        "onward": (
            "Bref, continuons.",
            "Bon, on avance — il y a de quoi voir par ici aussi.",
            "Poursuivons la balade.",
            "Voyons ce qu'il y a autour de nous maintenant.",
            "Continuons à marcher.",
        ),
    },
    "de": {
        "continue": (
            "Also, wo waren wir… ach ja, weiter geht's.",
            "Also, zurück zu unserem Spaziergang.",
            "Gut, ich mache da weiter, wo ich aufgehört habe.",
            "Machen wir dort weiter, wo wir waren.",
            "Also, weiter mit unserem Thema.",
        ),
        "onward": (
            "Also, gehen wir weiter.",
            "Gut, weiter — hier ringsum gibt es auch einiges.",
            "Setzen wir den Spaziergang fort.",
            "Schauen wir, was hier gerade um uns herum ist.",
            "Gehen wir weiter.",
        ),
    },
    "it": {
        "continue": (
            "Allora, dov'eravamo… ah sì, continuiamo.",
            "Dunque, torniamo alla nostra passeggiata.",
            "Bene, riprendo da dove avevo lasciato.",
            "Riprendiamo da dove eravamo.",
            "Allora, continuiamo con il nostro discorso.",
        ),
        "onward": (
            "Allora, andiamo avanti.",
            "Bene, proseguiamo — anche qui intorno c'è da vedere.",
            "Continuiamo la passeggiata.",
            "Vediamo cosa c'è qui intorno adesso.",
            "Continuiamo a camminare.",
        ),
    },
    "pt": {
        "continue": (
            "Então, onde estávamos… ah sim, continuamos.",
            "Enfim, voltando ao nosso passeio.",
            "Bom, retomo de onde parei.",
            "Vamos continuar de onde paramos.",
            "Então, seguimos com o nosso assunto.",
        ),
        "onward": (
            "Então, vamos seguindo.",
            "Bom, continuamos — por aqui também há o que ver.",
            "Vamos continuar o passeio.",
            "Vejamos o que há à nossa volta agora.",
            "Vamos continuar a caminhar.",
        ),
    },
    "zh": {
        "continue": (
            "好，我们刚才说到哪了……对，继续。",
            "那我们接着刚才的散步。",
            "好，我接着刚才的说。",
            "从刚才停下的地方继续吧。",
            "好，我们继续刚才的话题。",
        ),
        "onward": (
            "好，我们继续往前走。",
            "那我们接着走——这周围也有值得看的。",
            "继续我们的散步吧。",
            "看看现在我们周围有什么。",
            "我们接着走吧。",
        ),
    },
}


def tour_bridge(code: str | None, index: int = 0, mode: str = "onward") -> str:
    """A short 'back to the tour' line after a question or a pause. `mode` is "continue" (return
    to the same, still-relevant topic) or "onward" (we've moved on — lead into fresh material).
    `index` rotates the per-language variants so repeats don't get annoying."""
    lang = _TOUR_BRIDGES.get(normalize(code), _TOUR_BRIDGES[FALLBACK])
    variants = lang.get(mode) or lang["onward"]
    return variants[index % len(variants)]


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

# Grounded variant used when there are NO web-verified area facts. The model invents
# obscure street/district detail (the "метеоритный кратер" fabrication) but reliably
# knows a *named city* — so this leans on widely-known knowledge and demands [SILENCE]
# rather than invention. Only ever used at the city level (see Orchestrator._area_line).
_AREA_TOPIC_GROUNDED_EN = (
    "one widely-known, verifiable fact about the {label} {name} — real history or "
    "geography a well-read local would confirm, spoken plainly. Do NOT invent specifics, "
    "dates or names, and no repeats. If you don't know a solid fact, reply exactly [SILENCE]"
)
_AREA_TOPIC_GROUNDED: dict[str, str] = {
    "ru": (
        "один широко известный, достоверный факт про {label} {name} — реальная история "
        "или география, которую подтвердил бы начитанный местный, простыми словами. "
        "НЕ выдумывай конкретику, даты и имена, без повторов. Если твёрдого факта нет — "
        "ответь ровно [SILENCE]"
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


def area_topic_grounded(code: str | None, label: str, name: str) -> str:
    """A cascade beat for fact-less areas: widely-known city knowledge or [SILENCE]."""
    tmpl = _AREA_TOPIC_GROUNDED.get(normalize(code), _AREA_TOPIC_GROUNDED_EN)
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
