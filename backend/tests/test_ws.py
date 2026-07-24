import asyncio
import base64
import contextlib

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import settings
from app.services.agent.factory import build_orchestrator
from app.services.stt.stt import build_stt


def _heuristic_app(stt_text: str = "А когда его построили?"):
    # force a deterministic, offline backend regardless of .env
    settings.agent_backend = "heuristic"
    settings.geo_source = "fixture"
    settings.stt_backend = "mock"
    settings.stt_mock_text = stt_text
    settings.session_greeting = False  # test the narration flow without the one-time opener
    settings.thinking_filler = False  # the instant "let me think" filler has its own test
    main_module._orchestrator = build_orchestrator()
    main_module._stt = build_stt()
    return TestClient(main_module.app)


def _recv(ws, *, skip_reserve: bool = True):
    """Next frame, skipping async auxiliary pushes that may interleave with the main
    narration flow (`places`, and usually `reserve`)."""
    while True:
        msg = ws.receive_json()
        if msg["type"] == "places":
            continue
        if skip_reserve and msg["type"] == "reserve":
            continue
        return msg


def test_ws_narrates_then_replies():
    client = _heuristic_app()
    with client.websocket_connect("/ws") as ws:
        # standing on St Basil's (in the fixture) -> should narrate
        ws.send_json(
            {"type": "position", "lat": 55.7525, "lon": 37.6231, "gaze_confidence": "low"}
        )
        first = _recv(ws)
        assert first["type"] == "state"
        second = _recv(ws)
        assert second["type"] == "narration"
        assert "Василия" in second["text"]

        # barge-in
        ws.send_json({"type": "utterance", "text": "пропускай магазины"})
        _recv(ws)  # state
        reply = _recv(ws)
        assert reply["type"] == "reply"
        assert reply["text"]


def test_ws_audio_transcribes_then_replies():
    client = _heuristic_app(stt_text="пропускай магазины")
    with client.websocket_connect("/ws") as ws:
        clip = base64.b64encode(b"fake-audio-bytes").decode()
        ws.send_json({"type": "audio", "data_b64": clip, "format": "webm"})
        transcript = _recv(ws)
        assert transcript["type"] == "transcript"
        assert transcript["text"] == "пропускай магазины"
        _recv(ws)  # state
        reply = _recv(ws)
        assert reply["type"] == "reply"
        assert reply["text"]


def test_ws_thinking_filler_precedes_answer():
    # With the filler on, a question is met INSTANTLY with a short neutral "let me think" reply,
    # before the real answer — so the user isn't left in silence during STT + the answer LLM.
    client = _heuristic_app(stt_text="пропускай магазины")
    settings.thinking_filler = True
    try:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "utterance", "text": "а что это за здание?"})
            # Two replies now arrive: the instant filler, then the real answer.
            replies = []
            for _ in range(8):
                m = _recv(ws)
                if m["type"] == "reply" and m["text"]:
                    replies.append(m["text"])
                if len(replies) >= 2:
                    break
            assert len(replies) >= 2  # filler + answer
    finally:
        settings.thinking_filler = False


def test_ws_audio_empty_transcript_errors():
    # Whisper heard nothing intelligible -> say so, don't answer a blank question
    # (which used to produce a vague "ок, продолжим" that felt like no answer).
    client = _heuristic_app(stt_text="   ")
    with client.websocket_connect("/ws") as ws:
        clip = base64.b64encode(b"silence").decode()
        ws.send_json({"type": "audio", "data_b64": clip, "format": "wav"})
        assert _recv(ws)["type"] == "transcript"
        err = _recv(ws)
        assert err["type"] == "error"
        assert "расслыш" in err["message"].lower()


def test_ws_listen_pauses_then_question_resumes():
    # Opening the mic ("listen on") must hold the producer so it can't narrate over
    # the user; the answered question then resumes the tour.
    client = _heuristic_app(stt_text="пропускай магазины")
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "listen", "on": True})
        # position arrives while listening -> no narration is emitted (producer held)
        ws.send_json(
            {"type": "position", "lat": 55.7525, "lon": 37.6231, "gaze_confidence": "low"}
        )
        clip = base64.b64encode(b"fake-audio").decode()
        ws.send_json({"type": "audio", "data_b64": clip, "format": "wav"})
        assert _recv(ws)["type"] == "transcript"
        assert _recv(ws)["type"] == "state"
        reply = _recv(ws)
        assert reply["type"] == "reply" and reply["text"]
        # tour resumes after answering
        assert _recv(ws)["type"] == "state"
        assert _recv(ws)["type"] == "narration"


def test_producer_exits_on_shutdown_even_while_barging():
    """Zombie-producer regression: a disconnect (the /ws finally cancels the producer)
    that lands WHILE a barge-in is in flight must still terminate the producer. Before
    the fix the shutdown CancelledError was swallowed as a barge-in preempt, so the
    producer parked/hot-looped forever on the closed socket. The `closing` flag makes
    the producer tell the two apart and exit."""

    async def scenario():
        rt = main_module._SessionRuntime(ws=None, orch=None, session_id="z")

        async def idle_step():  # stand-in for _step: park until cancelled
            rt.wake.clear()
            await rt.wake.wait()

        rt._step = idle_step
        producer = asyncio.ensure_future(rt.run_producer())
        await asyncio.sleep(0.05)  # let it create + await the first step_task
        # simulate the /ws finally during an in-flight barge-in:
        rt.barging = True
        rt.closing = True
        producer.cancel()
        # must finish promptly; without the fix it parks on resume.wait() forever and
        # this wait_for raises TimeoutError instead.
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(producer, timeout=2.0)
        assert producer.done()

    asyncio.run(scenario())


def test_ws_pause_halts_narration_then_resume_continues():
    # In-app/notification Pause: cut narration AND generation, emit state=paused; a
    # position walked while paused is breadcrumbed with the paused flag ([lat, lon, 1.0]);
    # Resume continues the SAME tour (no restart, seen-list intact).
    client = _heuristic_app()
    orch = main_module._orchestrator
    sid = "pausetest1234567"
    with client.websocket_connect(f"/ws?sid={sid}") as ws:
        ws.send_json(
            {"type": "position", "lat": 55.7525, "lon": 37.6231, "gaze_confidence": "low"}
        )
        assert _recv(ws)["type"] == "state"
        assert _recv(ws)["type"] == "narration"
        # pause -> a single state=paused frame, no further narration
        ws.send_json({"type": "pause"})
        paused = _recv(ws)
        assert paused["type"] == "state" and paused["state"] == "paused"
        # a position while paused must NOT narrate, but is recorded (flagged) on the track
        ws.send_json(
            {"type": "position", "lat": 55.7550, "lon": 37.6231, "gaze_confidence": "low"}
        )
        # resume -> the producer comes back to life (a state frame at minimum)
        ws.send_json({"type": "resume"})
        assert _recv(ws)["type"] in ("state", "narration")
    state = asyncio.run(orch.store.load(sid))
    assert any(
        len(p) == 3 and p[2] == 1.0 for p in state.path
    ), "the point walked while paused should be flagged on the route"
    assert state.seen_place_ids, "seen-list survives the pause (same tour)"


def test_ws_route_accept_ack_round_trip():
    client = _heuristic_app()
    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {"type": "position", "lat": 55.7539, "lon": 37.6208, "gaze_confidence": "low"}
        )
        ws.send_json({"type": "start_guided", "mode": "loop", "budget_min": 40})
        route = None
        for _ in range(12):
            msg = _recv(ws, skip_reserve=False)
            if msg["type"] == "route":
                route = msg
                break
        assert route is not None and route["stops"]
        ws.send_json({"type": "route_accept"})
        ack = None
        startup = None
        for _ in range(10):
            msg = _recv(ws, skip_reserve=False)
            if msg["type"] == "route_accepted":
                ack = msg
            elif ack is not None and msg["type"] == "narration" and msg["text"]:
                startup = msg
                break
        assert ack == {"type": "route_accepted"}
        assert startup is not None


def test_ws_unknown_type_errors():
    client = _heuristic_app()
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "nonsense"})
        msg = ws.receive_json()
        assert msg["type"] == "error"


def test_ws_ping_is_ignored():
    # keepalive pings must not error or disturb the narration flow
    client = _heuristic_app()
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "ping"})
        ws.send_json(
            {"type": "position", "lat": 55.7525, "lon": 37.6231, "gaze_confidence": "low"}
        )
        assert _recv(ws)["type"] == "state"
        assert _recv(ws)["type"] == "narration"


def test_ws_emits_reserve_and_accepts_reserve_played_ack():
    client = _heuristic_app()
    sid = "reservews12345678"
    with client.websocket_connect(f"/ws?sid={sid}") as ws:
        ws.send_json(
            {"type": "position", "lat": 55.7525, "lon": 37.6231, "gaze_confidence": "low"}
        )
        reserve = None
        narration = None
        seen_state = False
        for _ in range(12):
            msg = _recv(ws, skip_reserve=False)
            if msg["type"] == "state":
                seen_state = True
            elif msg["type"] == "reserve":
                reserve = msg
            elif msg["type"] == "narration":
                narration = msg
            if seen_state and reserve and narration:
                break
        assert seen_state is True
        assert narration is not None
        assert reserve is not None and reserve["items"]
        rid = reserve["items"][0]["id"]
        ws.send_json({"type": "reserve_played", "reserve_id": rid})
    state = asyncio.run(main_module._orchestrator.store.load(sid))
    assert rid in state.played_reserve_ids
    assert all(it.id != rid for it in state.fact_reserve)


def test_ws_reserve_round_trips_even_when_aux_frames_interleave():
    client = _heuristic_app()
    sid = "reservews22345678"
    with client.websocket_connect(f"/ws?sid={sid}") as ws:
        ws.send_json(
            {"type": "position", "lat": 55.7525, "lon": 37.6231, "gaze_confidence": "low"}
        )
        seen = []
        reserve = None
        while len(seen) < 4:
            msg = _recv(ws, skip_reserve=False)
            seen.append(msg['type'])
            if msg['type'] == 'reserve':
                reserve = msg
        assert 'state' in seen
        assert 'narration' in seen
        assert reserve is not None and reserve['items']


def test_ws_reserve_includes_paid_audio_when_tts_enabled(monkeypatch):
    client = _heuristic_app()
    monkeypatch.setattr(main_module, '_synth_audio', lambda text, tier, language: asyncio.sleep(0, result=('ZmFrZQ==', 'audio/mpeg')))
    sid = 'reserveaudio1234567'
    with client.websocket_connect(f'/ws?sid={sid}') as ws:
        ws.send_json({'type': 'auth', 'token': ''})
        ws.send_json({'type': 'position', 'lat': 55.7525, 'lon': 37.6231, 'gaze_confidence': 'low'})
        reserve = None
        for _ in range(12):
            msg = _recv(ws, skip_reserve=False)
            if msg['type'] == 'reserve':
                reserve = msg
                break
        assert reserve is not None and reserve['items']
        assert any(it.get('audio_b64') == 'ZmFrZQ==' for it in reserve['items'])


def test_ws_resume_keeps_session_after_disconnect():
    # A reconnect with the same ?sid= must resume the SAME session: the seen-list
    # survives the disconnect (no delete-on-disconnect) so the tour doesn't repeat.
    client = _heuristic_app()
    orch = main_module._orchestrator
    sid = "resumetest123456"
    with client.websocket_connect(f"/ws?sid={sid}") as ws:
        ws.send_json(
            {"type": "position", "lat": 55.7525, "lon": 37.6231, "gaze_confidence": "low"}
        )
        assert _recv(ws)["type"] == "state"
        assert _recv(ws)["type"] == "narration"
    # after the socket closes the session is kept (TTL-evicted later, not deleted now)
    state = asyncio.run(orch.store.load(sid))
    assert state.seen_place_ids, "seen-list should persist across reconnects"
