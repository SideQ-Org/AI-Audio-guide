"""_speech_seconds — the per-language sentence-pacing estimate used by the producer."""

from app.main import _speech_seconds


def test_denser_script_gets_a_longer_estimate():
    text = "a" * 70
    # Chinese packs far more content per character -> longer spoken duration for the same
    # character count, so the next sentence isn't released early.
    assert _speech_seconds(text, "zh") > _speech_seconds(text, "ru")


def test_clamps_hold():
    assert _speech_seconds("!", "ru") == 1.5  # floor
    assert _speech_seconds("y" * 10000, "ru") == 18.0  # ceiling


def test_language_subtag_and_unknown_fall_back_to_default():
    text = "a" * 70
    assert _speech_seconds(text, "en-US") == _speech_seconds(text, "en")
    assert _speech_seconds(text, None) == _speech_seconds(text, "ru")  # both default rate
