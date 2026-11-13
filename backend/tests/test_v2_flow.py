"""The Socratic flow end to end over the v2 HTTP + WebSocket API, with a scripted model."""

import json

import pytest
from fastapi.testclient import TestClient

from sample_data import SOLUTION_OK, ORCHESTRATOR_OK
from test_teaching import ANIMATION, LESSON, board
from tutor.llm import FakeLLM
from tutor.speech.tts import FakeTTS, Narrator
from tutor.v2.app import create_app
from tutor.v2.service import TutorService
from v2_support import Client, png


def script(**over):
    base = {
        "orchestrator": [ORCHESTRATOR_OK],
        "solver": [SOLUTION_OK],
        "lesson_designer": [LESSON, LESSON],
        "board_director": [board(1), board(2), board(3), board(4), board(5)],
        "animator": [ANIMATION],
    }
    base.update(over)
    return base


@pytest.fixture
def make(math_worker):
    made = []

    def _make(scr=None, *, ack=None, delay=0.0, tts=True, **kw):
        llm = FakeLLM(scr if scr is not None else script(), delay=delay)
        service = TutorService(llm, math_worker, narrator=Narrator(FakeTTS() if tts else None), **kw)
        tc = TestClient(create_app(service))
        tc.__enter__()
        client = Client(tc, ack=ack)
        client.llm, client.service = llm, service
        made.append(client)
        return client

    yield _make
    for c in made:
        c.close()
        c.tc.__exit__(None, None, None)


def opened(make, **kw):
    c = make(**kw)
    info = c.open("quad_5_6")
    assert c.until_phase("ready")
    return c, info


def start_lesson(c):
    c.intent("tutor")
    return c.until_phase("student_turn")


def test_create_session_matches_the_contract(make):
    c = make()
    info = c.open("quad_5_6")
    assert info["protocol_version"] == 2 and info["board_revision"] == 0 and info["session_id"].startswith("s-")
    first = c.until_type("state")[-1]
    assert first["payload"]["phase"] == "ready"
    assert first["payload"]["problem"] == {"text": "x² + 5x + 6 = 0", "subject": "algebra", "confirmed": True, "item": "lesson"}


def test_session_without_preset_starts_idle(make):
    c = make()
    c.open(None)
    st = c.until_type("state")[-1]
    assert st["payload"]["phase"] == "idle" and "problem" not in st["payload"]


def test_tutor_me_traces_the_agents_then_asks_a_question(make):
    c, _ = opened(make)
    assert c.intent("tutor").json() == {"request_id": "r1"}
    seen = c.until_phase("student_turn")
    types = c.types(seen)
    assert types[0] == "state" and seen[0]["payload"]["phase"] == "tutoring"
    assert "agent_trace" in types
    turn = next(e for e in seen if e["type"] == "tutor_turn")
    assert turn["payload"]["move"] == "ask" and turn["payload"]["audio_url"].startswith(f"/v2/sessions/{c.sid}/audio/")
    assert "two times three" in turn["payload"]["caption"].lower()
    assert turn["turn_id"] == "t1" and turn["run_id"] == "run-t1" and turn["request_id"] == "r1"
    seqs = [e["seq"] for e in seen]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    # the clip is downloadable
    assert c.tc.get(turn["payload"]["audio_url"]).headers["content-type"] == "audio/wav"


def test_caption_comes_before_the_ink_and_the_ink_needs_an_ack_to_commit(make):
    c, _ = opened(make)
    start_lesson(c)
    c.intent("quiz_answer", "6")
    seen = c.until_phase("student_turn")
    kinds = c.types(seen)
    assert kinds.index("tutor_turn") < kinds.index("tutor_ink_preview")
    previews = [e for e in seen if e["type"] == "tutor_ink_preview"]
    commits = [e for e in seen if e["type"] == "tutor_ink_commit"]
    assert previews and [x["payload"]["id"] for x in commits] == [p["payload"]["ink_id"] for p in previews]
    assert all(p["turn_id"] == "t2" for p in previews)


def test_unplaced_ink_is_never_committed(make):
    c, _ = opened(make, ack=lambda env: (False, "invalid"))
    start_lesson(c)
    c.intent("quiz_answer", "6")
    seen = c.until_phase("student_turn")
    assert any(e["type"] == "tutor_ink_preview" for e in seen)
    assert not any(e["type"] == "tutor_ink_commit" for e in seen)


def test_stale_ink_is_reissued_once_with_a_new_id(make):
    calls = {"n": 0}

    def ack(env):
        calls["n"] += 1
        return (False, "stale_revision") if calls["n"] == 1 else (True, None)

    c, _ = opened(make, ack=ack)
    start_lesson(c)
    c.intent("quiz_answer", "6")
    seen = c.until_phase("student_turn")
    previews = [e["payload"]["ink_id"] for e in seen if e["type"] == "tutor_ink_preview"]
    commits = [e["payload"]["id"] for e in seen if e["type"] == "tutor_ink_commit"]
    assert previews[0] != previews[1]  # the retry has its own id
    assert previews[0] not in commits and previews[1] in commits


def test_missing_ack_means_no_commit_and_the_turn_still_ends(make):
    c, _ = opened(make, ack_timeout_s=0.2)
    c.pending_acks = False
    start_lesson(c)
    c.intent("quiz_answer", "6")
    seen = c.until_phase("student_turn")
    assert not any(e["type"] == "tutor_ink_commit" for e in seen)


def test_wrong_answers_walk_the_hint_ladder_then_the_step_is_revealed(make):
    c, _ = opened(make)
    start_lesson(c)
    moves = []
    for _ in range(3):
        c.intent("quiz_answer", "7")
        seen = c.until_phase("student_turn")
        moves.append([e["payload"] for e in seen if e["type"] == "tutor_turn"][0])
    assert [m["move"] for m in moves] == ["hint", "hint", "explain"]
    assert moves[0]["assisted"] is False and moves[2]["assisted"] is True  # the reveal is help
    assert "multiplication" in moves[1]["caption"].lower()  # hint 2 is the narrower one
    # after the reveal the tutor has moved to the next question
    assert "two times three" in moves[2]["caption"].lower()


def test_correct_answer_moves_to_the_next_question(make):
    c, _ = opened(make)
    start_lesson(c)
    c.intent("quiz_answer", "6")
    seen = c.until_phase("student_turn")
    turn = [e["payload"] for e in seen if e["type"] == "tutor_turn"][0]
    assert turn["move"] == "ask" and "Now step 2" in turn["caption"]


def test_show_answer_is_an_assisted_move_then_a_fresh_independent_item(make):
    c, _ = opened(make)
    start_lesson(c)
    c.intent("show_answer")
    seen = c.until_phase("independent_check")
    turns = [e for e in seen if e["type"] == "tutor_turn"]
    assert turns[0]["payload"]["move"] == "show_answer" and turns[0]["payload"]["assisted"] is True
    assert any(e["type"] == "tutor_ink_commit" for e in seen)
    skills = [e for e in seen if e["type"] == "skills"][-1]["payload"]["skills"]
    assert skills and all(s["assisted"] for s in skills) and all(s["level"] == 0 for s in skills)
    state = [e for e in seen if e["type"] == "state"][-1]
    assert state["payload"]["problem"]["item"] == "independent_check"
    assert state["payload"]["problem"]["text"] == "x² + 7x + 12 = 0"
    ask = c.until(lambda e: e["type"] == "tutor_turn")
    assert "on your own" in ask[-1]["payload"]["caption"]


def test_the_transfer_item_is_independent_even_after_a_helped_lesson(make):
    """Same as the iPad's summary fixture: the lesson was helped, the fresh item was not."""
    c, _ = opened(make)
    start_lesson(c)
    c.intent("show_answer")
    c.until_phase("independent_check")
    c.until_type("tutor_turn")
    c.until_phase("independent_check")
    c.intent("quiz_answer", "x = -3 or x = -4")
    seen = c.until_phase("complete")
    ev = [e["payload"] for e in seen if e["type"] == "evidence"][0]
    assert ev["verdict"] == "valid" and ev["label"] == "verified" and ev["independent"] is True
    summary = c.tc.get(f"/v2/sessions/{c.sid}/summary").json()
    assert [(i["assisted"], i["independent"]) for i in summary["items"]] == [(True, False), (False, True)]
    helped = [s for s in summary["skills"] if s["assisted"]]
    assert helped, "the helped lesson is flagged on its skills"
    assert sum(s["independent_evidence"] for s in summary["skills"]) == 1  # only the transfer item counted


def test_helped_lesson_alone_never_raises_a_level(make):
    c, _ = opened(make)
    start_lesson(c)
    c.intent("show_answer")
    c.until_phase("independent_check")
    summary = c.tc.get(f"/v2/sessions/{c.sid}/summary").json()
    assert all(s["level"] == 0 and s["independent_evidence"] == 0 for s in summary["skills"])


def test_unhelped_run_raises_the_skill_level(make):
    c, _ = opened(make)
    start_lesson(c)
    for step in (1, 2, 3):
        c.intent("quiz_answer", "6")
        c.until_phase("student_turn" if step < 3 else "independent_check")
    c.until_type("tutor_turn")
    c.intent("quiz_answer", "x=-4 or x=-3")
    seen = c.until_phase("complete")
    ev = [e["payload"] for e in seen if e["type"] == "evidence"][0]
    assert ev["independent"] is True
    summary = c.tc.get(f"/v2/sessions/{c.sid}/summary").json()
    assert [i["independent"] for i in summary["items"]] == [True, True]
    assert any(s["level"] >= 1 and s["independent_evidence"] >= 1 for s in summary["skills"])


def test_partial_and_wrong_transfer_answers_keep_the_item_open(make):
    c, _ = opened(make)
    start_lesson(c)
    c.intent("show_answer")
    c.until_phase("independent_check")
    c.until_type("tutor_turn")
    c.until_phase("independent_check")
    c.intent("quiz_answer", "x = -3")
    seen = c.until_phase("independent_check")
    assert [e["payload"] for e in seen if e["type"] == "evidence"][0]["verdict"] == "partial"
    c.intent("quiz_answer", "x = 3 or x = 4")
    seen = c.until_phase("independent_check")
    assert [e["payload"] for e in seen if e["type"] == "evidence"][0]["verdict"] == "invalid"


def test_cancel_over_the_socket_ends_the_turn_with_a_boundary(make):
    c, _ = opened(make, delay=0.4)
    c.intent("tutor")
    st = c.until_phase("tutoring")[-1]
    c.send("cancel_turn", turn_id=st["turn_id"], request_id="r9")
    seen = c.until_type("turn_cancelled")
    assert seen[-1]["turn_id"] == st["turn_id"] and seen[-1]["payload"]["reason"] == "student_cancelled"
    after = c.until(lambda e: e["type"] == "state")
    assert after[-1]["payload"]["phase"] in ("ready", "student_turn")
    # nothing more from the cancelled turn
    c.llm.delay = 0.0
    c.intent("show_answer", rid="r20")
    rest = c.until(lambda e: e["type"] == "tutor_turn")
    assert all(e.get("turn_id") != st["turn_id"] for e in rest)


def test_cancel_over_http_is_equivalent(make):
    c, _ = opened(make, delay=0.4)
    c.intent("tutor")
    st = c.until_phase("tutoring")[-1]
    c.intent("cancel", turn_id=st["turn_id"])
    assert c.until_type("turn_cancelled")[-1]["turn_id"] == st["turn_id"]


def test_a_new_intent_supersedes_the_running_turn(make):
    c, _ = opened(make, delay=0.3)
    c.intent("tutor")
    first = c.until_phase("tutoring")[-1]["turn_id"]
    c.intent("ask", "why?")
    seen = c.until_type("turn_cancelled")
    assert seen[-1]["turn_id"] == first and seen[-1]["payload"]["reason"] == "superseded"


def test_requests_are_idempotent(make):
    c, _ = opened(make)
    a = c.intent("tutor", rid="same").json()
    b = c.intent("tutor", rid="same").json()
    assert a == b == {"request_id": "same"}
    c.until_phase("student_turn")
    assert c.service.get(c.sid).turn_no == 1


def test_stale_board_revision_is_a_409_with_the_latest(make):
    c, _ = opened(make)
    assert c.intent("hint", rev=5).status_code == 200
    r = c.intent("hint", rev=3)
    assert r.status_code == 409 and r.json()["latest_board_revision"] == 5


def test_intent_validation_and_unknown_actions(make):
    c, _ = opened(make)
    assert c.tc.post(f"/v2/sessions/{c.sid}/intent", json={"action": "tutor"}).status_code == 422
    c.intent("teleport")
    err = c.until_type("error")[-1]["payload"]
    assert err["code"] == "unsupported_action" and err["retryable"] is False


def test_no_problem_yet_is_a_plain_error(make):
    c = make()
    c.open(None)
    c.until_type("state")
    c.intent("tutor")
    assert c.until_type("error")[-1]["payload"]["code"] == "no_problem"


def test_unknown_session_is_404(make):
    c = make()
    assert c.tc.post("/v2/sessions/nope/intent", json={"action": "tutor", "board_revision": 0, "request_id": "r"}).status_code == 404
    assert c.tc.get("/v2/sessions/nope/summary").status_code == 404


def test_protocol_header_and_token(make, math_worker):
    c = make()
    assert c.tc.post("/v2/sessions", json={}, headers={"X-Socrates-Protocol": "1"}).status_code == 426
    assert c.tc.post("/v2/sessions", json={}, headers={"X-Socrates-Protocol": "2"}).status_code == 200
    guarded = TestClient(create_app(TutorService(FakeLLM({}), math_worker), api_token="s3cret"))
    with guarded:
        assert guarded.post("/v2/sessions", json={}).status_code == 401
        assert guarded.post("/v2/sessions", json={}, headers={"Authorization": "Bearer nope"}).status_code == 401
        ok = guarded.post("/v2/sessions", json={}, headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200
        assert guarded.get("/v2/health").status_code == 200  # health stays open for the iPad's status dot
        with pytest.raises(Exception):
            with guarded.websocket_connect(f"/v2/sessions/{ok.json()['session_id']}/events"):
                pass
        with guarded.websocket_connect(f"/v2/sessions/{ok.json()['session_id']}/events?token=s3cret") as ws:
            assert ws.receive_json()["type"] == "state"


def test_health_reports_every_component_the_ipad_lists(make):
    c = make()
    body = c.tc.get("/v2/health").json()
    assert body["protocol_version"] == 2
    assert {"session_api", "vllm", "agent", "math", "guardrails", "tts", "asr"} >= {"session_api", "agent", "math", "guardrails", "tts", "asr"}
    for name in ("session_api", "agent", "math", "guardrails", "tts", "asr"):
        assert isinstance(body["components"][name]["ok"], bool)
    assert body["components"]["asr"]["ok"] is False  # none configured here: reported honestly
    assert body["components"]["math"]["ok"] is True


def test_delete_session(make):
    c, _ = opened(make)
    assert c.tc.delete(f"/v2/sessions/{c.sid}").json()["deleted"] is True
    assert c.tc.get(f"/v2/sessions/{c.sid}/summary").status_code == 404
