"""Agent output schemas for the solve spine (final-plan.md §16.2).

Only what the Orchestrator, Solver and Verifier need is defined here. Lesson,
board, scene and quiz schemas are added with their agents.
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .text import Hard, Soft, cap_list

Domain = Literal["math", "physics", "chemistry", "biology", "cs", "humanities", "other"]
Level = Literal["primary", "secondary", "university"]
SolutionKind = Literal["compute", "derive", "explain", "compare", "code"]
Template = Literal["solve_teach", "solve_guide", "check_board", "ask_followup", "quiz_answer"]
Safety = Literal["ok", "redirect", "refuse"]
VisualKind = Literal["plot", "motion", "array", "graph", "number_line", "bar", "timeline", "diagram"]
Status = Literal["verified", "unverified", "failed"]
Method = Literal["sympy", "units", "python", "critic", "none"]


class _Model(BaseModel):
    # Models sometimes add keys. Ignore them rather than fail the whole call.
    model_config = ConfigDict(extra="ignore")


class Visual(_Model):
    concept: Soft(60)
    kind_hint: VisualKind


class PlanDraft(_Model):
    """What the Orchestrator LLM returns. It never chooses the graph."""

    domain: Domain
    subdomain: Soft(40) = ""
    level: Level = "secondary"
    question_clean: Soft(600)
    statement_expr: Optional[Hard(200)] = None
    solution_kind: SolutionKind
    complexity: int = Field(default=3, ge=1, le=5)
    safety: Safety = "ok"
    is_new_topic: bool = False
    visuals: Annotated[list[Visual], cap_list(3)] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)


class Plan(PlanDraft):
    template: Template = "solve_teach"


class CodeTest(_Model):
    input: Hard(200)
    expected: Hard(200)


class Step(_Model):
    id: str = Field(pattern=r"^s\d+$")
    title: Soft(60)
    statement: Soft(200)
    rationale: Soft(240) = ""
    kind: SolutionKind = "compute"
    expr: Optional[Hard(200)] = None
    code: Optional[Hard(800)] = None
    tests: Annotated[list[CodeTest], cap_list(4)] = Field(default_factory=list)
    board_text: Annotated[list[Soft(48)], cap_list(3)] = Field(default_factory=list)
    facts: Annotated[list[Soft(80)], cap_list(4)] = Field(default_factory=list)


class FinalAnswer(_Model):
    text: Soft(200)
    value: Optional[Hard(100)] = None


class Misconception(_Model):
    wrong: Soft(100)
    why: Soft(160)


class Solution(_Model):
    steps: Annotated[list[Step], Field(min_length=1, max_length=8)]
    final_answer: FinalAnswer
    key_concepts: Annotated[list[Soft(40)], cap_list(5)] = Field(default_factory=list)
    misconceptions: Annotated[list[Misconception], cap_list(3)] = Field(default_factory=list)

    @field_validator("steps")
    @classmethod
    def _sequential_ids(cls, steps: list[Step]) -> list[Step]:
        expected = [f"s{i}" for i in range(1, len(steps) + 1)]
        if [s.id for s in steps] != expected:
            raise ValueError(f"step ids must be {expected}")
        return steps


class StepVerdict(_Model):
    id: str
    status: Status
    method: Method
    note: Soft(160) = ""


class VerifyReport(_Model):
    steps: list[StepVerdict]
    final_answer: Optional[StepVerdict] = None
    overall: Literal["verified", "partial", "unverified"]
    repair_hint: Optional[Soft(400)] = None

    @property
    def failed_ids(self) -> list[str]:
        ids = [v.id for v in self.steps if v.status == "failed"]
        if self.final_answer and self.final_answer.status == "failed":
            ids.append(self.final_answer.id)
        return ids


class CriticStep(_Model):
    id: str
    verdict: Literal["supported", "doubtful", "wrong"]
    note: Soft(160) = ""


class CriticOutput(_Model):
    steps: list[CriticStep]
