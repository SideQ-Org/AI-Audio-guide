"""Narrator CARD block: structured re-readable facts emitted in the same call as the spoken
narration, stripped before TTS (must be cut BEFORE the HOOK baton, whose matcher runs to EOF)."""

from __future__ import annotations

from app.services.agent.narrator import _strip_hook, split_card


def _parse(raw: str):
    body, card = split_card(raw)
    spoken, hook = _strip_hook(body)
    return spoken.strip(), hook, card


def test_card_stripped_before_hook_spoken_clean() -> None:
    raw = (
        "Музей открылся в тысяча восемьсот девяностом году.\n"
        "HOOK: дальше про парк\n"
        "CARD:\nОснован в 1890 году.\nАрхитектор — Иванов."
    )
    spoken, hook, card = _parse(raw)
    assert spoken == "Музей открылся в тысяча восемьсот девяностом году."
    assert hook == "дальше про парк"
    assert card == "Основан в 1890 году.\nАрхитектор — Иванов."
    assert "CARD" not in spoken and "HOOK" not in spoken


def test_card_without_hook() -> None:
    spoken, hook, card = _parse("Небольшая церковь.\nCARD:\nПостроена в XVIII веке.")
    assert spoken == "Небольшая церковь."
    assert hook is None
    assert card == "Построена в XVIII веке."


def test_no_card_leaves_narration_untouched() -> None:
    spoken, hook, card = _parse("Обычный сквер. HOOK: идём дальше")
    assert spoken == "Обычный сквер."
    assert hook == "идём дальше"
    assert card is None


def test_card_on_same_line_still_split() -> None:
    # DeepSeek sometimes puts the marker inline — still cut it off the spoken text.
    spoken, _, card = _parse("Старый мост. CARD: Построен в 1905 году.")
    assert spoken == "Старый мост."
    assert card == "Построен в 1905 году."
