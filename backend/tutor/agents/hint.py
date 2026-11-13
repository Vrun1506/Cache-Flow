"""Hint ladder and feedback (final-plan.md §16.5, §16.11).

One rule everywhere: miss 1 gives hint 1 (a question), miss 2 gives hint 2
(names the concept), miss 3 gives hint 3 (the worked explanation) and then the
lesson moves on. The ladder never loops.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from typing import Literal

from ..llm import LLMBackend, LLMRequest, structured_call
from ..prompts import load_prompt
from ..schemas import inline_schema
from ..schemas.quiz import FeedbackText, Hint, QuizItem, Verdict
from .guard import leaks

MAX_FEEDBACK_WORDS = 25
MAX_MISSES = 3

ActionKind = Literal["next_step", "hint", "reveal_and_advance"]


@dataclass(frozen=True)
class Action:
    kind: ActionKind
    level: int | None = None  # the hint level to show (3 for the reveal)


def advance(misses: int, correct: bool) -> Action:
    """What to do after an answer.

    ``misses`` counts the wrong answers so far for this item, including the one
    just given. A correct answer always moves on.
    """
    if correct:
        return Action("next_step")
    misses = max(misses, 1)
    if misses >= MAX_MISSES:
        return Action("reveal_and_advance", 3)
    return Action("hint", misses)


def next_hint(item: QuizItem, miss_count: int) -> Hint:
    """The pre-written hint for this miss. No model call, so it can never fail."""
    level = min(max(miss_count, 1), 3)
    return Hint(level=level, say=item.hints[level - 1])


# -- feedback --------------------------------------------------------------------

_TEMPLATES = {
    "correct": ("That's right. Nicely done.", "Yes, exactly. Well done.", "Correct, great work."),
    "partial": (
        "You're close. Let's look at it again.",
        "Good start. There's a bit more to it.",
        "Nearly there. Take another look.",
    ),
    "wrong": (
        "Not quite. Let's look at it again.",
        "Not yet, but that's fine. Have another go.",
        "Close, but not there. Let's think again.",
    ),
}


def templated_feedback(item: QuizItem, verdict: Verdict) -> str:
    kind = "correct" if verdict.correct else "partial" if verdict.partial else "wrong"
    options = _TEMPLATES[kind]
    return options[zlib.crc32(item.id.encode()) % len(options)]


def trim_words(text: str, limit: int = MAX_FEEDBACK_WORDS) -> str:
    words = text.split()
    if len(words) <= limit:
        return " ".join(words)
    cut = " ".join(words[:limit]).rstrip(",;:")
    return cut if cut.endswith((".", "!", "?")) else cut + "."


def _feedback_user(item: QuizItem, verdict: Verdict, reveal: bool) -> str:
    # The answer is only in the context when feedback is allowed to state it.
    lines = [f"question: {item.prompt}", f"student was: {'correct' if verdict.correct else 'partly right' if verdict.partial else 'wrong'}"]
    if verdict.matched_points:
        lines.append("got right: " + "; ".join(verdict.matched_points))
    if verdict.missing_points:
        lines.append("missed: " + "; ".join(verdict.missing_points))
    if verdict.misconception:
        lines.append(f"likely misconception: {verdict.misconception}")
    if reveal or verdict.correct:
        if item.expected.value:
            lines.append(f"expected answer: {item.expected.value}")
        if item.expected.key_points:
            lines.append("key points: " + "; ".join(item.expected.key_points))
    return "<context>\n" + "\n".join(lines) + "\n</context>\n<task>Write the feedback.</task>"


async def run_feedback(
    backend: LLMBackend, item: QuizItem, verdict: Verdict, *, concise: bool = False, reveal: bool = False
) -> str:
    """Kind feedback in at most 25 words. Never raises; falls back to a template."""
    prompt = load_prompt("hint")
    req = LLMRequest(
        agent="feedback",
        tier="reasoner",
        system=prompt.text,
        user=_feedback_user(item, verdict, reveal),
        schema=inline_schema(FeedbackText),
        temperature=0.4,
        think=False,
        num_predict=200 if concise else 400,
        prompt_version=prompt.version,
    )
    try:
        text = trim_words((await structured_call(backend, req, FeedbackText)).value.feedback)
    except Exception:  # CancelledError is not an Exception, so cancellation still propagates
        return templated_feedback(item, verdict)
    if not text:
        return templated_feedback(item, verdict)
    if not (reveal or verdict.correct) and leaks(text, item.expected.value):
        return templated_feedback(item, verdict)  # feedback on a wrong answer must not give the answer away
    return text
