"""Lesson designer output (final-plan.md §16.2 LessonStep).

Board cues are not part of the lesson: the board director runs after the
lesson and sets them on its own commands, which is the only place a board id
can be known.
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .text import Soft, cap_list

CheckType = Literal["mcq", "short", "numeric", "explain"]


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore")


class CheckExpected(_M):
    value: Optional[Soft(60)] = None
    key_points: Optional[Annotated[list[Soft(80)], cap_list(4)]] = None
    option_index: Optional[int] = Field(default=None, ge=0, le=3)


class StepCheck(_M):
    type: CheckType
    prompt: Soft(160)
    options: Optional[Annotated[list[Soft(60)], cap_list(4)]] = None
    expected: CheckExpected = Field(default_factory=CheckExpected)
    hints: Annotated[list[Soft(160)], Field(min_length=3, max_length=3)]

    @field_validator("hints", mode="before")
    @classmethod
    def _three_hints(cls, v):
        # Models sometimes give 4. Keep the first three; fewer than three is a real error.
        return v[:3] if isinstance(v, list) else v


class LessonStep(_M):
    step_id: str = Field(pattern=r"^s\d+$")
    intent: Soft(240)
    explain: Soft(420)
    check: StepCheck


class LessonBatch(_M):
    steps: Annotated[list[LessonStep], Field(min_length=1, max_length=3)]

    @field_validator("steps", mode="before")
    @classmethod
    def _cap(cls, v):
        return v[:3] if isinstance(v, list) else v
