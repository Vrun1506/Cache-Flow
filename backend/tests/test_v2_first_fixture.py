"""Interface.md "First fixture", driven through the real API: sign error, correction, partial root, Show the answer, transfer."""

import json

import pytest
from fastapi.testclient import TestClient

from sample_data import ORCHESTRATOR_OK, SOLUTION_OK
from test_teaching import LESSON, board
from test_v2_inputs import lines_json, reading
from tutor.llm import FakeLLM
from tutor.speech.tts import FakeTTS, Narrator
from tutor.v2.app import create_app
from tutor.v2.service import TutorService
from v2_support import Client


@pytest.fixture
def client(math_worker):
    llm = FakeLLM({
        "solver": [SOLUTION_OK], "lesson_designer": [LESSON, LESSON], "board_director": [board(i) for i in range(1, 10)],
        "vision_lines": [lines_json(reading("(x - 2)(x - 3) = 0")), lines_json(reading("(x + 2)(x + 3) = 0")),
                         lines_json(reading("x = -2"))],
    })
    tc = TestClient(create_app(TutorService(llm, math_worker, narrator=Narrator(FakeTTS()))))
    tc.__enter__()
    c = Client(tc)
    c.llm = llm
    yield c
    c.close()
    tc.__exit__(None, None, None)


def line(c, index):
    """The student writes another line (a new board revision) and taps Check."""
    c.rev += 1
    meta = {"board_revision": c.rev, "request_id": c.rid(), "image_region": [0, 0, 1200, 640],
            "lines": [{"line_index": i, "bbox": [40, 170 + 60 * i, 300, 50]} for i in range(index + 1)]}
    from v2_support import png

    r = c.tc.post(f"/v2/sessions/{c.sid}/board", data={"meta": json.dumps(meta)}, files={"image": ("b.png", png(lines=index + 1), "image/png")})
    assert r.status_code == 200
    c.intent("check")


def test_the_first_fixture(client):
    c = client
    c.open("quad_5_6")
    c.until_phase("ready")

    # Tutor me: the tutor asks, it does not tell.
    c.intent("tutor")
    opening = [e for e in c.until_phase("student_turn") if e["type"] == "tutor_turn"][0]["payload"]
    assert opening["move"] == "ask"

    # (x − 2)(x − 3) = 0 → invalid, and the sign is the problem; the tutor circles it.
    line(c, 0)
    seen = c.until_phase("student_turn")
    ev = next(e["payload"] for e in seen if e["type"] == "evidence")
    assert (ev["step_kind"], ev["verdict"], ev["label"]) == ("factorization", "invalid", "verified")
    assert ev["reason_code"] == "sign_mismatch"
    assert next(e["payload"] for e in seen if e["type"] == "tutor_turn")["move"] == "hint"
    assert [e["payload"]["style"] for e in seen if e["type"] == "tutor_ink_preview"][-1] == "circle"

    # (x + 2)(x + 3) = 0 → valid; the tutor moves on to the next question.
    line(c, 1)
    seen = c.until_phase("student_turn")
    ev = next(e["payload"] for e in seen if e["type"] == "evidence")
    assert (ev["verdict"], ev["reason_code"], ev["independent"]) == ("valid", "ok", False)
    assert next(e["payload"] for e in seen if e["type"] == "tutor_turn")["move"] == "ask"

    # x = −2 → partial root; the tutor asks for the other one.
    line(c, 2)
    seen = c.until_phase("student_turn")
    ev = next(e["payload"] for e in seen if e["type"] == "evidence")
    assert (ev["step_kind"], ev["verdict"], ev["reason_code"]) == ("candidate_root", "partial", "partial_root")
    assert "other" in next(e["payload"] for e in seen if e["type"] == "tutor_turn")["caption"]

    # Show the answer: explicit, assisted, written on the board, then one independent transfer item.
    c.intent("show_answer")
    seen = c.until_phase("independent_check")
    turn = next(e["payload"] for e in seen if e["type"] == "tutor_turn")
    assert turn["move"] == "show_answer" and turn["assisted"] is True
    assert any(e["type"] == "tutor_ink_commit" for e in seen)
    c.until_type("tutor_turn")
    c.until_phase("independent_check")
    c.intent("quiz_answer", "x = -3 or x = -4")
    seen = c.until_phase("complete")
    ev = next(e["payload"] for e in seen if e["type"] == "evidence")
    assert (ev["verdict"], ev["independent"]) == ("valid", True)

    summary = c.tc.get(f"/v2/sessions/{c.sid}/summary").json()
    assert [(i["problem"], i["assisted"], i["independent"]) for i in summary["items"]] == [
        ("x² + 5x + 6 = 0", True, False), ("x² + 7x + 12 = 0", False, True)]
    assert summary["next_step"]
