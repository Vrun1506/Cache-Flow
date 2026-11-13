"""Board director: turns one lesson step into pen commands (final-plan.md §5, §16.11).

It never raises on a model failure: a deterministic fallback keeps the step
presentable, so a lesson always completes (§15.3).
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..llm import LLMBackend, LLMError, LLMRequest, structured_call
from ..prompts import load_prompt
from ..schemas.board import Anchor, BoardCmd, BoardPlan, Cue, Payload, board_id, board_llm_schema

RECENT_LIMIT = 6
MAX_LINES = 3
MAX_LINE_CHARS = 48


def _lines(board_text: Sequence[str], title: str) -> list[str]:
    lines = [ln.strip()[:MAX_LINE_CHARS] for ln in board_text if ln and ln.strip()]
    return lines[:MAX_LINES] or [title.strip()[:MAX_LINE_CHARS] or "…"]


def fallback_board(step_index: int, board_text: Sequence[str], title: str) -> BoardPlan:
    """One write of the step's lines under the latest object, then an underline at the end."""
    first, second = board_id(step_index, 1), board_id(step_index, 2)
    return BoardPlan(
        commands=[
            BoardCmd(
                id=first,
                op="write",
                anchor=Anchor(rel="below"),
                payload=Payload(text="\n".join(_lines(board_text, title))),
                cue=Cue(at="after_intent"),
            ),
            BoardCmd(
                id=second,
                op="highlight",
                anchor=Anchor(rel="inside", ref=first),
                payload=Payload(ref=first, kind="box"),
                cue=Cue(at="end"),
            ),
        ]
    )


def _recent_text(recent_index: Sequence[Mapping[str, Any]]) -> str:
    rows = [f"{e.get('id')} ({e.get('kind', 'object')}): {str(e.get('label', ''))[:40]}" for e in recent_index[-RECENT_LIMIT:]]
    return "\n".join(rows) if rows else "(empty)"


def validate_plan(plan: BoardPlan, step_index: int, known_ids: set[str]) -> BoardPlan:
    """Drop commands that point at unknown objects, then renumber ids to ``b-<step>-<n>``."""
    mapping: dict[str, str] = {}
    kept: list[BoardCmd] = []
    for cmd in plan.commands:
        refs = [r for r in cmd.refs_used()]
        if any(r not in known_ids and r not in mapping for r in refs):
            continue  # also drops anything that referred to a dropped command
        new_id = board_id(step_index, len(kept) + 1)
        cmd = cmd.model_copy(deep=True)
        mapping[cmd.id] = new_id
        a, p = cmd.anchor, cmd.payload
        a.ref = mapping.get(a.ref, a.ref) if a.ref else None
        p.ref = mapping.get(p.ref, p.ref) if p.ref else None
        p.from_ = mapping.get(p.from_, p.from_) if p.from_ else None
        p.to = mapping.get(p.to, p.to) if p.to else None
        if p.refs:
            p.refs = [mapping.get(r, r) for r in p.refs]
        cmd.id = new_id
        kept.append(cmd)
    return BoardPlan(commands=kept)


async def run_board_director(
    backend: LLMBackend,
    step_index: int,
    step_title: str,
    board_text: Sequence[str],
    intent: str,
    explain: str,
    recent_index: Sequence[Mapping[str, Any]] = (),
    *,
    concise: bool = False,
) -> BoardPlan:
    prompt = load_prompt("board_director")
    lines = _lines(board_text, step_title)
    user = (
        f"<context>\nstep {step_index}: {step_title}\nboard lines:\n" + "\n".join(f"- {ln}" for ln in lines) + "\n"
        f"</context>\n<board>\n{_recent_text(recent_index)}\n</board>\n"
        f"<narration>\nintent: {intent}\nexplain: {explain}\n</narration>\n"
        f"<task>Plan the board commands for step {step_index}. Ids start at b-{step_index}-1."
        + (" Use at most 3 commands." if concise else "")
        + "</task>"
    )
    req = LLMRequest(
        agent="board_director",
        tier="router",
        system=prompt.text,
        user=user,
        schema=board_llm_schema(),
        temperature=0.2,
        think=False,
        num_predict=600 if concise else 1200,
        prompt_version=prompt.version,
    )
    try:
        plan = (await structured_call(backend, req, BoardPlan)).value
    except LLMError:
        return fallback_board(step_index, board_text, step_title)
    plan = validate_plan(plan, step_index, {str(e.get("id")) for e in recent_index if e.get("id")})
    return plan if plan.commands else fallback_board(step_index, board_text, step_title)
