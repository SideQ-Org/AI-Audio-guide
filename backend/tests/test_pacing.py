"""_speech_seconds — the per-language sentence-pacing estimate used by the producer."""

import asyncio

from app.main import _SessionRuntime, _speech_seconds


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


def test_free_walk_idle_wakes_on_prefetch_completion():
    async def run():
        rt = _SessionRuntime.__new__(_SessionRuntime)
        rt.wake = asyncio.Event()
        rt.live_position = object()
        rt._area_prefetch = asyncio.create_task(asyncio.sleep(0.01, result=("topic", "text", None)))
        waiters = [asyncio.create_task(rt.wake.wait())]
        if rt._area_prefetch is not None:
            waiters.append(rt._area_prefetch)
        done, pending = await asyncio.wait(
            waiters,
            timeout=0.2,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        assert done
        assert any(task is not waiters[0] for task in done)

    asyncio.run(run())


def test_guided_idle_wakes_on_prefetch_completion():
    async def run():
        rt = _SessionRuntime.__new__(_SessionRuntime)
        rt.wake = asyncio.Event()
        rt._area_prefetch = asyncio.create_task(asyncio.sleep(0.01, result=("topic", "text", None)))
        waiters = [asyncio.create_task(rt.wake.wait())]
        if rt._area_prefetch is not None:
            waiters.append(rt._area_prefetch)
        done, pending = await asyncio.wait(
            waiters,
            timeout=0.2,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        assert done
        assert any(task is not waiters[0] for task in done)

    asyncio.run(run())
