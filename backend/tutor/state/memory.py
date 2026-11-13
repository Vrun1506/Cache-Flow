"""Session memory and per-agent slices (final-plan.md §16.8).

Agents never see raw history. Each one gets a small text slice built here, so
every request stays inside the 8,192 token window however long the session is.
Everything in this module is deterministic code; only ``turns_summary`` is
ever produced by a model, and a deterministic fallback exists for it.
"""

from __future__ import annotations

import time
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..llm.budget import estimate_tokens
from ..schemas import Plan, Solution
from ..schemas.quiz import QuizItem
from ..schemas.text import trim_sentence

Mode = Literal["teach", "guide", "check", "ask", "quiz"]
Status = Literal["verified", "unverified", "failed"]

MAX_LAST_TURNS = 3
SUMMARY_TOKENS = 400
SUMMARISE_EVERY_TURNS = 6
SUMMARISE_OVER_TOKENS = 1500


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore")


class BoardRef(_M):
    id: str
    step_id: str = ""
    kind: str = "write"
    label: str = ""
    rect: Optional[tuple[float, float, float, float]] = None


class Attempt(_M):
    quiz_id: str
    correct: bool
    misses: int = 0
    hint_level: int = 0
    ts: float = Field(default_factory=time.time)


class Turn(_M):
    role: Literal["student", "tutor"]
    text: str


class Memory(_M):
    session_id: str = ""
    page_id: str = ""
    run_id: str = ""
    mode: Mode = "teach"
    level: str = "secondary"
    question: str = ""
    plan: Optional[Plan] = None
    solution: Optional[Solution] = None
    verified: dict[str, Status] = Field(default_factory=dict)
    board_index: list[BoardRef] = Field(default_factory=list)
    current_step: int = 0  # 1-based; 0 means "not teaching yet"
    resume_point: Optional[str] = None
    checks: list[QuizItem] = Field(default_factory=list)  # one per taught step, in order
    quiz: list[QuizItem] = Field(default_factory=list)  # consolidation questions
    attempts: list[Attempt] = Field(default_factory=list)
    skills: dict[str, int] = Field(default_factory=dict)
    turns_summary: str = ""
    last_turns: list[Turn] = Field(default_factory=list)
    turns_since_summary: int = 0
    selected_object: Optional[str] = None

    # ------------------------------------------------------------------ updates

    def set_solution(self, plan: Plan, solution: Solution, verified: dict[str, Status]) -> None:
        self.plan = plan
        self.solution = solution
        self.question = plan.question_clean
        self.level = plan.level
        self.verified = dict(verified)

    def add_board(self, ref: BoardRef) -> None:
        self.board_index = [r for r in self.board_index if r.id != ref.id] + [ref]

    def add_turn(self, role: Literal["student", "tutor"], text: str) -> None:
        self.last_turns.append(Turn(role=role, text=trim_sentence(text, 300)))
        self.last_turns = self.last_turns[-MAX_LAST_TURNS * 2 :]
        self.turns_since_summary += 1

    def record_attempt(self, quiz_id: str, correct: bool, misses: int, hint_level: int, skill: str = "") -> None:
        self.attempts.append(Attempt(quiz_id=quiz_id, correct=correct, misses=misses, hint_level=hint_level))
        if skill:
            old = self.skills.get(skill, 2)
            self.skills[skill] = max(0, min(5, old + (1 if correct and misses == 0 else 0 if correct else -1)))

    def label_for(self, object_id: str) -> str:
        for ref in self.board_index:
            if ref.id == object_id:
                step = f" (step {ref.step_id[1:]})" if ref.step_id else ""
                return f"{ref.label or ref.kind}{step}"
        return object_id

    def step_for_object(self, object_id: str) -> Optional[int]:
        for ref in self.board_index:
            if ref.id == object_id and ref.step_id[1:].isdigit():
                return int(ref.step_id[1:])
        return None

    # ----------------------------------------------------------------- slices

    def solution_digest(self) -> str:
        """Step id, title and expr only (never the long rationale)."""
        if not self.solution:
            return ""
        lines = []
        for s in self.solution.steps:
            expr = f" | {s.expr}" if s.expr else ""
            state = self.verified.get(s.id, "unverified")
            lines.append(f"{s.id}: {s.title}{expr} [{state}]")
        fa = self.solution.final_answer
        lines.append(f"final: {fa.value or fa.text} [{self.verified.get('final', 'unverified')}]")
        return "\n".join(lines)

    def skill_gaps(self) -> list[str]:
        return [name for name, score in sorted(self.skills.items(), key=lambda kv: kv[1]) if score <= 2][:3]

    def estimated_tokens(self) -> int:
        return estimate_tokens(self.qa_slice())

    def needs_summary(self) -> bool:
        return (
            self.turns_since_summary >= SUMMARISE_EVERY_TURNS
            or self.estimated_tokens() > SUMMARISE_OVER_TOKENS
        )

    def apply_summary(self, summary: str) -> None:
        self.turns_summary = trim_sentence(summary, SUMMARY_TOKENS * 3)
        self.turns_since_summary = 0

    def fallback_summary(self) -> str:
        """Deterministic truncation when the summariser fails (§16.8)."""
        final = self.solution.final_answer.text if self.solution else ""
        recent = " / ".join(f"{t.role}: {t.text}" for t in self.last_turns[-3:])
        return trim_sentence(f"Question: {self.question} Answer: {final} Recent: {recent}", SUMMARY_TOKENS * 3)

    def orchestrator_slice(self) -> str:
        return _slice(
            ("question", self.question),
            ("level", self.level),
            ("summary", self.turns_summary),
            limit=800,
        )

    def lesson_slice(self, step_ids: list[str]) -> str:
        """Plan, the batch of steps to teach, level and skill gaps (§16.8, limit 2k tokens)."""
        if not (self.plan and self.solution):
            return ""
        batch = []
        for s in self.solution.steps:
            if s.id in step_ids:
                state = self.verified.get(s.id, "unverified")
                batch.append(
                    f"{s.id} [{state}] {s.title}: {s.statement} Why: {s.rationale} "
                    f"expr: {s.expr or 'none'} facts: {'; '.join(s.facts) or 'none'}"
                )
        gaps = ", ".join(self.skill_gaps()) or "none"
        misc = "; ".join(f"{m.wrong} ({m.why})" for m in self.solution.misconceptions) or "none"
        return _slice(
            ("topic", f"{self.plan.domain}/{self.plan.subdomain or 'general'}"),
            ("level", self.level),
            ("question", self.question),
            ("skill gaps", gaps),
            ("common mistakes", misc),
            ("steps", "\n".join(batch)),
            limit=2000,
        )

    def qa_slice(self, selected_step: Optional[int] = None) -> str:
        """Question, digest, current step, selection, summary, recent turns (§16.8, 2k tokens)."""
        step = selected_step or self.current_step
        selection = self.label_for(self.selected_object) if self.selected_object else ""
        recent = "\n".join(f"{t.role}: {t.text}" for t in self.last_turns[-MAX_LAST_TURNS:])
        return _slice(
            ("question", self.question),
            ("solution", self.solution_digest()),
            ("current step", str(step) if step else ""),
            ("student selected", selection),
            ("summary", self.turns_summary),
            ("recent turns", recent),
            limit=2000,
        )

    def board_recent(self, n: int = 6) -> list[BoardRef]:
        return self.board_index[-n:]


def _slice(*parts: tuple[str, str], limit: int) -> str:
    """Join non-empty parts, then drop whole parts from the end until under ``limit`` tokens.

    The first parts are the ones that matter most, so they are kept longest.
    """
    kept = [(name, text) for name, text in parts if text]
    while kept and estimate_tokens(_render(kept)) > limit:
        name, text = kept[-1]
        if len(kept) == 1:
            kept[-1] = (name, trim_sentence(text, limit * 3 - 40))
            break
        kept.pop()
    return _render(kept)


def _render(parts: list[tuple[str, str]]) -> str:
    return "\n".join(f"{name}: {text}" for name, text in parts)
