"""Summariser: the rolling ``turns_summary`` in the session memory (final-plan.md §16.8).

It runs between turns, never on the critical path. Any failure falls back to
the deterministic summary the memory already knows how to build, so a lesson
never depends on it.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from ..llm import LLMBackend, LLMError, LLMRequest, structured_call
from ..llm.budget import estimate_tokens
from ..prompts import load_prompt
from ..schemas import inline_schema
from ..schemas.text import Soft, trim_sentence
from ..state.memory import SUMMARY_TOKENS, Memory

MAX_SUMMARY_CHARS = SUMMARY_TOKENS * 3  # 1,200 characters is the 400 token cap
MAX_INPUT_TOKENS = 1500  # the summariser's slice: well inside the 8,192 window
MAX_QUOTE = 120  # a student's words are never repeated verbatim beyond this
_PART_CAPS = (("question", 600), ("previous", MAX_SUMMARY_CHARS))
TURN_CAP = 300


class SummaryOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: Soft(MAX_SUMMARY_CHARS)


def _turn_lines(memory: Memory) -> list[str]:
    return [f"{t.role}: {trim_sentence(t.text, TURN_CAP)}" for t in memory.last_turns]


def build_user(memory: Memory) -> str:
    """The prompt body, trimmed from the oldest turns first until it fits the input budget."""
    question = trim_sentence(memory.question, _PART_CAPS[0][1])
    previous = trim_sentence(memory.turns_summary, _PART_CAPS[1][1])
    final = memory.solution.final_answer.text if memory.solution else ""
    turns = _turn_lines(memory)

    def render(ts: list[str]) -> str:
        return (
            f"<question>\n{question}\nfinal answer: {trim_sentence(final, 200) or 'unknown'}\n</question>\n"
            f"<previous>\n{previous or 'none'}\n</previous>\n"
            f"<turns>\n" + "\n".join(ts) + "\n</turns>\n"
            "<task>Update the running memory.</task>"
        )

    while len(turns) > 1 and estimate_tokens(render(turns)) > MAX_INPUT_TOKENS:
        turns = turns[1:]
    body = render(turns)
    if estimate_tokens(body) > MAX_INPUT_TOKENS:  # a single huge part: cut the previous summary first
        previous = trim_sentence(previous, 400)
        body = render(turns)
    return body


def scrub(summary: str, memory: Memory) -> str:
    """Remove long verbatim student messages and enforce the 400 token cap."""
    out = summary.strip()
    for turn in memory.last_turns:
        if turn.role == "student" and len(turn.text) > MAX_QUOTE and turn.text in out:
            out = out.replace(turn.text, trim_sentence(turn.text, 60))
    return trim_sentence(out, MAX_SUMMARY_CHARS)


async def run_summariser(backend: LLMBackend, memory: Memory) -> str:
    """Return the new ``turns_summary``. Never raises except on cancellation."""
    prompt = load_prompt("summariser")
    req = LLMRequest(
        agent="summariser",
        tier="reasoner",
        system=prompt.text,
        user=build_user(memory),
        schema=inline_schema(SummaryOut),
        temperature=0.1,
        think=False,
        num_predict=500,
        prompt_version=prompt.version,
    )
    try:
        text = scrub((await structured_call(backend, req, SummaryOut)).value.summary, memory)
    except LLMError:
        return memory.fallback_summary()
    return text or memory.fallback_summary()
