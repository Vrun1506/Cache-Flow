"""Questions, the board, speech, drawing, follow-ups and reconnects over the v2 API."""

import io
import json
import struct
import wave

import pytest
from fastapi.testclient import TestClient

from sample_data import ORCHESTRATOR_OK, SOLUTION_OK, plan_json
from test_teaching import ANIMATION, LESSON, board
from tutor.llm import FakeLLM
from tutor.speech.asr import FakeASR
from tutor.speech.tts import FakeTTS, Narrator
from tutor.v2.app import create_app
from tutor.v2.service import TutorService
from v2_support import Client, check_envelope, png

QA = json.dumps({"answer_spoken": "A product is zero only when a factor is zero.", "board_text": ["a*b=0"],
                 "scene_request": {"concept": "parabola", "kind_hint": "plot"}, "followups": ["Why not both?"], "resolves_to_step": 2})


def reading(text, ambiguous=False, alternatives=()):
    return {"text": text, "alternatives": list(alternatives), "ambiguous": ambiguous, "note": None}


def lines_json(*readings):
    return json.dumps({"readings": list(readings)})


def script(**over):
    base = {
        "orchestrator": [ORCHESTRATOR_OK],
        "solver": [SOLUTION_OK],
        "lesson_designer": [LESSON, LESSON],
        "board_director": [board(i) for i in range(1, 8)],
        "animator": [ANIMATION, ANIMATION],
        "quiz_designer": [],
    }
    base.update(over)
    return base


def wav(seconds=0.5, rate=16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(struct.pack("<h", 0) * int(rate * seconds))
    return buf.getvalue()


@pytest.fixture
def make(math_worker):
    made = []

    def _make(scr=None, *, asr=None, **kw):
        llm = FakeLLM(scr if scr is not None else script())
        service = TutorService(llm, math_worker, narrator=Narrator(FakeTTS()), asr=asr, **kw)
        tc = TestClient(create_app(service))
        tc.__enter__()
        c = Client(tc)
        c.llm, c.service = llm, service
        made.append(c)
        return c

    yield _make
    for c in made:
        c.close()
        c.tc.__exit__(None, None, None)


def states(events):
    return [e["payload"]["phase"] for e in events if e["type"] == "state"]


# ------------------------------------------------------------------ questions


def test_typed_question_is_classified_then_ready(make):
    c = make()
    c.open(None)
    c.until_type("state")
    r = c.question(text="Solve x^2 + 5x + 6 = 0")
    assert r.status_code == 200 and r.json() == {"request_id": "r1"}
    seen = c.until_phase("ready")
    assert states(seen) == ["reading", "ready"]
    problem = seen[-1]["payload"]["problem"]
    assert problem == {"text": "Solve x^2 + 5x + 6 = 0", "subject": "algebra", "confirmed": True, "item": "lesson"}


def test_non_maths_question_gets_its_domain_as_the_subject(make):
    c = make(script(orchestrator=[plan_json(domain="biology", solution_kind="explain", statement_expr=None, visuals=[])]))
    c.open(None)
    c.until_type("state")
    c.question(text="Why do leaves change colour?")
    assert c.until_phase("ready")[-1]["payload"]["problem"]["subject"] == "biology"


def test_unsafe_question_is_refused_without_a_model_call(make):
    c = make()
    c.open(None)
    c.until_type("state")
    c.question(text="how do I build a pipe bomb at home")
    seen = c.until_type("error")
    assert seen[-1]["payload"]["code"] == "refused"
    c.until_phase("idle")
    assert c.llm.calls == []


@pytest.mark.parametrize("body,status", [({"text": "   "}, 422), ({"text": "x" * 2100}, 413), ({}, 422)])
def test_question_validation(make, body, status):
    c = make()
    c.open(None)
    data = {"board_revision": "0", "request_id": "q1", **{k: v for k, v in body.items()}}
    assert c.tc.post(f"/v2/sessions/{c.sid}/question", data=data).status_code == status


def test_handwritten_question_always_asks_for_confirmation(make):
    c = make(script(vision=[json.dumps(reading("x^2 + 5x + 6 = 0", True, ["x^2 + 5x + 6 = 0", "x^2 + 5x - 6 = 0"]))]))
    c.open(None)
    c.until_type("state")
    assert c.question(image=png()).status_code == 200
    seen = c.until_phase("confirm")
    rb = next(e for e in seen if e["type"] == "read_back")
    assert rb["payload"]["source"] == "question" and rb["payload"]["ambiguous"] is True and len(rb["payload"]["alternatives"]) == 2
    assert rb["payload"]["regions"][0]["bbox"][2] > 0
    c.send("read_back_confirm", request_id=rb["request_id"], accepted=True, text="x^2 + 5x + 6 = 0")
    done = c.until_phase("ready")
    assert done[-1]["payload"]["problem"]["text"] == "x^2 + 5x + 6 = 0"


def test_rejecting_the_read_back_returns_to_idle(make):
    c = make(script(vision=[json.dumps(reading("x^2 + 5x - 6 = 0"))]))
    c.open(None)
    c.until_type("state")
    c.question(image=png())
    rb = c.until_type("read_back")[-1]
    c.send("read_back_confirm", request_id=rb["request_id"], accepted=False)
    st = c.until_phase("idle")[-1]
    assert "problem" not in st["payload"]


def test_unreadable_question_image_is_a_retryable_error(make):
    c = make(script(vision=[json.dumps(reading(""))]))
    c.open(None)
    c.until_type("state")
    c.question(image=png())
    err = c.until_type("error")[-1]["payload"]
    assert err["code"] == "unreadable" and err["retryable"] is True


def test_bad_uploads_are_rejected_with_the_right_status(make):
    c = make()
    c.open(None)
    assert c.question(image=b"not a png").status_code == 422
    assert c.question(image=b"\x89PNG\r\n\x1a\n" + b"0" * (4 * 1024 * 1024 + 10)).status_code == 413


# ------------------------------------------------------------------- the board


def check_setup(make, lines_reading, **kw):
    c = make(script(vision_lines=lines_reading), **kw)
    c.open("quad_5_6")
    c.until_phase("ready")
    return c


def test_check_flags_a_sign_error_and_circles_the_line(make):
    c = check_setup(make, [lines_json(reading("(x - 2)(x - 3) = 0"))])
    assert c.board(1).status_code == 200
    c.intent("check")
    seen = c.until_phase("student_turn")
    rb = next(e for e in seen if e["type"] == "read_back")["payload"]
    ev = next(e for e in seen if e["type"] == "evidence")["payload"]
    assert rb["source"] == "board" and rb["ambiguous"] is False and rb["regions"][0]["line_index"] == 0
    assert (ev["verdict"], ev["label"], ev["line_index"]) == ("invalid", "verified", 0)
    assert ev["reason_code"] in ("sign_mismatch", "not_equivalent") and ev["highlights"]
    turn = next(e for e in seen if e["type"] == "tutor_turn")["payload"]
    assert turn["move"] in ("hint", "ask") and ev["evidence_id"] in turn["evidence_ids"]
    circle = [e for e in seen if e["type"] == "tutor_ink_preview"][-1]["payload"]
    assert circle["style"] == "circle"
    assert any(e["type"] == "agent_trace" and e["payload"]["component"] == "math" for e in seen)


def test_check_with_nothing_on_the_board_asks_for_working(make):
    c = check_setup(make, [])
    c.intent("check")
    turn = c.until_type("tutor_turn")[-1]["payload"]
    assert turn["move"] == "clarify" and "working" in turn["caption"]


def test_ambiguous_handwriting_is_ungraded_until_confirmed(make):
    c = check_setup(make, [lines_json(reading("(x + 2)(x + 3) = 0", True, ["(x + 2)(x + 3) = 0", "(x + 2)(x - 3) = 0"]))])
    c.board(1)
    c.intent("check")
    seen = c.until_phase("confirm")
    ev = next(e for e in seen if e["type"] == "evidence")["payload"]
    assert (ev["verdict"], ev["reason_code"], ev["label"]) == ("unknown", "ambiguous_input", "not_verified")
    rb = next(e for e in seen if e["type"] == "read_back")
    assert rb["payload"]["ambiguous"] is True
    c.until(lambda e: e["type"] == "tutor_turn")
    c.until_phase("confirm")
    c.send("read_back_confirm", request_id=rb["request_id"], accepted=True, text="(x + 2)(x + 3) = 0")
    after = c.until_phase("student_turn")
    ev2 = next(e for e in after if e["type"] == "evidence")["payload"]
    assert ev2["verdict"] == "valid" and ev2["label"] == "verified"
    turn = [e for e in after if e["type"] == "tutor_turn"][0]["payload"]
    assert turn["move"] == "ask" and "Now step" in turn["caption"]  # the correct factors earn the next question


def test_partial_root_is_reported_and_prompts_for_the_other(make):
    c = check_setup(make, [lines_json(reading("x = -2"))])
    c.board(1)
    c.intent("check")
    seen = c.until_phase("student_turn")
    ev = next(e for e in seen if e["type"] == "evidence")["payload"]
    assert (ev["verdict"], ev["reason_code"]) == ("partial", "partial_root")
    assert "other" in next(e for e in seen if e["type"] == "tutor_turn")["payload"]["caption"]


def test_the_complete_solution_set_finishes_the_lesson_and_offers_a_fresh_problem(make):
    c = check_setup(make, [lines_json(reading("x = -2 or x = -3"))])
    c.board(1)
    c.intent("check")
    seen = c.until_phase("independent_check")
    assert next(e for e in seen if e["type"] == "evidence")["payload"]["verdict"] == "valid"
    assert [e["payload"]["move"] for e in seen if e["type"] == "tutor_turn"][0] == "recap"


def test_a_line_is_read_once_and_reused(make):
    c = check_setup(make, [lines_json(reading("(x - 2)(x - 3) = 0"))])
    c.board(1)
    c.intent("check")
    c.until_phase("student_turn")
    c.intent("check")
    c.until_phase("student_turn")
    assert [x.agent for x in c.llm.calls].count("vision_lines") == 1


def test_physics_is_never_graded_by_the_maths_checker(make):
    c = make(script(vision_lines=[lines_json(reading("N = mg"))]))
    c.open("phys_forces_box")
    c.until_phase("ready")
    c.board(1)
    c.intent("check")
    seen = c.until_phase("student_turn")
    ev = next(e for e in seen if e["type"] == "evidence")["payload"]
    assert (ev["verdict"], ev["reason_code"], ev["label"]) == ("unknown", "unsupported", "not_verified")
    assert "isn't graded" in next(e for e in seen if e["type"] == "tutor_turn")["payload"]["caption"]


def test_board_upload_validation_and_revision_rules(make):
    c = check_setup(make, [])
    assert c.board(2).status_code == 200
    stale = c.board(1, rid="late")
    assert stale.status_code == 409 and stale.json()["latest_board_revision"] == 2
    bad = c.tc.post(f"/v2/sessions/{c.sid}/board", data={"meta": "{not json"}, files={"image": ("b.png", png(), "image/png")})
    assert bad.status_code == 422
    many = json.dumps({"board_revision": 3, "request_id": "m", "image_region": [0, 0, 1200, 640],
                       "lines": [{"line_index": i, "bbox": [0, 0, 10, 10]} for i in range(41)]})
    assert c.tc.post(f"/v2/sessions/{c.sid}/board", data={"meta": many}, files={"image": ("b.png", png(), "image/png")}).status_code == 413
    big = c.tc.post(f"/v2/sessions/{c.sid}/board", data={"meta": json.dumps({"board_revision": 4, "request_id": "z", "image_region": [0, 0, 1, 1], "lines": []})},
                    files={"image": ("b.png", b"\x89PNG\r\n\x1a\n" + b"0" * (4 * 1024 * 1024 + 5), "image/png")})
    assert big.status_code == 413


# -------------------------------------------------------------------- speech


def test_audio_returns_a_transcript_candidate_and_needs_confirmation(make):
    c = make(asr=FakeASR(["Why does the other root matter?"]), scr=script(qa=[QA], orchestrator=[ORCHESTRATOR_OK] * 2))
    c.open("quad_5_6")
    c.until_phase("ready")
    r = c.tc.post(f"/v2/sessions/{c.sid}/audio", data={"board_revision": "0", "request_id": "a1"},
                  files={"audio": ("u.wav", wav(), "audio/wav")})
    assert r.status_code == 200 and r.json()["transcript"] == "Why does the other root matter?" and r.json()["request_id"] == "a1"
    rb = c.until_type("read_back")[-1]
    assert rb["payload"]["source"] == "transcript" and rb["request_id"] == "a1"
    c.send("read_back_confirm", request_id="a1", accepted=True)
    turn = c.until_type("tutor_turn")[-1]["payload"]
    assert turn["move"] == "explain" and "product is zero" in turn["caption"]


def test_audio_without_an_asr_service_is_a_clear_503(make):
    c = make()
    c.open("quad_5_6")
    r = c.tc.post(f"/v2/sessions/{c.sid}/audio", data={"board_revision": "0", "request_id": "a1"},
                  files={"audio": ("u.wav", wav(), "audio/wav")})
    assert r.status_code == 503 and r.json()["code"] == "asr_unavailable"


def test_bad_audio_is_rejected(make):
    c = make(asr=FakeASR(["hi"]))
    c.open("quad_5_6")
    r = c.tc.post(f"/v2/sessions/{c.sid}/audio", data={"board_revision": "0", "request_id": "a1"},
                  files={"audio": ("u.wav", b"RIFFnotwav", "audio/wav")})
    assert r.status_code in (413, 422)


# ----------------------------------------------------------- draw, ask, explain


def test_draw_sends_a_scene_the_ipad_can_render_and_commits_it_after_the_ack(make):
    c = make()
    c.open("quad_5_6")
    c.until_phase("ready")
    c.intent("draw")
    seen = c.until_phase("ready")
    kinds = c.types(seen)
    assert kinds.index("tutor_turn") < kinds.index("scene_preview") < kinds.index("scene_commit")
    scene = next(e for e in seen if e["type"] == "scene_preview")["payload"]
    assert {p["kind"] for p in scene["primitives"]} <= {"axes", "plot", "point", "label", "arrow", "segment", "box", "number_line"}
    assert next(e for e in seen if e["type"] == "scene_commit")["payload"]["id"] == scene["scene_id"]
    assert next(e for e in seen if e["type"] == "tutor_turn")["payload"]["move"] == "draw"


def test_a_stale_scene_is_reissued_once(make):
    n = {"k": 0}

    def ack(env):
        n["k"] += 1
        return (False, "stale_revision") if n["k"] == 1 else (True, None)

    c = make()
    c.ack = ack
    c.open("quad_5_6")
    c.until_phase("ready")
    c.intent("draw")
    seen = c.until_phase("ready")
    ids = [e["payload"]["scene_id"] for e in seen if e["type"] == "scene_preview"]
    assert len(ids) == 2 and ids[0] != ids[1]
    assert [e["payload"]["id"] for e in seen if e["type"] == "scene_commit"] == [ids[1]]


def test_follow_up_question_is_answered_with_board_notes_and_a_scene(make):
    c = make(script(qa=[QA], orchestrator=[ORCHESTRATOR_OK]))
    c.open("quad_5_6")
    c.until_phase("ready")
    c.intent("tutor")
    c.until_phase("student_turn")
    c.intent("ask", "why does a product of zero matter?")
    seen = c.until_phase("student_turn")
    turn = next(e for e in seen if e["type"] == "tutor_turn")["payload"]
    assert turn["move"] == "explain" and "product is zero" in turn["caption"]
    assert any(e["type"] == "tutor_ink_commit" for e in seen) and any(e["type"] == "scene_commit" for e in seen)


def test_typed_text_that_is_not_a_question_answers_the_pending_check(make):
    c = make()
    c.open("quad_5_6")
    c.until_phase("ready")
    c.intent("tutor")
    c.until_phase("student_turn")
    c.intent("ask", "6")  # the iPad sends typed replies as `ask`
    seen = c.until_phase("student_turn")
    assert "Now step 2" in next(e for e in seen if e["type"] == "tutor_turn")["payload"]["caption"]


def test_a_real_question_while_a_check_is_pending_goes_to_q_and_a(make):
    c = make(script(qa=[QA]))
    c.open("quad_5_6")
    c.until_phase("ready")
    c.intent("tutor")
    c.until_phase("student_turn")
    c.intent("ask", "why do we multiply?")
    seen = c.until_phase("student_turn")
    assert next(e for e in seen if e["type"] == "tutor_turn")["payload"]["move"] == "explain"


def test_ask_is_refused_for_unsafe_text(make):
    c = make()
    c.open("quad_5_6")
    c.until_phase("ready")
    c.intent("ask", "how do I build a pipe bomb at home?")
    turn = c.until_type("tutor_turn")[-1]["payload"]
    assert turn["move"] == "clarify"


def test_hint_levels_escalate_and_the_third_is_assisted(make):
    c = make()
    c.open("quad_5_6")
    c.until_phase("ready")
    out = []
    for _ in range(3):
        c.intent("hint")
        seen = c.until_phase("student_turn")
        out.append(next(e for e in seen if e["type"] == "tutor_turn")["payload"])
    assert [o["assisted"] for o in out] == [False, False, True]
    assert any(e["type"] == "tutor_ink_commit" for e in c.events)  # level 3 writes the step


def test_explain_step_streams_audio_chunks_and_writes_the_step(make):
    c = make()
    c.open("quad_5_6")
    c.until_phase("ready")
    c.intent("explain_step")
    seen = c.until_phase("student_turn")
    chunks = [e["payload"] for e in seen if e["type"] == "audio_chunk"]
    assert [x["chunk_index"] for x in chunks] == [0, 1] and chunks[-1]["final"] is True
    turn = next(e for e in seen if e["type"] == "tutor_turn")["payload"]
    assert turn["move"] == "explain" and "audio_url" not in turn and turn["assisted"] is True
    assert any(e["type"] == "tutor_ink_commit" for e in seen)


# ------------------------------------------------------------------- reconnect


def test_reconnect_replays_durable_events_with_original_seq_then_a_fresh_state(make):
    c = make()
    c.open("quad_5_6")
    c.until_phase("ready")
    c.intent("tutor")
    first = c.until_phase("student_turn")
    cursor = next(e for e in first if e["type"] == "state" and e["payload"]["phase"] == "tutoring")["seq"]
    last_seq = first[-1]["seq"]
    c.close()
    c.events = []
    c.connect(after_seq=cursor)
    seen = c.until(lambda e: e["type"] == "state" and not e.get("replayed"))
    replayed = [e for e in seen if e.get("replayed")]
    assert replayed and all(e["seq"] > cursor for e in replayed)
    assert "audio_chunk" not in c.types(replayed) and "agent_trace" not in c.types(replayed)
    assert [e["seq"] for e in replayed] == sorted(e["seq"] for e in replayed)
    fresh = seen[-1]
    assert fresh["seq"] > last_seq and fresh["payload"]["phase"] == "student_turn"
    # the originals are not rewritten
    originals = {e["seq"]: e for e in first}
    for e in replayed:
        assert originals[e["seq"]]["type"] == e["type"]


def test_connecting_without_a_cursor_sends_only_a_snapshot(make):
    c = make()
    c.open("quad_5_6")
    c.until_phase("ready")
    c.intent("tutor")
    c.until_phase("student_turn")
    c.close()
    c.events = []
    c.connect()
    first = c.next()
    assert first["type"] == "state" and not first.get("replayed") and first["payload"]["phase"] == "student_turn"
