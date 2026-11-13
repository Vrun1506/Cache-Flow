"""Lesson designer: turn solved steps into a spoken, Socratic lesson (final-plan.md §3, §16.11)."""

from __future__ import annotations

from typing import Mapping

from ..llm import LLMBackend, LLMError, LLMRequest, structured_call
from ..prompts import load_prompt
from ..schemas import Plan, Solution, Step, inline_schema
from ..schemas.lesson import CheckExpected, LessonBatch, LessonStep, StepCheck
from ..schemas.text import trim_sentence
from ..state.memory import Memory

BATCH = 3
UNVERIFIED_NOTE = "I could not fully check this part, so treat it with care."
FAILED_NOTE = "This part did not pass my automatic check, so please double-check it."


def batches(solution: Solution, size: int = BATCH) -> list[list[str]]:
    ids = [s.id for s in solution.steps]
    return [ids[i : i + size] for i in range(0, len(ids), size)]


def fallback_step(step: Step, status: str = "unverified") -> LessonStep:
    """A plain lesson built from the solver's own text, so a lesson always completes (§15.3)."""
    explain = trim_sentence(step.rationale or step.statement, 420)
    return LessonStep(
        step_id=step.id,
        intent=trim_sentence(step.statement, 240),
        explain=explain,
        check=StepCheck(
            type="explain",
            prompt="In your own words, why did we do this step?",
            expected=CheckExpected(key_points=step.facts[:3] or [trim_sentence(step.rationale or step.title, 80)]),
            hints=[
                "What was the goal of this step?",
                f"Think about: {step.title}.",
                trim_sentence(step.rationale or step.statement, 160),
            ],
        ),
    )


def apply_uncertainty(lesson: LessonStep, status: str) -> LessonStep:
    """Tell the student plainly when a step is not verified. Done in code so it is never skipped (§16.3)."""
    note = {"failed": FAILED_NOTE, "unverified": UNVERIFIED_NOTE}.get(status)
    if not note or note in lesson.explain:
        return lesson
    return lesson.model_copy(update={"explain": trim_sentence(f"{lesson.explain} {note}", 420)})


def _normalise(batch: LessonBatch, wanted: list[str], solution: Solution, verified: Mapping[str, str]) -> list[LessonStep]:
    """One entry per wanted step id, in order. Missing or mislabelled entries use the fallback."""
    by_id = {s.step_id: s for s in batch.steps}
    steps = {s.id: s for s in solution.steps}
    out: list[LessonStep] = []
    for i, sid in enumerate(wanted):
        lesson = by_id.get(sid)
        if lesson is None and i < len(batch.steps) and batch.steps[i].step_id not in wanted:
            lesson = batch.steps[i].model_copy(update={"step_id": sid})  # right order, wrong label
        status = verified.get(sid, "unverified")
        out.append(apply_uncertainty(lesson or fallback_step(steps[sid], status), status))
    return out


async def run_lesson_designer(
    backend: LLMBackend,
    memory: Memory,
    step_ids: list[str],
    *,
    concise: bool = False,
) -> list[LessonStep]:
    """Design the spoken lesson for up to three steps. Never raises except on cancellation."""
    assert memory.plan and memory.solution, "memory needs a plan and a solution"
    solution = memory.solution
    prompt = load_prompt("lesson_designer")
    ids = step_ids[:BATCH]
    user = (
        f"<question>\n{memory.question}\n</question>\n"
        f"<context>\n{memory.lesson_slice(ids)}\n</context>\n"
        f"<steps>{', '.join(ids)}</steps>\n"
        "<task>Write the lesson entries.</task>"
    )
    req = LLMRequest(
        agent="lesson_designer",
        tier="reasoner",
        system=prompt.text,
        user=user,
        schema=inline_schema(LessonBatch),
        temperature=0.4,
        think=False,
        num_predict=800 if concise else 1500,
        prompt_version=prompt.version,
    )
    try:
        batch = (await structured_call(backend, req, LessonBatch)).value
    except LLMError:
        batch = LessonBatch.model_construct(steps=[])
    return _normalise(batch, ids, solution, memory.verified)


def check_to_item(lesson: LessonStep, step: Step | None = None):
    """The step's check as a QuizItem the judge and hint ladder understand (id ``c<step>``).

    A check that fails the stricter QuizItem rules (for example an mcq whose answer is not among
    its options) becomes an "explain" question from the step's own text instead of failing the lesson.
    """
    from pydantic import ValidationError

    from ..schemas.quiz import Expected, QuizItem

    check = lesson.check
    base = {
        "id": f"c{lesson.step_id[1:]}",
        "type": check.type,
        "prompt": check.prompt,
        "options": check.options,
        "expected": Expected(**check.expected.model_dump()),
        "hints": check.hints,
        "skill": (step.title if step else lesson.step_id)[:30],
    }
    try:
        return QuizItem(**base)
    except ValidationError:
        fb = fallback_step(step, "unverified") if step else lesson
        points = fb.check.expected.key_points or [lesson.explain[:80]]
        return QuizItem(**{**base, "type": "explain", "options": None, "expected": Expected(key_points=points)})
