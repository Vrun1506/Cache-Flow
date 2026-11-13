"""Regression tests from an adversarial review of tutor/v2 (each one failed before its fix)."""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from test_v2_inputs import lines_json, reading, script
from tutor.llm import FakeLLM
from tutor.speech.tts import FakeTTS, Narrator
from tutor.v2.app import create_app
from tutor.v2.ink import ink_is_valid
from tutor.v2.service import TutorService
from v2_support import Client, meta, png


@pytest.fixture
def make(math_worker):
    made = []

    def _make(scr=None, *, token=None, delay=0.0, **kw):
        llm = FakeLLM(scr if scr is not None else script(), delay=delay)
        service = TutorService(llm, math_worker, narrator=Narrator(FakeTTS()), **kw)
        tc = TestClient(create_app(service, api_token=token))
        tc.__enter__()
        c = Client(tc)
        c.llm, c.service = llm, service
        made.append(c)
        return c

    yield _make
    for c in made:
        c.close()
        c.tc.__exit__(None, None, None)


def board_at(c, rev, x, y, w=300, h=50, lines=1, rid=None, image=None):
    """Upload a board whose single line sits at a chosen place."""
    c.rev = rev
    m = json.dumps({"board_revision": rev, "request_id": rid or c.rid(), "image_region": [0, 0, 1200, 640],
                    "lines": [{"line_index": i, "bbox": [x, y + 60 * i, w, h]} for i in range(lines)]})
    return c.tc.post(f"/v2/sessions/{c.sid}/board", data={"meta": m}, files={"image": ("b.png", image or png(lines=lines), "image/png")})


def finish_lesson_unhelped(c):
    c.intent("tutor")
    c.until_phase("student_turn")
    for step in (1, 2, 3):
        c.intent("quiz_answer", "6")
        c.until_phase("student_turn" if step < 3 else "independent_check")
    c.until_type("tutor_turn")
    c.until_phase("independent_check")


# --------------------------------------------------------------------- ink the iPad can draw


def test_the_circle_around_a_wrong_line_has_strokes_and_stays_on_the_board(make):
    """The iPad draws ink only from `strokes`, so an empty circle is invisible; a line near the edge must not leave the board."""
    c = make(script(vision_lines=[lines_json(reading("(x - 2)(x - 3) = 0"))]))
    c.open("quad_5_6")
    c.until_phase("ready")
    assert board_at(c, 1, 5, 4).status_code == 200  # 5 pt from the left edge, 4 pt from the top
    c.intent("check")
    seen = c.until_phase("student_turn")
    circle = [e["payload"] for e in seen if e["type"] == "tutor_ink_preview" and e["payload"]["style"] == "circle"][-1]
    assert circle["strokes"] and len(circle["strokes"][0]) > 8
    from tutor.v2.layout import BoardLayout, Rect

    assert ink_is_valid(circle, BoardLayout.for_screen(1210, 834).board_bounds)


# ------------------------------------------------------------------- transfer item correctness


def test_a_factorised_line_does_not_complete_the_transfer_item(make):
    c = make()
    c.open("quad_5_6")
    c.until_phase("ready")
    finish_lesson_unhelped(c)
    c.intent("quiz_answer", "(x+3)(x+4)=0")  # valid maths, but not the answer to "solve"
    seen = c.until_phase("independent_check")
    assert not any(e["type"] == "state" and e["payload"]["phase"] == "complete" for e in seen)
    summary = c.tc.get(f"/v2/sessions/{c.sid}/summary").json()
    assert len(summary["items"]) == 1  # only the lesson itself is recorded


def test_checking_the_transfer_item_on_the_board_emits_one_evidence(make):
    c = make(script(vision_lines=[lines_json(reading("x = -3 or x = -4"))]))
    c.open("quad_5_6")
    c.until_phase("ready")
    finish_lesson_unhelped(c)
    board_at(c, 1, 40, 170)
    c.intent("check")
    seen = c.until_phase("complete")
    assert [e["type"] for e in seen].count("evidence") == 1


def test_hints_are_refused_on_the_independent_item_and_do_not_taint_it(make):
    c = make()
    c.open("quad_5_6")
    c.until_phase("ready")
    finish_lesson_unhelped(c)
    c.intent("hint")
    seen = c.until_phase("independent_check")
    turn = next(e["payload"] for e in seen if e["type"] == "tutor_turn")
    assert turn["assisted"] is False and "on your own" in turn["caption"].lower()
    c.intent("quiz_answer", "x=-4 or x=-3")
    seen = c.until_phase("complete")
    assert next(e["payload"] for e in seen if e["type"] == "evidence")["independent"] is True


def test_tutor_me_during_the_transfer_item_re_asks_the_transfer_not_the_old_question(make):
    c = make()
    c.open("quad_5_6")
    c.until_phase("ready")
    finish_lesson_unhelped(c)
    c.intent("tutor")
    seen = c.until_phase("independent_check")
    assert "x² + 7x + 12" in next(e["payload"] for e in seen if e["type"] == "tutor_turn")["caption"]


# ------------------------------------------------------------------------ after completion


def test_after_completion_the_phase_stays_complete_and_nothing_is_recorded_twice(make):
    c = make(script(qa=[json.dumps({"answer_spoken": "Sure.", "board_text": [], "scene_request": None, "followups": [], "resolves_to_step": None})],
                    orchestrator=[]))
    c.open("quad_5_6")
    c.until_phase("ready")
    finish_lesson_unhelped(c)
    c.intent("quiz_answer", "x=-4 or x=-3")
    c.until_phase("complete")
    c.intent("ask", "why does that work?")
    seen = c.until(lambda e: e["type"] == "state" and e["payload"]["phase"] != "tutoring")
    assert seen[-1]["payload"]["phase"] == "complete"
    c.intent("show_answer")
    c.until(lambda e: e["type"] == "state" and e["payload"]["phase"] != "tutoring")
    assert len(c.tc.get(f"/v2/sessions/{c.sid}/summary").json()["items"]) == 2  # lesson + transfer, not a third


# ---------------------------------------------------------------------------- stale readings


def test_a_rewritten_line_is_read_again_not_taken_from_the_old_confirmation(make):
    c = make(script(vision_lines=[
        lines_json(reading("(x + 2)(x + 3) = 0", True, ["(x + 2)(x + 3) = 0", "(x + 2)(x - 3) = 0"])),
        lines_json(reading("(x - 2)(x - 3) = 0")),
    ]))
    c.open("quad_5_6")
    c.until_phase("ready")
    board_at(c, 1, 40, 170, image=png(lines=1))
    c.intent("check")
    rb = c.until_type("read_back")[-1]
    c.until_phase("confirm")
    c.send("read_back_confirm", request_id=rb["request_id"], accepted=True, text="(x + 2)(x + 3) = 0")
    c.until_phase("student_turn")
    from PIL import Image, ImageDraw
    import io

    img = Image.new("RGB", (1200, 640), "white")
    ImageDraw.Draw(img).text((40, 180), "something else entirely", fill="black")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    board_at(c, 2, 40, 170, image=buf.getvalue())  # the student rewrote line 0
    c.intent("check")
    seen = c.until_phase("student_turn")
    ev = next(e["payload"] for e in seen if e["type"] == "evidence")
    assert ev["verdict"] == "invalid"  # graded on the NEW writing
    assert [x.agent for x in c.llm.calls].count("vision_lines") == 2


def test_check_against_an_older_snapshot_than_the_intent_revision_is_not_graded(make):
    c = make(script(vision_lines=[]))
    c.open("quad_5_6")
    c.until_phase("ready")
    board_at(c, 1, 40, 170)
    c.rev = 2  # the student wrote again but the iPad has not uploaded revision 2
    c.intent("check")
    turn = c.until_type("tutor_turn")[-1]["payload"]
    assert turn["move"] == "clarify" and "latest" in turn["caption"].lower()
    assert [x.agent for x in c.llm.calls].count("vision_lines") == 0


# -------------------------------------------------------------------- input hardening


@pytest.mark.parametrize("lines", [[1, 2], ["a"], [{"bbox": [0, 0, -5, 10]}], [{"bbox": [0, 0, 10]}], [{"bbox": ["x", 0, 1, 1]}], [{"bbox": [0, 0, 1e999, 1]}]])
def test_hostile_board_meta_is_a_422_not_a_500(make, lines):
    c = make()
    c.open("quad_5_6")
    m = json.dumps({"board_revision": 1, "request_id": "z", "image_region": [0, 0, 1200, 640], "lines": lines})
    r = c.tc.post(f"/v2/sessions/{c.sid}/board", data={"meta": m}, files={"image": ("b.png", png(), "image/png")})
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("region", [[0, 0, 0, 0], [0, 0, -1, 5], [0, 0, "a", 5], [0, 0, 1e999, 1]])
def test_hostile_image_region_is_a_422(make, region):
    c = make()
    c.open("quad_5_6")
    m = json.dumps({"board_revision": 1, "request_id": "z", "image_region": region, "lines": []})
    r = c.tc.post(f"/v2/sessions/{c.sid}/board", data={"meta": m}, files={"image": ("b.png", png(), "image/png")})
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("board", [{"w": -5, "h": 0}, {"w": "x", "h": 1}, {"w": 1e999, "h": 5}, {"w": 1, "h": 1}, {"w": 10 ** 9, "h": 10 ** 9}])
def test_hostile_board_size_never_breaks_session_creation(make, board):
    c = make()
    raw = json.dumps({"preset_id": "quad_5_6", "board": board}).encode()  # allow_nan: Infinity reaches the server
    r = c.tc.post("/v2/sessions", content=raw, headers={"Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    service_session = c.service.get(sid)
    box = service_session.placer.layout.board_bounds
    assert 300 <= box.w <= 5000 and 200 <= box.h <= 5000


def test_a_non_ascii_credential_is_a_401_not_a_500(math_worker):
    app = create_app(TutorService(FakeLLM({}), math_worker), api_token="s3cret")
    with TestClient(app, raise_server_exceptions=False) as tc:
        r = tc.post("/v2/sessions", json={}, headers={"Authorization": "Bearer café".encode("latin-1")})
        assert r.status_code == 401
        sid = tc.post("/v2/sessions", json={}, headers={"Authorization": "Bearer s3cret"}).json()["session_id"]
        with pytest.raises(Exception):
            with tc.websocket_connect(f"/v2/sessions/{sid}/events?token=caf%C3%A9"):
                pass


def test_an_oversized_upload_is_refused_before_it_is_parsed(make):
    c = make()
    c.open("quad_5_6")
    big = b"\x89PNG\r\n\x1a\n" + b"0" * (9 * 1024 * 1024)
    r = c.tc.post(f"/v2/sessions/{c.sid}/question", data={"board_revision": "0", "request_id": "q"}, files={"image": ("q.png", big, "image/png")})
    assert r.status_code == 413


def test_pending_readbacks_and_stored_audio_are_bounded(make):
    c = make()
    c.open("quad_5_6")
    s = c.service.get(c.sid)
    from tutor.v2.session import MAX_AUDIO_BYTES, MAX_PENDING_READBACKS

    for i in range(MAX_PENDING_READBACKS + 20):
        s.add_pending(f"r{i}", {"kind": "transcript", "text": "x"})
    assert len(s.pending_readbacks) == MAX_PENDING_READBACKS
    for i in range(10):
        s.store_audio(f"c{i}", b"0" * (MAX_AUDIO_BYTES // 3))
    assert sum(len(v) for v in s.audio.values()) <= MAX_AUDIO_BYTES


def test_a_cursor_beyond_the_log_does_not_silence_the_stream(make):
    c = make()
    c.open("quad_5_6", after_seq=10_000)
    first = c.next()
    assert first["type"] == "state"
    c.intent("hint")
    assert c.until_type("tutor_turn")


# ------------------------------------------------------------------ races and lifecycle


def test_deleting_a_session_closes_its_sockets(make):
    c = make()
    c.open("quad_5_6")
    c.until_phase("ready")
    from starlette.websockets import WebSocketDisconnect

    assert c.tc.delete(f"/v2/sessions/{c.sid}").status_code == 200
    with pytest.raises(WebSocketDisconnect):  # closed by the server, not just silent
        for _ in range(5):
            c._recv(timeout=2)


def test_a_new_question_cancels_the_running_turn_of_the_old_problem(make):
    c = make(script(orchestrator=[__import__("sample_data").ORCHESTRATOR_OK]), delay=0.3)
    c.open("quad_5_6")
    c.until_phase("ready")
    c.intent("tutor")
    t = c.until_phase("tutoring")[-1]["turn_id"]
    c.question(text="Solve x^2 + 5x + 6 = 0")
    seen = c.until_phase("ready")
    cancelled = [e for e in seen if e["type"] == "turn_cancelled"]
    assert cancelled and cancelled[0]["turn_id"] == t
    c.llm.delay = 0
    assert c.service.get(c.sid).running_turn is None


def test_a_new_problem_forgets_pending_readbacks_of_the_old_one(make):
    c = make(script(orchestrator=[__import__("sample_data").ORCHESTRATOR_OK]))
    c.open("quad_5_6")
    c.until_phase("ready")
    s = c.service.get(c.sid)
    s.add_pending("old", {"kind": "board", "text": "x", "line": 0})
    c.question(text="Solve x^2 + 5x + 6 = 0")
    c.until_phase("ready")
    assert "old" not in s.pending_readbacks


def test_skills_are_not_duplicated_when_a_step_title_has_punctuation(make):
    c = make()
    c.open("quad_5_6")
    c.until_phase("ready")
    s = c.service.get(c.sid)
    s.skills.seed("Find two numbers that multiply to 6, and add to 5!")
    s.skills.mark_assisted("Find two numbers that multiply to 6, and add to 5!")
    assert len(s.skills.skills) == 1


def test_concurrent_duplicate_intents_run_once(math_worker):
    async def go():
        llm = FakeLLM(script(), delay=0.2)
        service = TutorService(llm, math_worker, narrator=Narrator(FakeTTS()))
        app = create_app(service)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
            sid = (await http.post("/v2/sessions", json={"preset_id": "quad_5_6"})).json()["session_id"]
            await http.post(f"/v2/sessions/{sid}/intent", json={"action": "tutor", "board_revision": 0, "request_id": "a"})
            await asyncio.sleep(0.05)  # a turn is running, so starting another one has to await the cancellation
            body = {"action": "hint", "board_revision": 0, "request_id": "dup"}
            await asyncio.gather(*[http.post(f"/v2/sessions/{sid}/intent", json=body) for _ in range(3)])
            await asyncio.sleep(0.5)
            return service.get(sid).turn_no

    assert asyncio.run(go()) == 2  # the tutor turn + ONE hint turn
