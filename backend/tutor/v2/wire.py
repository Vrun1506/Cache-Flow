"""The iPad v2 wire format (Interface.md, ios/CONTRACT-QUESTIONS.md).

Every stream event is one JSON envelope::

    {"v":2,"type","session_id","seq","board_revision","turn_id"?,"run_id"?,"request_id"?,"replayed"?,"payload":{…}}

This module only builds those dicts. The shapes mirror the Swift types in
ios/Packages/CacheFlowKit/Sources/CacheFlowKit/Protocol and are checked against
the iPad's own JSON fixtures in tests/test_v2_contract.py.
"""

from __future__ import annotations

from typing import Any, Optional

PROTOCOL_VERSION = 2
PROTOCOL_HEADER = "X-Socrates-Protocol"

# Phases (LessonPhase). The set is open on the client; these are the ones we use.
IDLE, READING, CONFIRM, READY = "idle", "reading", "confirm", "ready"
TUTORING, STUDENT_TURN, INDEPENDENT_CHECK, COMPLETE = "tutoring", "student_turn", "independent_check", "complete"

# Events that are never replayed after a reconnect: speech is not durable and traces are noise.
NON_DURABLE = frozenset({"audio_chunk", "agent_trace"})

MOVES = frozenset({"ask", "hint", "explain", "write_step", "draw", "show_answer", "clarify", "recap"})
VERDICTS = frozenset({"valid", "invalid", "partial", "unknown"})
REASON_CODES = frozenset({
    "ok", "sign_mismatch", "partial_root", "extraneous_root", "domain_changed",
    "ambiguous_input", "unsupported", "timeout",
})
LABELS = frozenset({"verified", "supported_by_source", "not_verified"})


def envelope(
    type: str,
    session_id: str,
    seq: int,
    board_revision: int,
    payload: dict[str, Any],
    *,
    turn_id: Optional[str] = None,
    run_id: Optional[str] = None,
    request_id: Optional[str] = None,
    replayed: bool = False,
) -> dict[str, Any]:
    env: dict[str, Any] = {
        "v": PROTOCOL_VERSION,
        "type": type,
        "session_id": session_id,
        "seq": seq,
        "board_revision": board_revision,
        "payload": payload,
    }
    if turn_id is not None:
        env["turn_id"] = turn_id
    if run_id is not None:
        env["run_id"] = run_id
    if request_id is not None:
        env["request_id"] = request_id
    if replayed:
        env["replayed"] = True
    return env


def is_durable(env: dict[str, Any]) -> bool:
    return env.get("type") not in NON_DURABLE and env.get("type") != "unknown"


def state_payload(
    phase: str,
    problem: Optional[dict[str, Any]],
    latest_turn_id: Optional[str],
    skills: list[dict[str, Any]],
) -> dict[str, Any]:
    out: dict[str, Any] = {"phase": phase, "skills": skills}
    if problem is not None:
        out["problem"] = problem
    if latest_turn_id is not None:
        out["latest_turn_id"] = latest_turn_id
    return out


def problem_info(text: str, subject: str, confirmed: bool, item: str = "lesson") -> dict[str, Any]:
    return {"text": text, "subject": subject, "confirmed": confirmed, "item": item}


def tutor_turn_payload(
    move: str,
    caption: str,
    *,
    audio_url: Optional[str] = None,
    evidence_ids: Optional[list[str]] = None,
    assisted: bool = False,
    highlights: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    if move not in MOVES:
        raise ValueError(f"unknown tutor move {move!r}")
    out: dict[str, Any] = {
        "move": move,
        "caption": caption,
        "evidence_ids": evidence_ids or [],
        "assisted": assisted,
        "highlights": highlights or [],
    }
    if audio_url:
        out["audio_url"] = audio_url
    return out


def evidence_payload(
    *,
    evidence_id: str,
    request_id: Optional[str],
    subject: str,
    step_kind: str,
    verdict: str,
    reason_code: str,
    label: str,
    verifier_version: str,
    normalized_step: Optional[str] = None,
    line_index: Optional[int] = None,
    highlights: Optional[list[dict[str, Any]]] = None,
    source_ids: Optional[list[str]] = None,
    independent: bool = False,
) -> dict[str, Any]:
    if verdict not in VERDICTS:
        raise ValueError(f"unknown verdict {verdict!r}")
    if label not in LABELS:
        raise ValueError(f"unknown evidence label {label!r}")
    if verdict == "unknown" and label == "verified":
        raise ValueError("an unknown verdict can never be labelled verified")
    out: dict[str, Any] = {
        "evidence_id": evidence_id,
        "subject": subject,
        "step_kind": step_kind,
        "verdict": verdict,
        "reason_code": reason_code,
        "label": label,
        "verifier_version": verifier_version,
        "highlights": highlights or [],
        "source_ids": source_ids or [],
        "independent": independent,
    }
    if request_id is not None:
        out["request_id"] = request_id
    if normalized_step is not None:
        out["normalized_step"] = normalized_step
    if line_index is not None:
        out["line_index"] = line_index
    return out


def read_back_payload(
    request_id: str,
    source: str,
    text: str,
    *,
    alternatives: Optional[list[str]] = None,
    ambiguous: bool = False,
    note: Optional[str] = None,
    regions: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "request_id": request_id,
        "source": source,
        "text": text,
        "alternatives": alternatives or [],
        "ambiguous": ambiguous,
        "regions": regions or [],
    }
    if note:
        out["note"] = note
    return out


def region(bbox: tuple[float, float, float, float] | list[float], line_index: Optional[int] = None) -> dict[str, Any]:
    out: dict[str, Any] = {"bbox": [float(v) for v in bbox]}
    if line_index is not None:
        out["line_index"] = line_index
    return out


def error_payload(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    request_id: Optional[str] = None,
    latest_board_revision: Optional[int] = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"code": code, "message": message, "retryable": retryable}
    if request_id is not None:
        out["request_id"] = request_id
    if latest_board_revision is not None:
        out["latest_board_revision"] = latest_board_revision
    return out


def skill_level(skill: str, label: str, level: int, independent_evidence: int, assisted: bool) -> dict[str, Any]:
    return {"skill": skill, "label": label, "level": level, "independent_evidence": independent_evidence, "assisted": assisted}
