"""The backend against the iPad's own fixtures (ios/CONTRACT-QUESTIONS.md says a backend test can load them)."""

import json
from pathlib import Path

import pytest

from tutor.v2 import wire
from v2_support import ShapeError, check_envelope

FIX = Path(__file__).parent / "fixtures" / "v2"
EVENT_FILES = sorted(p for p in (FIX / "events").glob("*.json") if not p.name.startswith("_"))
SCENARIO_FILES = sorted((FIX / "scenarios").glob("*.json"))


def load(p: Path):
    return json.loads(p.read_text())


@pytest.mark.parametrize("path", EVENT_FILES, ids=lambda p: p.name)
def test_ipad_event_fixtures_pass_the_shape_mirror(path):
    check_envelope(load(path))


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=lambda p: p.name)
def test_ipad_scenarios_pass_the_shape_mirror(path):
    steps = load(path)["steps"]
    servers = [s["server"] for s in steps if "server" in s]
    assert servers
    for env in servers:
        check_envelope(env)


@pytest.mark.parametrize("name", ["bad_rect.json", "bad_verdict.json", "missing_seq.json"])
def test_malformed_fixtures_are_rejected_by_the_mirror(name):
    with pytest.raises(ShapeError):
        check_envelope(load(FIX / "malformed" / name))


def test_unknown_event_types_are_tolerated():
    check_envelope(load(FIX / "events" / "_unknown_type.json"))


def test_builders_produce_the_fixture_keys():
    """Every key the iPad fixture for an event carries is a key our builder can produce."""
    ev = load(FIX / "events" / "evidence.json")["payload"]
    built = wire.evidence_payload(
        evidence_id="e", request_id="r", subject="algebra", step_kind="factorization", verdict="invalid",
        reason_code="sign_mismatch", label="verified", verifier_version="v", normalized_step="x", line_index=0,
        highlights=[wire.region([0, 0, 1, 1], 0)], source_ids=["s"], independent=False)
    assert set(ev) <= set(built)
    turn = load(FIX / "events" / "tutor_turn.json")["payload"]
    assert set(turn) <= set(wire.tutor_turn_payload("ask", "c", audio_url="/a.wav", evidence_ids=["e"], assisted=False, highlights=[]))
    rb = load(FIX / "events" / "read_back.json")["payload"]
    assert set(rb) <= set(wire.read_back_payload("r", "question", "t", alternatives=["a"], ambiguous=True, note="n", regions=[]))
    err = load(FIX / "events" / "error.json")["payload"]
    assert set(err) <= set(wire.error_payload("c", "m", request_id="r", latest_board_revision=1))
    st = load(FIX / "events" / "state.json")["payload"]
    assert set(st) <= set(wire.state_payload("ready", wire.problem_info("t", "algebra", True), "t1", []))


def test_unknown_verdict_can_never_be_labelled_verified():
    with pytest.raises(ValueError):
        wire.evidence_payload(evidence_id="e", request_id=None, subject="algebra", step_kind="x", verdict="unknown",
                              reason_code="unsupported", label="verified", verifier_version="v")


def test_envelope_builder_matches_the_documented_shape():
    env = wire.envelope("state", "s1", 3, 2, {"phase": "ready", "skills": []}, turn_id="t1", run_id="run-t1", request_id="r1", replayed=True)
    assert env == {"v": 2, "type": "state", "session_id": "s1", "seq": 3, "board_revision": 2, "payload": {"phase": "ready", "skills": []},
                   "turn_id": "t1", "run_id": "run-t1", "request_id": "r1", "replayed": True}
    assert "replayed" not in wire.envelope("state", "s1", 3, 2, {})


def test_audio_and_traces_are_not_durable():
    assert not wire.is_durable({"type": "audio_chunk"}) and not wire.is_durable({"type": "agent_trace"})
    assert wire.is_durable({"type": "tutor_ink_preview"}) and wire.is_durable({"type": "evidence"})
