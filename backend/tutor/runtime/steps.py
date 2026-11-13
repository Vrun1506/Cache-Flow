"""Assembling the atomic ``step`` event (final-plan.md §16.6).

A step is only sent once its lesson text, board commands (with cues and
strokes) and spoken clips all exist, so the iPad never waits mid-step.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

from ..schemas import Step
from ..schemas.board import BoardCmd, BoardPlan
from ..schemas.lesson import LessonStep
from ..speech.tts import Clip
from ..state.memory import BoardRef, Memory

ClipUrl = Callable[[Clip], Optional[str]]


def dump_cmd(cmd: BoardCmd) -> dict[str, Any]:
    return cmd.model_dump(by_alias=True, exclude_none=True)


def build_step_event(
    index: int,
    of: int,
    step: Step,
    lesson: LessonStep,
    clips: Sequence[Clip],
    board: BoardPlan | Sequence[BoardCmd],
    status: str,
    clip_url: ClipUrl | None = None,
) -> dict[str, Any]:
    cmds = board.commands if isinstance(board, BoardPlan) else list(board)
    return {
        "index": index,
        "of": of,
        "step_id": step.id,
        "title": step.title,
        "display": list(step.board_text),
        "verified": status,
        "intent": lesson.intent,
        "explain": lesson.explain,
        "clips": [c.public(clip_url(c) if clip_url else None) for c in clips],
        "board": [dump_cmd(c) for c in cmds],
    }


def index_board(memory: Memory, step_id: str, cmds: Sequence[BoardCmd]) -> None:
    """Record what the agent wrote so later steps and follow-ups can refer to it."""
    for cmd in cmds:
        label = (cmd.payload.text or cmd.payload.ref or cmd.op)[:40]
        memory.add_board(BoardRef(id=cmd.id, step_id=step_id, kind=cmd.op, label=label.replace("\n", " ")))
