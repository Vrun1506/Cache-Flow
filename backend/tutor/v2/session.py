"""One v2 session: ordered events with replay, request idempotency, board state (Interface.md).

Everything the iPad can observe goes through :meth:`V2Session.emit`, which assigns the
session-wide monotonic ``seq``, drops events of cancelled turns, keeps a bounded log and
fans out to the connected sockets. Replay after a reconnect re-sends durable events with
their original ``seq`` and ``replayed: true``, followed by a fresh ``state`` (answers
CONTRACT-QUESTIONS 1 and 2).
"""

from __future__ import annotations

import asyncio
import copy
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from ..schemas import Plan, Solution, VerifyReport
from ..schemas.lesson import LessonStep
from ..schemas.quiz import QuizItem
from ..state.memory import Memory
from .skills import SkillBook
from .wire import IDLE, envelope, is_durable, state_payload

MAX_LOG = 5000
SUBSCRIBER_QUEUE = 500
MAX_AUDIO_CLIPS = 200
MAX_AUDIO_BYTES = 32 * 1024 * 1024  # per session: a long lesson of real TTS clips must not grow without bound
MAX_PENDING_READBACKS = 50
MAX_LINE_READS = 200


@dataclass
class LineRegion:
    line_index: int
    bbox: tuple[float, float, float, float]


@dataclass
class BoardState:
    """What the student's board looked like at the last upload (Interface.md `/board`)."""

    revision: int = -1
    png: Optional[bytes] = None
    image_region: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    lines: list[LineRegion] = field(default_factory=list)
    texts: dict[int, str] = field(default_factory=dict)  # line_index -> text read from the image
    confirmed: dict[int, str] = field(default_factory=dict)  # line_index -> text the student confirmed
    reads: dict[str, Any] = field(default_factory=dict)  # sha1(line crop) -> Reading, so a line is read once
    confirmed_key: dict[int, str] = field(default_factory=dict)  # line_index -> sha1 of the crop the student confirmed


@dataclass
class Lesson:
    """The hidden reference solution and where the student is in it."""

    plan: Optional[Plan] = None
    solution: Optional[Solution] = None
    report: Optional[VerifyReport] = None
    memory: Memory = field(default_factory=Memory)
    lessons: dict[str, LessonStep] = field(default_factory=dict)
    checks: dict[int, QuizItem] = field(default_factory=dict)  # step number -> its check question
    step: int = 1  # 1-based: the step the student is working on
    misses: dict[int, int] = field(default_factory=dict)  # wrong answers per step
    hints_shown: dict[int, int] = field(default_factory=dict)  # hint requests per step
    assisted_steps: set[int] = field(default_factory=set)
    done: bool = False
    preparing: Optional[asyncio.Task] = None

    @property
    def ready(self) -> bool:
        return self.solution is not None

    @property
    def total(self) -> int:
        return len(self.solution.steps) if self.solution else 0


@dataclass
class Transfer:
    display: str
    expr: Optional[str]
    item: Optional[QuizItem] = None  # used when no equation can be generated
    attempts: int = 0


@dataclass
class Pending:
    """An answer the tutor is waiting for: the check of the current step."""

    step: int


@dataclass
class V2Session:
    session_id: str = field(default_factory=lambda: "s-" + uuid.uuid4().hex[:10])
    locale: str = "en"
    preset_id: Optional[str] = None
    board_revision: int = 0
    seq: int = 0
    phase: str = IDLE
    problem: Optional[dict[str, Any]] = None
    plan: Optional[Plan] = None
    level: str = "secondary"

    log: list[dict[str, Any]] = field(default_factory=list)
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    requests: dict[str, dict[str, Any]] = field(default_factory=dict)  # request_id -> accepted response
    pending_readbacks: dict[str, dict[str, Any]] = field(default_factory=dict)

    board: BoardState = field(default_factory=BoardState)
    lesson: Lesson = field(default_factory=Lesson)
    pending: Optional[Pending] = None
    transfer: Optional[Transfer] = None
    skills: SkillBook = field(default_factory=SkillBook)
    summary_items: list[dict[str, Any]] = field(default_factory=list)
    assisted: bool = False  # the current item has been helped
    complete: bool = False  # the lesson and its independent item are finished
    inflight: dict = field(default_factory=dict)  # request_id -> Future for slow idempotent requests (audio)

    turn_no: int = 0
    running_turn: Optional[str] = None
    latest_turn_id: Optional[str] = None
    cancelled_turns: set[str] = field(default_factory=set)
    runner: Optional[asyncio.Task] = None
    setup_task: Optional[asyncio.Task] = None
    background: set = field(default_factory=set)  # fire-and-forget tasks (summaries)
    summarising: bool = False
    evidence_no: int = 0
    ink_no: int = 0
    scene_no: int = 0
    acks: dict[str, asyncio.Future] = field(default_factory=dict)
    audio: dict[str, bytes] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    placer: Any = None  # tutor.v2.layout.Placer, created by the service

    # ---------------------------------------------------------------- emission

    def emit(
        self,
        type: str,
        payload: dict[str, Any],
        *,
        turn: Optional[str] = None,
        request: Optional[str] = None,
        rev: Optional[int] = None,
    ) -> Optional[dict[str, Any]]:
        """Send one event. Events of a cancelled turn are dropped (Interface.md: old audio, ink and scenes are ignored)."""
        if turn is not None and turn in self.cancelled_turns:
            return None
        self.seq += 1
        env = envelope(
            type, self.session_id, self.seq, self.board_revision if rev is None else rev, payload,
            turn_id=turn, run_id=f"run-{turn}" if turn else None, request_id=request,
        )
        self.log.append(env)
        if len(self.log) > MAX_LOG:
            del self.log[: len(self.log) - MAX_LOG]
        for queue in list(self.subscribers):
            try:
                queue.put_nowait(env)
            except asyncio.QueueFull:
                self.drop_subscriber(queue)  # the socket is closed, so the client reconnects and replays
        return env

    def drop_subscriber(self, queue: asyncio.Queue) -> None:
        """Stop feeding ``queue`` and make its socket close (a ``None`` is the close signal)."""
        self.subscribers.discard(queue)
        while True:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        queue.put_nowait(None)

    def close_subscribers(self) -> None:
        for queue in list(self.subscribers):
            self.drop_subscriber(queue)

    def snapshot(self, phase: Optional[str] = None) -> dict[str, Any]:
        return state_payload(phase or self.phase, self.problem, self.latest_turn_id, self.skills.wire())

    def emit_state(self, phase: Optional[str] = None, *, turn: Optional[str] = None,
                   request: Optional[str] = None, rev: Optional[int] = None) -> None:
        if phase is not None:
            self.phase = phase
        self.emit("state", self.snapshot(), turn=turn, request=request, rev=rev)

    def emit_error(self, code: str, message: str, *, retryable: bool = False, request: Optional[str] = None) -> None:
        from .wire import error_payload

        self.emit("error", error_payload(code, message, retryable=retryable, request_id=request,
                                         latest_board_revision=self.board_revision), request=request)

    # ------------------------------------------------------------ subscribers

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE)
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self.subscribers.discard(queue)

    def replay_after(self, cursor: int) -> list[dict[str, Any]]:
        """Durable events with ``seq > cursor``, original seq kept, marked ``replayed`` (speech is never replayed)."""
        out = []
        for env in self.log:
            if env["seq"] > cursor and is_durable(env):
                env = copy.deepcopy(env)
                env["replayed"] = True
                # An audio URL in a replayed tutor_turn must not play again; the client also suppresses it.
                out.append(env)
        return out

    # --------------------------------------------------------------- requests

    def add_pending(self, request_id: str, info: dict[str, Any]) -> None:
        """A read-back waiting for the student. The oldest are forgotten when they pile up unanswered."""
        self.pending_readbacks[request_id] = info
        while len(self.pending_readbacks) > MAX_PENDING_READBACKS:
            del self.pending_readbacks[next(iter(self.pending_readbacks))]

    def remember(self, request_id: str, response: dict[str, Any]) -> dict[str, Any]:
        self.requests[request_id] = response
        if len(self.requests) > 2000:
            for key in list(self.requests)[:500]:
                del self.requests[key]
        return response

    def store_audio(self, name: str, wav: bytes) -> str:
        self.audio[name] = wav
        total = sum(len(v) for v in self.audio.values())
        while len(self.audio) > 1 and (len(self.audio) > MAX_AUDIO_CLIPS or total > MAX_AUDIO_BYTES):
            total -= len(self.audio.pop(next(iter(self.audio))))
        return f"/v2/sessions/{self.session_id}/audio/{name}.wav"

    def next_evidence_id(self) -> str:
        self.evidence_no += 1
        return f"ev-{self.evidence_no}"

    def next_ink_id(self) -> str:
        self.ink_no += 1
        return f"ink-{self.ink_no}"

    def next_scene_id(self) -> str:
        self.scene_no += 1
        return f"scene-{self.scene_no}"


class SessionStore:
    def __init__(self, max_sessions: int = 50) -> None:
        self._sessions: dict[str, V2Session] = {}
        self._max = max_sessions

    def add(self, session: V2Session) -> Optional[V2Session]:
        """Store ``session``; returns the oldest session if one had to be evicted (the caller stops its work)."""
        evicted = None
        if len(self._sessions) >= self._max:
            evicted = self._sessions.pop(next(iter(self._sessions)))
        self._sessions[session.session_id] = session
        return evicted

    def get(self, session_id: str) -> Optional[V2Session]:
        return self._sessions.get(session_id)

    def delete(self, session_id: str) -> Optional[V2Session]:
        return self._sessions.pop(session_id, None)

    def __len__(self) -> int:
        return len(self._sessions)
