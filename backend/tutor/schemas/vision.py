"""What the vision reader returns (final-plan.md §16.2 "Vision", §16.11).

A ``Reading`` is a transcription, never an answer. It always goes back to the student as a
read-back before anything downstream uses it.
"""

from __future__ import annotations

from typing import Annotated, Optional

from pydantic import BaseModel, ConfigDict, Field

from .text import Soft, cap_list

MAX_LINES_PER_CALL = 8  # schema ceiling; the agent batches lower so the image tokens fit the 8,192 window


class Reading(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: Soft(300) = ""
    alternatives: Annotated[list[Soft(300)], cap_list(3)] = Field(default_factory=list)
    ambiguous: bool = False
    note: Optional[Soft(120)] = None


class LinesReading(BaseModel):
    """One ``Reading`` per image, in image order."""

    model_config = ConfigDict(extra="ignore")

    readings: Annotated[list[Reading], cap_list(MAX_LINES_PER_CALL)]
