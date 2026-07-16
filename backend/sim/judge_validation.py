"""Judge validation set — the GOLD STANDARD the interestingness judge is measured against
(Block 4). Hand-labeled from real prod walks + known cases, web-verified where noted. If the
judge disagrees with these, the judge (rubric) is wrong, not the label.

Each case: blurb + optional FACTS given to the narrator + expected verdict:
  grounded  — is it TRUE / well-founded? (NOT "was a FACTS string provided" — the bug we fix:
              a true-but-unsourced claim must be grounded=True, not "fabricated")
  cliche    — empty poetic filler / ad-speak?
  interesting — should the overall interest be high (>=3/4) or low (<=1/4)?

Run with ``sim.judge_validate`` (needs a reachable judge model — prod only).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Case:
    blurb: str
    grounded: bool
    cliche: bool
    interesting: str          # "high" | "low" | "any"
    note: str
    facts: str | None = None


CASES: list[Case] = [
    # --- TRUE but unsourced (FACTS=None): the judge MUST NOT call these fabrication -------- #
    Case(
        blurb="Субстратостат «Волга» — последний советский стратостат, его создали в "
              "Долгопрудненском КБ автоматики, а рекордные прыжки с него были в 1962 году.",
        grounded=True, cliche=False, interesting="high", facts=None,
        note="web-verified true: Волга stratostat, ДКБА, 1962 (ru.wikipedia Волга_(стратостат))",
    ),
    Case(
        blurb="Долгопрудный — наукоград: тут и МФТИ, и конструкторские бюро, где делали "
              "аэростаты и стратостаты ещё со времён Дирижаблестроя.",
        grounded=True, cliche=False, interesting="any", facts=None,
        note="web-verified true; interest borderline (general 'science town') — accept 2-4",
    ),
    Case(
        blurb="Аллея Космонавтов появилась здесь в 1967 году, к десятилетию запуска "
              "первого спутника.",
        grounded=True, cliche=False, interesting="high", facts=None,
        note="true: Аллея Космонавтов открыта 1967, Спутник-1 1957 (10 лет)",
    ),
    # --- CLEARLY FABRICATED (FACTS=None, implausible specifics) -> grounded=False --------- #
    Case(
        blurb="А знаете, что именно здесь, в Останкине, в конце девяностых проходили первые "
              "в Москве испытания беспилотных автомобилей — их тестировали инженеры из МАДИ?",
        grounded=False, cliche=False, interesting="any", facts=None,
        note="fabricated: no autonomous-car tests in Ostankino in the late 90s",
    ),
    Case(
        blurb="Этот детский сад построили в тридцатые годы прошлого века специально для "
              "рабочих завода.",
        grounded=False, cliche=False, interesting="low", facts=None,
        note="fabricated: invented date/purpose for an ordinary kindergarten, no facts",
    ),
    # --- GROUNDED in provided FACTS -> grounded=True -------------------------------------- #
    Case(
        blurb="Башню возвели в 1901 году, и в ней двенадцать залов.",
        grounded=True, cliche=False, interesting="any",
        facts="Построена в 1901 году. 12 залов.",
        note="every claim follows from FACTS",
    ),
    # --- CONTRADICTS / exceeds FACTS -> grounded=False ----------------------------------- #
    Case(
        blurb="Здесь, по легенде, останавливался сам Пушкин, когда ехал в Петербург.",
        grounded=False, cliche=False, interesting="any",
        facts="Купеческий дом XIX века. Кирпичный, два этажа.",
        note="Pushkin claim not in FACTS -> fabrication (grounded=false); not poetic cliché",
    ),
    # --- CLICHÉ / ad-speak --------------------------------------------------------------- #
    Case(
        blurb="Здесь время будто застыло, и всё вокруг дышит историей этого удивительного "
              "места.",
        grounded=True, cliche=True, interesting="low", facts=None,
        note="empty poetic filler, no content — cliché",
    ),
    Case(
        blurb="За обычным фасадом клиника уже больше десяти лет возвращает людям зрение без "
              "скальпеля — операции без наркоза, и домой в тот же день.",
        grounded=True, cliche=True, interesting="low", facts=None,
        note="ad-speak for a clinic (invariant: no ad-speak) — claims plausible but promotional",
    ),
    # --- INTERESTING vs DULL (grounded either way) --------------------------------------- #
    Case(
        blurb="Тело Ленина забальзамировали в 1924-м, а в начале шестидесятых тайно "
              "перезахоронили тут же, у Кремлёвской стены.",
        grounded=True, cliche=False, interesting="high",
        facts="Ленин, бальзамирование 1924. Перезахоронение в 1960-е у Кремлёвской стены.",
        note="grounded + genuinely novel/hooky -> high interest",
    ),
    Case(
        blurb="Это парк, здесь гуляют люди и отдыхают.",
        grounded=True, cliche=False, interesting="low", facts=None,
        note="true but trivial -> low interest (not a fact worth telling)",
    ),
    Case(
        blurb="Слева — старая водонапорная башня.",
        grounded=True, cliche=False, interesting="low", facts=None,
        note="plain naming of a visible object, no unverifiable claim -> grounded, low interest",
    ),
]
