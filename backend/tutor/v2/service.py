"""The v2 tutor: a Socratic loop over the agents (Interface.md, final-plan.md §7, AGENT-SYSTEM.md §16).

``tutor`` solves the problem in the background as a hidden reference, then asks the first
question. The student answers by writing (``check``) or typing; wrong answers walk the hint
ladder, right ones move on and the tutor writes what was established. ``show_answer`` is an
explicit, recorded assist. Every claim about correctness comes from a tool, never a model.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import logging
import re
import time
import uuid
from typing import Any, Optional

from ..agents.animator import run_animator
from ..agents.board_director import fallback_board, run_board_director
from ..agents.guard import REDIRECT, REFUSAL, check_safety, guard_hint, professional_advice_framing
from ..agents.hint import next_hint, run_feedback
from ..agents.judge import run_judge
from ..agents.lesson_designer import batches, check_to_item, run_lesson_designer
from ..agents.orchestrator import fallback_plan, run_orchestrator
from ..agents.qa import run_qa
from ..agents.quiz_designer import run_quiz_designer
from ..agents.summariser import run_summariser
from ..agents.vision import read_lines, read_question
from ..board.strokes import attach_strokes
from ..llm import LLMBackend, LLMError
from ..media.png import MediaError, crop, validate_png, validate_wav
from ..runtime import LocalRuntime, RunContext
from ..runtime.steps import index_board
from ..schemas import Plan
from ..schemas.board import Anchor, BoardCmd, BoardPlan, Payload, SizeHint
from ..schemas.scene import AnimatorDraft, SceneParams
from ..schemas.core import Visual
from ..scenes.validate import build_scene
from ..speech.asr import ASRBackend, ASRError, transcript_candidate
from ..speech.tts import Narrator
from ..state.memory import BoardRef
from ..tools.mathcheck import StepCheck, acheck_student_line, infer_step_kind
from ..tools.worker import MathWorker
from .ink import cmd_to_inks
from .layout import BoardLayout, Placer, Rect
from .presets import PRESETS
from .primitives import scene_to_primitives
from .ink import _ellipse_stroke, ink_is_valid
from .session import MAX_LINE_READS, BoardState, LineRegion, Lesson, Pending, SessionStore, Transfer, V2Session
from .transfer import make_transfer, simple_answer
from .turns import TurnRunner
from .wire import (
    COMPLETE, CONFIRM, IDLE, INDEPENDENT_CHECK, READING, READY, STUDENT_TURN,
    evidence_payload, problem_info, read_back_payload, region, tutor_turn_payload,
)

log = logging.getLogger("tutor.v2")

VERIFIER_VERSION = "mathcheck-1"
MAX_QUESTION_CHARS = 2000
MAX_LINES = 40
LINE_BATCH = 6
STEP_LINES = 3
_QUESTION_START = re.compile(r"^(why|what|how|when|where|who|which|can|could|does|do|is|are|should|would|will|explain|tell)\b", re.I)


class ServiceError(Exception):
    """Mapped to an HTTP status by the app (409 stale, 413 too large, 404 unknown, 422 bad input, 503 unavailable)."""

    def __init__(self, status: int, code: str, message: str = "", **extra: Any) -> None:
        super().__init__(message or code)
        self.status, self.code, self.message, self.extra = status, code, message or code, extra


def choices(text: str, alternatives: list[str]) -> list[str]:
    """The picker list for a read-back: the main reading first, then the others (as in the iPad's mock)."""
    if not alternatives:
        return []
    return [text] + [a for a in alternatives if a != text]


def looks_like_question(text: str) -> bool:
    t = text.strip()
    return "?" in t or bool(_QUESTION_START.match(t))


def subject_for(plan: Plan) -> str:
    return "algebra" if plan.domain == "math" else plan.domain


def end_phase(session: V2Session) -> str:
    """The phase a turn ends in: the student's move, the transfer item, or done."""
    if session.complete:
        return COMPLETE
    if any(p.get("kind") == "board" for p in session.pending_readbacks.values()):
        return CONFIRM  # waiting for the student to accept or correct a reading of their line
    if session.transfer is not None:
        return INDEPENDENT_CHECK
    if session.pending is not None or session.lesson.ready or session.board.lines:
        return STUDENT_TURN  # the tutor is waiting for the student's work or reply
    return READY if session.problem and session.problem.get("confirmed") else IDLE


class TutorService:
    def __init__(
        self,
        backend: LLMBackend,
        worker: MathWorker,
        *,
        narrator: Optional[Narrator] = None,
        asr: Optional[ASRBackend] = None,
        store: Optional[SessionStore] = None,
        timeout_scale: float = 1.0,
        ack_timeout_s: float = 4.0,
        screen: tuple[float, float] = (1210.0, 834.0),
        health_probe=None,
    ) -> None:
        self.backend = backend
        self.worker = worker
        self.narrator = narrator or Narrator(None)
        self.asr = asr
        self.store = store or SessionStore()
        self.scale = timeout_scale
        self.turns = TurnRunner(ack_timeout_s)
        self.screen = screen
        self.health_probe = health_probe

    # ================================================================== HTTP

    def get(self, session_id: str) -> V2Session:
        s = self.store.get(session_id)
        if s is None:
            raise ServiceError(404, "unknown_session", "That session no longer exists.")
        return s

    async def create_session(self, preset_id: Optional[str], locale: str, board: Optional[dict] = None) -> V2Session:
        s = V2Session(locale=locale or "en", preset_id=preset_id)
        w, h = self.screen
        bw, bh = (board or {}).get("w"), (board or {}).get("h")
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (bw, bh)) \
                and 300 <= bw <= 5000 and 200 <= bh <= 5000:
            w, h = float(bw), float(bh) + 196.0  # board area + header + caption bar
        # anything else is ignored: a bad size must never break session creation or push ink off the board
        s.placer = Placer(BoardLayout.for_screen(w, h))
        preset = PRESETS.get(preset_id or "")
        if preset:
            self._set_problem(s, preset.display, preset.subject, preset.plan)
            s.phase = READY
        evicted = self.store.add(s)
        if evicted is not None:
            self._dispose(evicted)
        return s

    @staticmethod
    def _dispose(s: V2Session) -> None:
        """Stop everything a removed session still has running and close its sockets."""
        for task in (s.runner, s.setup_task, *s.background):
            if task is not None and not task.done():
                task.cancel()
        for fut in s.acks.values():
            if not fut.done():
                fut.cancel()
        s.close_subscribers()

    def _admit(self, s: V2Session, rev: int, request_id: str) -> Optional[dict[str, Any]]:
        """Idempotency and board-revision conflicts (CONTRACT-QUESTIONS 5)."""
        if not request_id or len(request_id) > 80:
            raise ServiceError(422, "bad_request_id", "request_id is required.")
        done = s.requests.get(request_id)
        if done is not None:
            return done
        if rev < s.board_revision:
            raise ServiceError(409, "stale_board_revision", "The board has changed.", latest_board_revision=s.board_revision)
        s.board_revision = rev
        return None

    @staticmethod
    def _accepted(s: V2Session, request_id: str) -> dict[str, Any]:
        return s.remember(request_id, {"request_id": request_id})

    # ---------------------------------------------------------------- question

    async def submit_question(self, s: V2Session, rev: int, request_id: str, *, text: Optional[str] = None,
                              png: Optional[bytes] = None) -> dict[str, Any]:
        if (done := self._admit(s, rev, request_id)) is not None:
            return done
        if text is not None:
            text = " ".join(text.split())
            if not text:
                raise ServiceError(422, "empty_question", "Type or write a question first.")
            if len(text) > MAX_QUESTION_CHARS:
                raise ServiceError(413, "question_too_long", "That question is too long.")
            self._start_setup(s, self._problem_from_text(s, request_id, text))
        elif png is not None:
            try:
                validate_png(png)
            except MediaError as exc:
                raise ServiceError(exc.http_status, exc.code, str(exc)) from None
            self._start_setup(s, self._read_question_image(s, request_id, png))
        else:
            raise ServiceError(422, "empty_question", "Send text or an image.")
        return self._accepted(s, request_id)

    def _start_setup(self, s: V2Session, coro) -> None:
        if s.setup_task and not s.setup_task.done():
            s.setup_task.cancel()
        s.setup_task = asyncio.create_task(self._guarded(s, coro))

    async def _guarded(self, s: V2Session, coro) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("setup failed")
            s.emit_error("setup_failed", "I couldn't set that up. Please try again.", retryable=True)
            s.emit_state(IDLE if not s.problem else READY)

    def _reset_lesson(self, s: V2Session) -> None:
        s.lesson = Lesson()
        s.pending = None
        s.transfer = None
        s.assisted = False
        s.complete = False
        s.pending_readbacks.clear()  # a reading of the old board must not be confirmed into the new problem
        s.board = BoardState()
        if s.placer is not None:
            s.placer = Placer(s.placer.layout)

    def _set_problem(self, s: V2Session, text: str, subject: str, plan: Plan) -> None:
        self._reset_lesson(s)
        s.plan = plan
        s.level = plan.level
        s.problem = problem_info(text, subject, True)
        s.lesson.memory.question = plan.question_clean
        s.lesson.memory.level = plan.level

    async def _problem_from_text(self, s: V2Session, request_id: str, text: str) -> None:
        s.emit_state(READING, request=request_id)
        safety = check_safety(text)
        if safety != "ok":
            s.emit_error("refused" if safety == "refuse" else "redirected",
                         REFUSAL if safety == "refuse" else REDIRECT, request=request_id)
            s.emit_state(READY if s.problem else IDLE, request=request_id)
            return
        try:
            plan = await asyncio.wait_for(run_orchestrator(self.backend, text, level_hint=s.level), 20 * self.scale)
        except (LLMError, asyncio.TimeoutError):
            plan = fallback_plan(text)
        if plan.safety != "ok":
            s.emit_error("refused" if plan.safety == "refuse" else "redirected",
                         REFUSAL if plan.safety == "refuse" else REDIRECT, request=request_id)
            s.emit_state(READY if s.problem else IDLE, request=request_id)
            return
        if s.running_turn:  # the old problem's turn must not keep talking over the new one
            await self.turns.cancel(s, s.running_turn, "superseded", emit_state=False)
        self._set_problem(s, text, subject_for(plan), plan)
        s.emit_state(READY, request=request_id)

    async def _read_question_image(self, s: V2Session, request_id: str, png: bytes) -> None:
        s.emit_state(READING, request=request_id)
        reading = await read_question(self.backend, png)
        if not reading.text.strip():
            s.emit_error("unreadable", "I couldn't read that. Try again or type the question.", retryable=True, request=request_id)
            s.emit_state(READY if s.problem else IDLE, request=request_id)
            return
        box = s.placer.layout.question_box if s.placer else None
        s.add_pending(request_id, {"kind": "question", "text": reading.text})
        s.emit("read_back", read_back_payload(
            request_id, "question", reading.text, alternatives=choices(reading.text, reading.alternatives),
            ambiguous=reading.ambiguous, note=reading.note,
            regions=[region(box.to_list())] if box else []), request=request_id)
        s.emit_state(CONFIRM, request=request_id)

    # ------------------------------------------------------------------- board

    async def submit_board(self, s: V2Session, meta: dict[str, Any], png: bytes) -> dict[str, Any]:
        bad = ServiceError(422, "bad_board_meta", "The board upload was malformed.")
        raw_lines = meta.get("lines", [])
        if not isinstance(raw_lines, list):
            raise bad
        if len(raw_lines) > MAX_LINES:
            raise ServiceError(413, "too_many_lines", "Too many lines on the board.")
        try:
            rev, request_id = int(meta["board_revision"]), str(meta["request_id"])
            image_region = tuple(float(v) for v in meta["image_region"])
            lines = [LineRegion(int(i if (i := ln.get("line_index")) is not None else n),
                                tuple(float(v) for v in ln["bbox"]))  # type: ignore[arg-type]
                     for n, ln in enumerate(raw_lines)]
        except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
            raise bad from None
        if rev < 0 or not _rect_ok(image_region, positive=True) or not all(_rect_ok(ln.bbox) for ln in lines):
            raise bad
        try:
            validate_png(png)
        except MediaError as exc:
            raise ServiceError(exc.http_status, exc.code, str(exc)) from None
        if (done := self._admit(s, rev, request_id)) is not None:
            return done
        s.board.revision, s.board.png, s.board.image_region, s.board.lines = rev, png, image_region, lines
        if s.placer is not None:
            s.placer.student_regions = [Rect.from_list(list(ln.bbox)) for ln in lines]
            for ln in lines:
                s.placer.register(f"line-{ln.line_index}", Rect.from_list(list(ln.bbox)))
        return self._accepted(s, request_id)

    # ------------------------------------------------------------------- audio

    async def submit_audio(self, s: V2Session, rev: int, request_id: str, wav: bytes) -> dict[str, Any]:
        if (running := s.inflight.get(request_id)) is not None:  # a retry while the first is still transcribing
            return await asyncio.shield(running)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        s.inflight[request_id] = fut
        try:
            result = await self._submit_audio(s, rev, request_id, wav)
            fut.set_result(result)
            return result
        except BaseException as exc:
            fut.set_exception(exc)
            fut.exception()  # mark retrieved: nobody may be waiting
            raise
        finally:
            s.inflight.pop(request_id, None)

    async def _submit_audio(self, s: V2Session, rev: int, request_id: str, wav: bytes) -> dict[str, Any]:
        if (done := self._admit(s, rev, request_id)) is not None:
            return done
        if self.asr is None:
            raise ServiceError(503, "asr_unavailable", "Speech input isn't available right now.")
        try:
            validate_wav(wav)
        except MediaError as exc:
            raise ServiceError(exc.http_status, exc.code, str(exc)) from None
        try:
            cand = await transcript_candidate(self.asr, wav)
        except ASRError:
            raise ServiceError(503, "asr_unavailable", "Speech input isn't available right now.") from None
        s.add_pending(request_id, {"kind": "transcript", "text": cand["text"]})
        s.emit("read_back", read_back_payload(
            request_id, "transcript", cand["text"], alternatives=choices(cand["text"], cand.get("alternatives") or []),
            ambiguous=bool(cand["ambiguous"]), note=cand.get("note")), request=request_id)
        s.remember(request_id, {"request_id": request_id, "transcript": cand["text"], "ambiguous": bool(cand["ambiguous"])})
        return s.requests[request_id]

    # ------------------------------------------------------------------ intent

    async def intent(self, s: V2Session, body: dict[str, Any]) -> dict[str, Any]:
        action, rev, rid = body.get("action"), body.get("board_revision"), body.get("request_id")
        if not isinstance(action, str) or not isinstance(rev, int) or isinstance(rev, bool) or not isinstance(rid, str):
            raise ServiceError(422, "bad_intent", "action, board_revision and request_id are required.")
        if (done := self._admit(s, rev, rid)) is not None:
            return done
        # Remember it NOW: starting a turn awaits the cancellation of the running one, and a retry that
        # arrives in that window must get this answer instead of starting a second turn.
        accepted = self._accepted(s, rid)
        text = body.get("text") if isinstance(body.get("text"), str) else None
        if action == "cancel":
            turn = body.get("turn_id") or s.running_turn
            if turn:
                await self.turns.cancel(s, turn, "student_cancelled", end_phase=end_phase)
            return accepted
        handlers = {
            "tutor": self._t_tutor, "check": self._t_check, "hint": self._t_hint,
            "explain_step": self._t_explain, "draw": self._t_draw, "show_answer": self._t_show_answer,
        }
        if action in ("ask", "quiz_answer"):
            if not text or not text.strip():
                s.emit_error("empty_text", "Type something first.", request=rid)
                return accepted
            build = (lambda t, tx=text: self._t_ask(s, t, rid, rev, tx)) if action == "ask" else \
                    (lambda t, tx=text: self._t_quiz_answer(s, t, rid, rev, tx))
        elif action in handlers:
            fn = handlers[action]
            build = lambda t, fn=fn: fn(s, t, rid, rev)  # noqa: E731
        else:
            s.emit_error("unsupported_action", "That action isn't available yet.", request=rid)
            return accepted
        if not (s.problem and s.problem.get("confirmed")):
            s.emit_error("no_problem", "Write or pick a problem first.", request=rid)
            return accepted
        await self.turns.start(s, rid, rev, build, end_phase=end_phase)
        return accepted

    # ----------------------------------------------------------- client messages

    async def client_message(self, s: V2Session, msg: dict[str, Any]) -> None:
        """Messages from the iPad over the socket. Unknown or malformed ones are ignored."""
        if msg.get("v") != 2 or msg.get("session_id") != s.session_id:
            return
        rev = msg.get("board_revision")
        if isinstance(rev, int) and not isinstance(rev, bool):
            s.board_revision = max(s.board_revision, rev)  # the ack tells us the revision the iPad is on
        p = msg.get("payload") if isinstance(msg.get("payload"), dict) else {}
        kind = msg.get("type")
        if kind == "tutor_ink_ack" and isinstance(p.get("ink_id"), str):
            self.turns.resolve_ack(s, f"ink:{p['ink_id']}", bool(p.get("placed")), p.get("reason"))
        elif kind == "scene_ack" and isinstance(p.get("scene_id"), str):
            self.turns.resolve_ack(s, f"scene:{p['scene_id']}", bool(p.get("placed")), p.get("reason"))
        elif kind == "cancel_turn" and isinstance(p.get("turn_id"), str):
            await self.turns.cancel(s, p["turn_id"], "student_cancelled", end_phase=end_phase)
        elif kind == "read_back_confirm" and isinstance(p.get("request_id"), str):
            await self._read_back_confirm(s, p["request_id"], bool(p.get("accepted")),
                                          p["text"] if isinstance(p.get("text"), str) else None)
        # audio_done needs no action: the server never waits on playback (final-plan.md §16.6).

    async def _read_back_confirm(self, s: V2Session, rid: str, accepted: bool, text: Optional[str]) -> None:
        pending = s.pending_readbacks.pop(rid, None)
        if pending is None:
            return
        kind, candidate = pending["kind"], pending["text"]
        chosen = (text or "").strip() or (candidate if accepted else "")
        if kind == "question":
            if not chosen:
                s.problem = None
                s.emit_state(IDLE, request=rid)
                return
            self._start_setup(s, self._problem_from_text(s, rid, chosen))
        elif kind == "board":
            if not chosen:
                s.emit_state(end_phase(s), request=rid)
                return
            s.board.confirmed[pending["line"]] = chosen
            s.board.confirmed_key[pending["line"]] = pending.get("key")
            rev = s.board_revision
            await self.turns.start(s, rid, rev, lambda t: self._evaluate_line(s, t, rid, rev, pending["line"], chosen),
                                   end_phase=end_phase)
        elif kind == "transcript":
            if chosen:
                rev = s.board_revision
                await self.turns.start(s, rid, rev, lambda t: self._t_ask(s, t, rid, rev, chosen), end_phase=end_phase)

    # =============================================================== speaking

    async def _speak(self, s: V2Session, turn: str, text: str, tag: str) -> Optional[str]:
        """One spoken clip for ``text``; None when there is no TTS (the iPad voices the caption)."""
        try:
            clips = await self.narrator.narrate("", text, prefix=f"{turn}-{tag}")
        except Exception:
            return None
        for clip in clips:
            if clip.wav is not None:
                return s.store_audio(f"{turn}-{tag}", clip.wav)
        return None

    async def _say(self, s: V2Session, turn: str, rev: int, rid: str, move: str, caption: str, *, tag: str = "a",
                   evidence_ids: Optional[list[str]] = None, assisted: bool = False,
                   highlights: Optional[list[dict]] = None, audio: bool = True) -> None:
        url = await self._speak(s, turn, caption, tag) if audio else None
        s.emit("tutor_turn", tutor_turn_payload(move, caption, audio_url=url, evidence_ids=evidence_ids,
                                                assisted=assisted, highlights=highlights), turn=turn, request=rid, rev=rev)

    def _trace(self, s: V2Session, turn: str, rev: int, component: str, action: str, outcome: str, ms: int,
               evidence_id: Optional[str] = None) -> None:
        p: dict[str, Any] = {"component": component, "action": action, "outcome": outcome, "latency_ms": ms}
        if evidence_id:
            p["evidence_id"] = evidence_id
        s.emit("agent_trace", p, turn=turn, rev=rev)

    # ================================================================ lesson

    async def _ensure_lesson(self, s: V2Session, turn: str, rev: int) -> bool:
        """Build the hidden reference (orchestrate, solve, verify) and the first lessons; traced to the iPad."""
        L = s.lesson
        if not L.ready:
            assert s.plan is not None
            runtime = LocalRuntime(self.backend, self.worker, timeout_scale=self.scale)

            async def sink(event: dict[str, Any]) -> None:
                if event["type"] == "agent" and event.get("state") in ("done", "failed"):
                    self._trace(s, turn, rev, str(event["id"]), "solve" if event["id"] != "verify" else "verify",
                                "ok" if event["state"] == "done" else "failed", int(event.get("ms", 0)))

            result = await runtime.run_solve_spine(s.plan.question_clean, RunContext(run_id=turn, sink=sink), plan=s.plan)
            if result.status != "ok" or result.solution is None or result.report is None:
                return False
            status = {v.id: v.status for v in result.report.steps}
            status["final"] = result.report.final_answer.status if result.report.final_answer else "unverified"
            L.plan, L.solution, L.report = result.plan, result.solution, result.report
            L.memory.set_solution(result.plan, result.solution, status)
            L.memory.session_id, L.memory.page_id = s.session_id, "p1"
        return await self._ensure_step_lesson(s, turn, rev, L.step)

    async def _ensure_step_lesson(self, s: V2Session, turn: str, rev: int, step: int) -> bool:
        L = s.lesson
        if L.solution is None or not (1 <= step <= L.total):
            return False
        if f"s{step}" in L.lessons:
            return True
        group = next((ids for ids in batches(L.solution) if f"s{step}" in ids), [f"s{step}"])
        missing = [i for i in group if i not in L.lessons]
        started = time.monotonic()
        for lesson in await run_lesson_designer(self.backend, L.memory, missing):
            L.lessons[lesson.step_id] = lesson
            n = int(lesson.step_id[1:])
            L.checks[n] = check_to_item(lesson, L.solution.steps[n - 1])
            s.skills.seed(L.checks[n].skill)
        self._trace(s, turn, rev, "lesson_designer", "design_lesson", "ok", int((time.monotonic() - started) * 1000))
        return f"s{step}" in L.lessons

    def _final_value(self, s: V2Session) -> Optional[str]:
        sol = s.lesson.solution
        return (sol.final_answer.value or sol.final_answer.text) if sol else None

    def _step_exprs(self, s: V2Session) -> list[str]:
        sol = s.lesson.solution
        return [st.expr for st in sol.steps if st.expr] if sol else []

    def _allowed(self, s: V2Session) -> list[str]:
        return [x for x in (s.plan.question_clean if s.plan else None, s.plan.statement_expr if s.plan else None) if x]

    async def _guarded_hint(self, s: V2Session, item, level: int) -> str:
        hint = next_hint(item, level)

        async def regenerate(_forbidden: list[str]):
            raise LLMError("no regeneration: the guard falls back to a template")

        hint = await guard_hint(self.backend, hint, level, self._final_value(s), self._step_exprs(s), regenerate,
                                allowed=self._allowed(s))
        return hint.say

    # ================================================================= turns

    async def _finished(self, s: V2Session, turn: str, rid: str, rev: int) -> None:
        await self._say(s, turn, rev, rid, "recap",
                        "We've finished this one. Ask me about it, or write a new problem to keep going.", tag="done")

    async def _no_hints(self, s: V2Session, turn: str, rid: str, rev: int) -> None:
        # Nothing is marked as assisted: the independent item stays independent unless the student asks to see the answer.
        await self._say(s, turn, rev, rid, "clarify",
                        "This one is on your own, so no hints. If you're stuck you can ask to see the answer.", tag="nohint")

    def _transfer_ask(self, s: V2Session) -> str:
        t = s.transfer
        return (f"Now one on your own, with no hints: solve {t.display}. Type or write the answer."
                if t.expr else f"Now one on your own, with no hints: {t.display}")

    async def _t_tutor(self, s: V2Session, turn: str, rid: str, rev: int) -> None:
        if s.complete:
            return await self._finished(s, turn, rid, rev)
        if s.transfer is not None:
            return await self._say(s, turn, rev, rid, "ask", self._transfer_ask(s), tag="transfer")
        if not await self._ensure_lesson(s, turn, rev):
            await self._say(s, turn, rev, rid, "clarify",
                            "I couldn't work that problem out just now. Try again, or rephrase it.", tag="fail")
            return
        L = s.lesson
        step = L.step
        check, lesson = L.checks[step], L.lessons[f"s{step}"]
        started = step > 1 or s.pending is not None
        lead = "Let's pick up where we left off." if started else f"Let's work on {s.problem['text']} together."
        s.pending = Pending(step)
        await self._say(s, turn, rev, rid, "ask", f"{lead} {lesson.intent} {check.prompt}")

    async def _t_hint(self, s: V2Session, turn: str, rid: str, rev: int) -> None:
        if s.complete:
            return await self._finished(s, turn, rid, rev)
        if s.transfer is not None:
            return await self._no_hints(s, turn, rid, rev)
        if not await self._ensure_lesson(s, turn, rev):
            return await self._say(s, turn, rev, rid, "clarify", "I need a moment more on this problem first.", tag="fail")
        L = s.lesson
        step = L.step
        n = min(L.hints_shown.get(step, 0) + 1, 3)
        L.hints_shown[step] = n
        check, lesson = L.checks[step], L.lessons[f"s{step}"]
        s.pending = Pending(step)
        if n < 3:
            await self._say(s, turn, rev, rid, "hint", await self._guarded_hint(s, check, n))
            return
        L.assisted_steps.add(step)
        s.assisted = True
        s.skills.mark_assisted(check.skill)
        await self._say(s, turn, rev, rid, "hint", f"{check.hints[2]}", assisted=True)
        await self._write_step(s, turn, rev, step)

    async def _t_explain(self, s: V2Session, turn: str, rid: str, rev: int) -> None:
        if s.complete:
            return await self._finished(s, turn, rid, rev)
        if s.transfer is not None:
            return await self._no_hints(s, turn, rid, rev)
        if not await self._ensure_lesson(s, turn, rev):
            return await self._say(s, turn, rev, rid, "clarify", "I need a moment more on this problem first.", tag="fail")
        L = s.lesson
        step = L.step
        lesson = L.lessons[f"s{step}"]
        L.assisted_steps.add(step)
        s.assisted = True
        s.skills.mark_assisted(L.checks[step].skill)
        s.emit("tutor_turn", tutor_turn_payload("explain", f"{lesson.intent} {lesson.explain}", assisted=True),
               turn=turn, request=rid, rev=rev)
        for i, text in enumerate((lesson.intent, lesson.explain)):
            if turn in s.cancelled_turns:
                return
            url = await self._speak(s, turn, text, f"c{i}")
            if url:
                s.emit("audio_chunk", {"chunk_index": i, "audio_url": url, "caption_span": text, "final": i == 1},
                       turn=turn, rev=rev)
        await self._write_step(s, turn, rev, step)

    async def _t_show_answer(self, s: V2Session, turn: str, rid: str, rev: int) -> None:
        if s.complete:
            return await self._finished(s, turn, rid, rev)
        s.assisted = True
        if s.transfer is not None:
            await self._show_transfer_answer(s, turn, rid, rev)
            return
        if not await self._ensure_lesson(s, turn, rev):
            return await self._say(s, turn, rev, rid, "clarify", "I couldn't work that problem out just now.", tag="fail")
        L = s.lesson
        sol = L.solution
        for st in sol.steps:
            s.skills.mark_assisted(st.title)
        caption = f"Here's the answer: {sol.final_answer.text} I've written the steps on the board, and marked this one as helped."
        await self._say(s, turn, rev, rid, "show_answer", caption, assisted=True)
        await self._write_lines(s, turn, rev, self._answer_lines(s), "answer")
        s.emit("skills", {"skills": s.skills.wire()}, turn=turn, rev=rev)
        L.done = True
        await self._finish_lesson(s, turn, rev, rid, assisted=True)

    def _answer_lines(self, s: V2Session) -> list[str]:
        out: list[str] = []
        for st in s.lesson.solution.steps:
            line = st.expr or (st.board_text[0] if st.board_text else st.title)
            out.append(line[:60])
        fa = s.lesson.solution.final_answer
        out.append((fa.value or fa.text)[:60])
        return out[:8]

    async def _t_draw(self, s: V2Session, turn: str, rid: str, rev: int) -> None:
        spec = await self._scene_spec(s, turn, rev)
        subject = (s.problem or {}).get("subject", "")
        caption = ("Here's a picture to help. It's an illustration, not a checked answer."
                   if subject not in ("algebra",) else "Here's the graph, so you can see where the equation is zero.")
        await self._say(s, turn, rev, rid, "draw", caption)
        cmd = BoardCmd(id=f"scene-region-{s.scene_no + 1}", op="plot", anchor=Anchor(rel="free"),
                       size_hint=SizeHint(w=440, h=280))
        rect = s.placer.place(cmd, (440.0, 280.0)) if s.placer else None
        if rect is None:
            return s.emit_error("board_full", "There's no room on the board. Clear some space first.", request=rid)
        scene = scene_to_primitives(spec, rect)
        scene["scene_id"] = s.next_scene_id()
        await self.turns.place_scene(s, turn, rev, scene)

    async def _scene_spec(self, s: V2Session, turn: str, rev: int):
        plan = s.plan
        started = time.monotonic()
        spec = None
        if plan and plan.statement_expr and "x" in plan.statement_expr and plan.solution_kind in ("compute", "derive"):
            spec = self._function_scene(plan.statement_expr)
        if spec is None:
            L = s.lesson
            visual = plan.visuals[0] if plan and plan.visuals else Visual(
                concept=(L.solution.steps[L.step - 1].title if L.solution else (plan.question_clean if plan else "the idea"))[:60],
                kind_hint="diagram")
            facts = [f for st in (L.solution.steps if L.solution else []) for f in st.facts][:6]
            spec = await run_animator(self.backend, visual, facts, s.level, scene_id=f"scene-{s.scene_no + 1}")
        self._trace(s, turn, rev, "animator", "build_scene", "ok", int((time.monotonic() - started) * 1000))
        return spec

    @staticmethod
    def _function_scene(statement: str):
        """A plot of lhs − rhs, computed by code from the problem itself (no model, so it is always right)."""
        left, _, right = statement.partition("=")
        expr = left if not right.strip() or right.strip() == "0" else f"({left})-({right})"
        draft = AnimatorDraft(template="fn_plot", title="Graph", params=SceneParams(expr=expr.replace("**", "^"),
                              x_min=-6.0, x_max=6.0), captions=["Where the curve crosses zero, the equation is solved."])
        spec = build_scene(draft, "plot", fallback_bullets=[])
        return spec

    async def _t_ask(self, s: V2Session, turn: str, rid: str, rev: int, text: str) -> None:
        if s.transfer is not None and not looks_like_question(text):
            return await self._t_quiz_answer(s, turn, rid, rev, text)
        if s.pending is not None and s.lesson.ready and not looks_like_question(text):
            return await self._answer_check(s, turn, rid, rev, text)
        safety = check_safety(text)
        if safety != "ok":
            return await self._say(s, turn, rev=rev, rid=rid, move="clarify", caption=REFUSAL if safety == "refuse" else REDIRECT, tag="safe")
        mem = s.lesson.memory
        mem.selected_object = None
        started = time.monotonic()
        reply = await run_qa(self.backend, mem, text, selected_step=None)
        self._trace(s, turn, rev, "qa", "answer", "ok", int((time.monotonic() - started) * 1000))
        answer = reply.answer_spoken
        if framing := professional_advice_framing(text):
            answer = f"{answer} {framing}"
        mem.add_turn("student", text)
        mem.add_turn("tutor", answer)
        self._summarise_later(s)
        await self._say(s, turn, rev, rid, "explain", answer)
        if reply.board_text:
            await self._write_lines(s, turn, rev, list(reply.board_text), "qa")
        if reply.scene_request is not None and turn not in s.cancelled_turns:
            facts = [f for st in (s.lesson.solution.steps if s.lesson.solution else []) for f in st.facts][:6]
            spec = await run_animator(self.backend, reply.scene_request, facts, s.level, scene_id=f"scene-{s.scene_no + 1}")
            cmd = BoardCmd(id=f"scene-region-{s.scene_no + 1}", op="plot", anchor=Anchor(rel="free"), size_hint=SizeHint(w=440, h=280))
            rect = s.placer.place(cmd, (440.0, 280.0)) if s.placer else None
            if rect is not None:
                scene = scene_to_primitives(spec, rect)
                scene["scene_id"] = s.next_scene_id()
                await self.turns.place_scene(s, turn, rev, scene)

    def _summarise_later(self, s: V2Session) -> None:
        """Roll the conversation into ``turns_summary`` off the critical path (AGENT-SYSTEM.md §16.8)."""
        mem = s.lesson.memory
        if not mem.needs_summary() or s.summarising:
            return
        s.summarising = True

        async def work() -> None:
            try:
                mem.apply_summary(await run_summariser(self.backend, mem))
            finally:
                s.summarising = False

        task = asyncio.create_task(work())
        s.background.add(task)
        task.add_done_callback(s.background.discard)

    async def _t_quiz_answer(self, s: V2Session, turn: str, rid: str, rev: int, text: str,
                             line: Optional[int] = None) -> None:
        if s.transfer is None:
            return await self._answer_check(s, turn, rid, rev, text) if s.pending else await self._t_ask(s, turn, rid, rev, text)
        T = s.transfer
        T.attempts += 1
        if T.expr:
            check = await acheck_student_line(self.worker, T.expr, text)
            ev = self._evidence(s, rid, check, line=line, independent=not s.assisted)
            verdict = check.verdict
            s.emit("evidence", ev, turn=turn, request=rid, rev=rev)
            self._trace(s, turn, rev, "math", "check_math_step", verdict, 0, ev["evidence_id"])
            caption = {
                "valid": "Correct, and you did that one without any help." if not s.assisted else "Correct. This one was helped, so it won't count as independent.",
                "partial": "That's one root. What's the other?",
                "invalid": "Not quite. Substitute your answer back in and check the signs.",
            }.get(verdict, "I couldn't check that. Write the answer as x = … or x = …")
            ids = [ev["evidence_id"]]
            # "Solve" is answered by the solutions: a correct factorisation is a good step, not the answer.
            correct = verdict == "valid" and check.step_kind == "solution_set"
            if verdict == "valid" and not correct:
                caption = "That's a correct step, but I need the solutions themselves. Which values of x work?"
            skill_name = "Solving equations"
        else:
            item = T.item
            verdict_obj = await run_judge(self.backend, item, text, self.worker)
            correct = verdict_obj.correct
            caption = verdict_obj.feedback or ("Correct." if correct else "Not quite. Think about it again.")
            ids = []
            skill_name = item.skill
        if correct:
            independent = not s.assisted
            s.skills.record(skill_name, correct=True, independent=independent)
            s.summary_items.append({"problem": T.display, "outcome": "valid", "assisted": s.assisted,
                                    "independent": independent, "evidence_ids": ids})
            s.transfer = None
            s.pending = None
            s.complete = True
            s.phase = COMPLETE
            s.emit("skills", {"skills": s.skills.wire()}, turn=turn, rev=rev)
            await self._say(s, turn, rev, rid, "recap", caption, evidence_ids=ids)
        else:
            await self._say(s, turn, rev, rid, "ask", caption, evidence_ids=ids)

    async def _show_transfer_answer(self, s: V2Session, turn: str, rid: str, rev: int) -> None:
        T = s.transfer
        answer = simple_answer(T.expr) if T.expr else (T.item.expected.value or "") if T.item else ""
        caption = f"Here's the answer: {answer}. I've marked this one as helped." if answer else "I've marked this one as helped."
        await self._say(s, turn, rev, rid, "show_answer", caption, assisted=True)
        if answer:
            await self._write_lines(s, turn, rev, [answer], "transfer")
        s.summary_items.append({"problem": T.display, "outcome": "valid", "assisted": True, "independent": False, "evidence_ids": []})
        s.skills.mark_assisted("Solving equations")
        s.emit("skills", {"skills": s.skills.wire()}, turn=turn, rev=rev)
        s.transfer = None
        s.pending = None
        s.complete = True
        s.phase = COMPLETE

    # ============================================================ answers

    async def _answer_check(self, s: V2Session, turn: str, rid: str, rev: int, text: str) -> None:
        """The student answered the pending check in words (typed)."""
        if not await self._ensure_lesson(s, turn, rev):
            return await self._say(s, turn, rev, rid, "clarify", "I couldn't work that problem out just now.", tag="fail")
        L = s.lesson
        step = L.step
        item = L.checks.get(step)
        if item is None:
            return await self._say(s, turn, rev, rid, "clarify", "Let's start with Tutor me.", tag="x")
        verdict = await run_judge(self.backend, item, text, self.worker)
        if verdict.correct:
            independent = L.misses.get(step, 0) == 0 and step not in L.assisted_steps
            s.skills.record(item.skill, correct=True, independent=independent)
            await self._advance(s, turn, rid, rev, step, lead=verdict.feedback or "That's right.", written=step)
            return
        L.misses[step] = L.misses.get(step, 0) + 1
        s.skills.record(item.skill, correct=False, independent=False)
        await self._wrong(s, turn, rid, rev, step, item, verdict.feedback)

    async def _wrong(self, s: V2Session, turn: str, rid: str, rev: int, step: int, item, feedback: str,
                     *, reason: str = "", ids: Optional[list[str]] = None) -> None:
        """The hint ladder: miss 1 a question, miss 2 a narrower hint, miss 3 the step is written and we move on."""
        L = s.lesson
        misses = L.misses[step]
        if misses < 3:
            hint = reason or await self._guarded_hint(s, item, misses)
            lead = feedback or "Not quite."
            await self._say(s, turn, rev, rid, "hint", f"{lead} {hint}".strip(), evidence_ids=ids)
            s.pending = Pending(step)
            return
        L.assisted_steps.add(step)
        s.assisted = True
        s.skills.mark_assisted(item.skill)
        await self._advance(s, turn, rid, rev, step, lead=f"Let's look at it together. {item.hints[2]}", written=step,
                            assisted=True, move="explain", ids=ids)

    async def _advance(self, s: V2Session, turn: str, rid: str, rev: int, done_step: int, *, lead: str, written: Optional[int],
                       assisted: bool = False, move: str = "ask", ids: Optional[list[str]] = None) -> None:
        L = s.lesson
        L.misses[done_step] = 0
        if done_step >= L.total:
            L.done = True
            await self._say(s, turn, rev, rid, "recap" if not assisted else move, lead, assisted=assisted, evidence_ids=ids)
            if written:
                await self._write_step(s, turn, rev, written)
            await self._finish_lesson(s, turn, rev, rid, assisted=s.assisted)
            return
        L.step = done_step + 1
        await self._ensure_step_lesson(s, turn, rev, L.step)
        lesson, check = L.lessons[f"s{L.step}"], L.checks[L.step]
        s.pending = Pending(L.step)
        await self._say(s, turn, rev, rid, move, f"{lead} {lesson.intent} {check.prompt}", assisted=assisted, evidence_ids=ids)
        if written:
            await self._write_step(s, turn, rev, written)

    async def _finish_lesson(self, s: V2Session, turn: str, rev: int, rid: str, *, assisted: bool) -> None:
        """Record the item, then offer a fresh problem with no hints (Interface.md first fixture)."""
        s.summary_items.append({"problem": s.problem["text"], "outcome": "valid", "assisted": assisted,
                                "independent": not assisted, "evidence_ids": []})
        s.emit("skills", {"skills": s.skills.wire()}, turn=turn, rev=rev)
        s.pending = None
        preset = PRESETS.get(s.preset_id or "")
        made = make_transfer(s.plan.statement_expr if s.plan else None, preset)
        if made is None:
            quiz = await run_quiz_designer(self.backend, s.plan, s.lesson.solution, s.level)
            item = quiz.items[-1]
            s.transfer = Transfer(item.prompt, None, item)
        else:
            s.transfer = Transfer(made[0], made[1])
        s.assisted = False
        s.problem = problem_info(s.transfer.display, (s.problem or {}).get("subject", "algebra"), True, "independent_check")
        s.phase = INDEPENDENT_CHECK
        s.emit_state(INDEPENDENT_CHECK, turn=turn, rev=rev)
        await self._say(s, turn, rev, rid, "ask", self._transfer_ask(s), tag="transfer")

    # ============================================================== check

    async def _t_check(self, s: V2Session, turn: str, rid: str, rev: int) -> None:
        if s.transfer is not None and s.transfer.expr is None:
            return await self._say(s, turn, rev, rid, "ask", "Type your answer and send it.", tag="x")
        B = s.board
        if not B.lines or B.png is None:
            return await self._say(s, turn, rev, rid, "clarify", "I don't see any working yet. Write a line, then tap Check.", tag="none")
        if s.complete:
            return await self._finished(s, turn, rid, rev)
        if B.revision < rev:  # the iPad has not uploaded the board it is asking about: do not grade an older one
            return await self._say(s, turn, rev, rid, "clarify",
                                   "I don't have your latest writing yet. Please tap Check again.", tag="stale")
        last = max(B.lines, key=lambda ln: ln.line_index)
        key = self._crop_key(s, last)
        if last.line_index in B.confirmed and key is not None and B.confirmed_key.get(last.line_index) == key:
            return await self._evaluate_line(s, turn, rid, rev, last.line_index, B.confirmed[last.line_index])
        text, ambiguous, alternatives, note = await self._read_line(s, last)
        if not text.strip():
            return await self._say(s, turn, rev, rid, "clarify", "I couldn't read that line. Could you write it again a little larger?", tag="illegible")
        reg = [region(last.bbox, last.line_index)]
        if ambiguous:
            s.add_pending(rid, {"kind": "board", "text": text, "line": last.line_index, "key": key})
            ev = self._evidence(s, rid, StepCheck("unknown", "ambiguous_input", "factorization", None, ""),
                                line=last.line_index, highlights=reg)
            s.emit("read_back", read_back_payload(rid, "board", text, alternatives=choices(text, alternatives), ambiguous=True,
                                                 note=note or "Is this what you wrote?", regions=reg), turn=turn, request=rid, rev=rev)
            s.emit("evidence", ev, turn=turn, request=rid, rev=rev)
            s.emit_state("confirm", turn=turn, rev=rev)
            await self._say(s, turn, rev, rid, "clarify",
                            "I can't tell exactly what you wrote there, so I haven't graded it. Is this what you wrote?",
                            evidence_ids=[ev["evidence_id"]], highlights=reg, tag="amb")
            return
        s.emit("read_back", read_back_payload(rid, "board", text, ambiguous=False, regions=reg), turn=turn, request=rid, rev=rev)
        await self._evaluate_line(s, turn, rid, rev, last.line_index, text)

    @staticmethod
    def _crop_key(s: V2Session, line: LineRegion) -> Optional[str]:
        """A fingerprint of the pixels of one line, so a rewritten line is never mistaken for the old one."""
        try:
            return hashlib.sha1(crop(s.board.png, line.bbox, s.board.image_region)).hexdigest()
        except MediaError:
            return None

    async def _read_line(self, s: V2Session, line: LineRegion) -> tuple[str, bool, list[str], Optional[str]]:
        B = s.board
        try:
            png = crop(B.png, line.bbox, B.image_region)
        except MediaError:
            return "", True, [], None
        key = hashlib.sha1(png).hexdigest()
        if key not in B.reads:
            B.reads[key] = (await read_lines(self.backend, [png]))[0]
            while len(B.reads) > MAX_LINE_READS:
                del B.reads[next(iter(B.reads))]
        r = B.reads[key]
        B.texts[line.line_index] = r.text
        return r.text, r.ambiguous, list(r.alternatives), r.note

    def _evidence(self, s: V2Session, rid: str, check: StepCheck, *, line: Optional[int] = None,
                  highlights: Optional[list[dict]] = None, independent: bool = False) -> dict[str, Any]:
        math = (s.problem or {}).get("subject") == "algebra"
        verified = math and check.verdict != "unknown"
        return evidence_payload(
            evidence_id=s.next_evidence_id(), request_id=rid, subject=(s.problem or {}).get("subject", "algebra"),
            step_kind=check.step_kind, verdict=check.verdict, reason_code=check.reason_code,
            label="verified" if verified else "not_verified", verifier_version=VERIFIER_VERSION,
            normalized_step=check.normalized, line_index=line, highlights=highlights, independent=independent)

    async def _evaluate_line(self, s: V2Session, turn: str, rid: str, rev: int, index: int, text: str) -> None:
        """Check one line of the student's working against the original problem, then respond as a tutor."""
        if s.transfer is not None and s.transfer.expr:
            return await self._t_quiz_answer(s, turn, rid, rev, text, line=index)  # checks, records, and emits the evidence once
        B = s.board
        line = next((ln for ln in B.lines if ln.line_index == index), None)
        reg = [region(line.bbox, index)] if line else []
        subject = (s.problem or {}).get("subject", "algebra")
        if subject != "algebra" or not (s.plan and s.plan.statement_expr):
            check = StepCheck("unknown", "unsupported", "explanation", None, "")
            ev = self._evidence(s, rid, check, line=index)
            s.emit("evidence", ev, turn=turn, request=rid, rev=rev)
            await self._say(s, turn, rev, rid, "ask",
                            "I can't verify answers in this subject automatically, so this isn't graded. "
                            + (s.lesson.checks[s.lesson.step].prompt if s.lesson.checks.get(s.lesson.step) else "What do you think comes next?"),
                            evidence_ids=[ev["evidence_id"]], tag="ung")
            return
        problem = s.plan.statement_expr
        started = time.monotonic()
        # Every line is checked against the ORIGINAL problem, never against the previous line: a corrected line
        # must not be compared with the wrong one it replaces.
        check = await acheck_student_line(self.worker, problem, text, None)
        ev = self._evidence(s, rid, check, line=index, highlights=reg if check.verdict == "invalid" else [],
                            independent=False)
        self._trace(s, turn, rev, "math", "check_math_step", check.verdict, int((time.monotonic() - started) * 1000), ev["evidence_id"])
        s.emit("evidence", ev, turn=turn, request=rid, rev=rev)
        ids = [ev["evidence_id"]]

        if not await self._ensure_lesson(s, turn, rev):
            return await self._say(s, turn, rev, rid, "clarify", "I couldn't work that problem out just now.", tag="fail")
        L = s.lesson
        step = L.step
        item = L.checks[step]
        if check.verdict == "valid":
            matched = await self._matched_step(s, check)
            final = check.step_kind == "solution_set"
            if final:
                independent = not L.assisted_steps and not any(L.misses.values())  # before the counters are cleared
                for k in range(step, L.total + 1):
                    L.misses[k] = 0
                L.step = L.total
                L.done = True
                s.skills.record(item.skill, correct=True, independent=independent)
                await self._say(s, turn, rev, rid, "recap", "That's the complete answer. Well done.", evidence_ids=ids, tag="ok")
                await self._finish_lesson(s, turn, rev, rid, assisted=s.assisted)
                return
            if matched is not None and matched >= step:
                s.skills.record(item.skill, correct=True, independent=L.misses.get(step, 0) == 0 and step not in L.assisted_steps)
                await self._advance(s, turn, rid, rev, matched, lead="Those check out.", written=None, ids=ids)
                return
            await self._say(s, turn, rev, rid, "ask", "That checks out. What would you do next?", evidence_ids=ids, tag="ok")
            return
        if check.verdict == "partial":
            await self._say(s, turn, rev, rid, "ask",
                            "That works, so it's one solution. A product of two brackets has two ways to be zero. What's the other one?",
                            evidence_ids=ids, tag="part")
            return
        if check.verdict == "invalid":
            L.misses[step] = L.misses.get(step, 0) + 1
            s.skills.record(item.skill, correct=False, independent=False)
            reason = ("Multiply your brackets back out. Which sign does each term get, and does it match the original?"
                      if check.reason_code == "sign_mismatch" and L.misses[step] == 1 else "")
            await self._wrong(s, turn, rid, rev, step, item, "", reason=reason, ids=ids)
            if line is not None and L.misses.get(step, 0) > 0 and s.placer is not None:
                circle = _circle_ink(s.next_ink_id(), line.bbox, s.placer.layout.board_bounds)
                if circle is not None:
                    await self.turns.place_ink(s, turn, rev, circle)
            return
        await self._say(s, turn, rev, rid, "ask",
                        "I can't check that step yet, so I won't mark it right or wrong. Could you write it another way?",
                        evidence_ids=ids, tag="unk")

    async def _matched_step(self, s: V2Session, check: StepCheck) -> Optional[int]:
        """Which reference step the student's line is (the furthest one of the same kind that is equivalent).

        Kind matters: every step of an equivalent chain has the same solution set, so equivalence alone would
        match the last step for any correct line. "(x+2)(x+3)=0" is a factorisation, so it matches the
        factorisation step, not the final "x=-2 or x=-3".
        """
        if not check.normalized or not (s.plan and s.plan.statement_expr):
            return None
        best: Optional[int] = None
        for n, st in enumerate(s.lesson.solution.steps, start=1):
            if not st.expr or infer_step_kind(s.plan.statement_expr, st.expr, None) != check.step_kind:
                continue
            try:
                res = await self.worker.acall("relate", prev=st.expr, cur=check.normalized)
            except Exception:
                continue
            if res.get("status") == "verified":
                best = n
        return best

    # ================================================================ ink

    async def _write_step(self, s: V2Session, turn: str, rev: int, step: int) -> None:
        """The tutor writes what was just established, using the board director."""
        L = s.lesson
        st = L.solution.steps[step - 1]
        lesson = L.lessons.get(f"s{step}")
        started = time.monotonic()
        plan = await run_board_director(
            self.backend, step, st.title, st.board_text or ([st.expr] if st.expr else []),
            lesson.intent if lesson else "", lesson.explain if lesson else "",
            [r.model_dump() for r in L.memory.board_recent()])
        self._trace(s, turn, rev, "board_director", "plan_board", "ok", int((time.monotonic() - started) * 1000))
        await self._place_plan(s, turn, rev, plan, step)

    async def _write_lines(self, s: V2Session, turn: str, rev: int, lines: list[str], tag: str) -> None:
        """Write short lines below one another without asking a model (answers, Q&A notes)."""
        cmds = [BoardCmd(id=f"b-{tag}-{i}", op="write", anchor=Anchor(rel="below"), payload=Payload(text=ln[:60]))
                for i, ln in enumerate(lines[:8], start=1)]
        await self._place_plan(s, turn, rev, BoardPlan(commands=cmds), None)

    async def _place_plan(self, s: V2Session, turn: str, rev: int, plan: BoardPlan, step: Optional[int]) -> None:
        if s.placer is None:
            return
        cmds = attach_strokes(plan.commands) if plan.commands else []
        for cmd in cmds:
            if turn in s.cancelled_turns:
                return
            for ink in cmd_to_inks(cmd, s.placer, ink_id_prefix="ink"):
                ink["ink_id"] = s.next_ink_id()
                if await self.turns.place_ink(s, turn, rev, ink) and step is not None:
                    index_board(s.lesson.memory, f"s{step}", [cmd])

    # ============================================================= summary

    async def summary(self, s: V2Session) -> dict[str, Any]:
        independent = any(i["independent"] and i["outcome"] == "valid" for i in s.summary_items)
        weakest = min(s.skills.skills.values(), key=lambda k: k.level, default=None)
        if independent:
            nxt = f"Practise a harder problem on {weakest.label.lower()}." if weakest else "Try a harder problem."
        else:
            nxt = "Try a fresh problem without hints."
        return {"items": s.summary_items, "skills": s.skills.wire(), "next_step": nxt}

    async def delete(self, session_id: str) -> dict[str, Any]:
        s = self.store.delete(session_id)
        if s is None:
            raise ServiceError(404, "unknown_session", "That session no longer exists.")
        if s.running_turn:
            await self.turns.cancel(s, s.running_turn, "deleted", emit_state=False)
        self._dispose(s)  # background tasks stopped, acks released, sockets told to close
        return {"deleted": True, "session_id": session_id}

    # ================================================================ health

    async def health(self) -> dict[str, Any]:
        comps: dict[str, dict[str, Any]] = {"session_api": {"ok": True, "revision": "0.1.0"}}
        t0 = time.monotonic()
        try:
            await self.worker.acall("relate", prev="x=1", cur="x=1")
            comps["math"] = {"ok": True, "revision": VERIFIER_VERSION, "latency_ms": int((time.monotonic() - t0) * 1000)}
        except Exception as exc:
            comps["math"] = {"ok": False, "detail": type(exc).__name__}
        if self.health_probe is not None:
            try:
                comps.update(await asyncio.wait_for(self.health_probe(), 5.0))
            except Exception as exc:
                comps["vllm"] = {"ok": False, "detail": type(exc).__name__}
        comps.setdefault("agent", {"ok": True, "detail": "local agent runtime"})
        comps.setdefault("guardrails", {"ok": True, "detail": "rule-based guard; NeMo Guardrails not integrated"})
        comps.setdefault("tts", {"ok": self.narrator is not None and getattr(self.narrator, "_tts", None) is not None,
                                 "detail": "on-device speech fallback" if getattr(self.narrator, "_tts", None) is None else "configured"})
        comps.setdefault("asr", {"ok": self.asr is not None, "detail": "not configured" if self.asr is None else "configured"})
        return {"protocol_version": 2, "components": comps}


def _rect_ok(r: tuple[float, ...], *, positive: bool = False) -> bool:
    """Four finite numbers, sane magnitude, non-negative size (strictly positive when ``positive``)."""
    if len(r) != 4 or not all(math.isfinite(v) and abs(v) < 1e6 for v in r):
        return False
    return (r[2] > 0 and r[3] > 0) if positive else (r[2] >= 0 and r[3] >= 0)


def _circle_ink(ink_id: str, bbox: tuple[float, float, float, float], board: Rect) -> Optional[dict[str, Any]]:
    """An ellipse around a student line, clamped to the board. None when it would not be a valid ink."""
    x, y, w, h = bbox
    region_rect = Rect(x - 14.0, y - 12.0, w + 28.0, h + 24.0).intersection(board)
    if region_rect is None or region_rect.w < 8 or region_rect.h < 8:
        return None
    stroke = [[round(px, 1), round(py, 1)] for px, py in _ellipse_stroke(region_rect)]
    ink = {"ink_id": ink_id, "style": "circle", "region": region_rect.to_list(), "strokes": [stroke]}
    return ink if ink_is_valid(ink, board) else None
