"""Q&A output (final-plan.md §16.2 AskReply, §16.9).

The Q&A agent answers and says what is worth writing down. It does not place
anything on the board: the board director turns ``board_text`` into commands,
so placement logic lives in exactly one agent.
"""

from __future__ import annotations

from typing import Annotated, Optional

from pydantic import BaseModel, ConfigDict, Field

from .core import Visual
from .text import Soft, cap_list


class AskDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    answer_spoken: Soft(480)
    board_text: Annotated[list[Soft(48)], cap_list(3)] = Field(default_factory=list)
    scene_request: Optional[Visual] = None
    followups: Annotated[list[Soft(80)], cap_list(2)] = Field(default_factory=list)
    resolves_to_step: Optional[int] = Field(default=None, ge=1, le=8)


class AskReply(AskDraft):
    """The draft plus what code adds."""

    suggest_new_page: bool = False
