"""Quiz, verdict and hint schemas (final-plan.md §16.2).

Same rules as core.py: soft text is truncated rather than rejected, unknown
keys are ignored, and nothing here uses a JSON-schema ``pattern`` (Ollama's
grammar builder rejects it).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .text import Soft, cap_list

QuizType = Literal["mcq", "short", "numeric", "explain"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


def _empty_to_none(value: Any) -> Any:
    return None if value == [] or value == "" else value


class Expected(_Model):
    value: Optional[Soft(60)] = None
    key_points: Optional[Annotated[list[Soft(80)], cap_list(4)]] = None
    option_index: Optional[int] = Field(default=None, ge=0, le=3)

    @field_validator("value", "key_points", mode="before")
    @classmethod
    def _blank(cls, v: Any) -> Any:
        return _empty_to_none(v)


class QuizItem(_Model):
    """One question: a per-step check or a consolidation question."""

    id: Soft(10) = "q1"
    type: QuizType
    prompt: Soft(200)
    options: Optional[Annotated[list[Soft(60)], cap_list(4), Field(min_length=2)]] = None
    expected: Expected
    # Exactly three: 1 a question that does not state the answer, 2 names the
    # concept or formula, 3 the worked explanation (the reveal).
    hints: Annotated[list[Soft(160)], cap_list(3), Field(min_length=3, max_length=3)]
    skill: Soft(30) = "general"

    @field_validator("options", mode="before")
    @classmethod
    def _blank_options(cls, v: Any) -> Any:
        return _empty_to_none(v)

    @model_validator(mode="after")
    def _consistent(self) -> "QuizItem":
        e = self.expected
        if self.type == "mcq":
            if not self.options:
                raise ValueError("an mcq needs options")
            if e.option_index is None:
                if e.value is None:
                    raise ValueError("an mcq needs option_index or value")
                wanted = e.value.strip().lower()
                matches = [i for i, o in enumerate(self.options) if o.strip().lower() == wanted]
                if not matches:
                    raise ValueError("expected.value is not one of the options")
                e.option_index = matches[0]
            if e.option_index >= len(self.options):
                raise ValueError("option_index is out of range")
        elif self.type in ("short", "numeric"):
            if not e.value:
                raise ValueError(f"a {self.type} question needs expected.value")
        elif not e.key_points:
            raise ValueError("an explain question needs expected.key_points")
        return self


class QuizSet(_Model):
    """2 to 3 consolidation questions for the end of a lesson."""

    items: Annotated[list[QuizItem], cap_list(3), Field(min_length=2, max_length=3)]

    @model_validator(mode="after")
    def _renumber(self) -> "QuizSet":
        # Ids are bookkeeping, so fix them instead of spending a retry on "q1" vs "Q1".
        for i, item in enumerate(self.items, start=1):
            item.id = f"q{i}"
        return self


class Verdict(_Model):
    correct: bool
    partial: bool = False
    matched_points: Annotated[list[Soft(80)], cap_list(4)] = Field(default_factory=list)
    missing_points: Annotated[list[Soft(80)], cap_list(4)] = Field(default_factory=list)
    feedback: Soft(200) = ""
    misconception: Optional[Soft(100)] = None


class Hint(_Model):
    level: int = Field(ge=1, le=3)
    say: Soft(200)
    # TODO: becomes list[BoardCmd] (<=2) once schemas/board.py lands. Until then it is a
    # loose placeholder so the integration layer can already pass hints through.
    board: Optional[Annotated[list[dict[str, Any]], cap_list(2)]] = None


class FeedbackText(_Model):
    """What the feedback call returns (the caller trims it to 25 words)."""

    feedback: Soft(200)
