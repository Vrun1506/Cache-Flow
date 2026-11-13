"""Quiz designer: 2 to 3 consolidation questions (final-plan.md §7, §16.4, §16.11).

The model writes the questions. Code then checks that hints 1 and 2 do not
state the answer, and a deterministic generic quiz covers any failure so a
lesson always ends with questions.
"""

from __future__ import annotations

import re

from ..llm import LLMBackend, LLMRequest, structured_call
from ..prompts import load_prompt
from ..schemas import Plan, Solution, inline_schema
from ..schemas.quiz import Expected, QuizItem, QuizSet
from .guard import find_leaks, templated_hint

_NUMBERISH = re.compile(r"^[\s$€£]*-?[\d.,/]+\s*[a-zA-Z%°/^0-9]{0,6}\s*$")


def _digest(plan: Plan, solution: Solution, level: str) -> str:
    lines = [
        f"domain: {plan.domain}",
        f"level: {level}",
        "steps:",
        *(f"- {s.id}: {s.title} | {s.expr or s.statement}"[:140] for s in solution.steps[:8]),
        f"final answer: {solution.final_answer.text}"
        + (f" | value: {solution.final_answer.value}" if solution.final_answer.value else ""),
    ]
    if solution.key_concepts:
        lines.append("key concepts: " + "; ".join(solution.key_concepts))
    if solution.misconceptions:
        lines.append("common mistakes: " + "; ".join(f"{m.wrong} ({m.why})"[:120] for m in solution.misconceptions[:2]))
    return "\n".join(lines)


def _sanitise(quiz: QuizSet, plan: Plan) -> QuizSet:
    """Hints 1 and 2 must not state the expected answer; swap in a template if they do."""
    allowed = [plan.question_clean, plan.statement_expr or ""]
    for item in quiz.items:
        for i in (0, 1):
            if find_leaks(item.hints[i], item.expected.value, [], allowed=allowed):
                item.hints[i] = templated_hint(i + 1).say
    return quiz


def generic_quiz(plan: Plan, solution: Solution) -> QuizSet:
    """A deterministic 2-question quiz built from the key concepts and the final answer."""
    concept = (solution.key_concepts[0] if solution.key_concepts else plan.subdomain or plan.domain).strip() or "this topic"
    points = [concept] + [s.title for s in solution.steps[:2]]
    explain = QuizItem(
        id="q1",
        type="explain",
        prompt=f"In your own words, why does {concept} matter in this problem?"[:200],
        expected=Expected(key_points=points[:4]),
        hints=[
            f"What was the first idea we used here, and what was it for?",
            f"Think about {concept}. What does it tell you about the problem?",
            f"We relied on {concept} in the working. {solution.steps[0].statement}"[:160],
        ],
        skill=concept[:30],
    )
    value = (solution.final_answer.value or "").strip()
    if value and len(value) <= 60:
        kind = "numeric" if _NUMBERISH.match(value) else "short"
        recall = QuizItem(
            id="q2",
            type=kind,
            prompt="What was the final answer to the original question?",
            expected=Expected(value=value),
            hints=[
                "Look at the last line of the working. What did it end up as?",
                "Check the final step and read off the result.",
                f"The final answer was {value}. {solution.final_answer.text}"[:160],
            ],
            skill=concept[:30],
        )
    else:
        recall = QuizItem(
            id="q2",
            type="explain",
            prompt="Summarise how we reached the final answer.",
            expected=Expected(key_points=[solution.final_answer.text[:80], *[s.title for s in solution.steps[:2]]][:4]),
            hints=[
                "What was the first thing we did?",
                "Go through the main steps in order.",
                f"We ended with: {solution.final_answer.text}"[:160],
            ],
            skill=concept[:30],
        )
    return QuizSet(items=[explain, recall])


async def run_quiz_designer(
    backend: LLMBackend, plan: Plan, solution: Solution, level: str, *, concise: bool = False
) -> QuizSet:
    """Never raises: any model failure returns ``generic_quiz``."""
    prompt = load_prompt("quiz_designer")
    user = (
        f"<question>\n{plan.question_clean}\n</question>\n"
        f"<context>\n{_digest(plan, solution, level)}\n</context>\n"
        f"<task>Write {'2' if concise else '2 or 3'} questions that check understanding.</task>"
    )
    req = LLMRequest(
        agent="quiz_designer",
        tier="reasoner",
        system=prompt.text,
        user=user,
        schema=inline_schema(QuizSet),
        temperature=0.4,
        think=False,
        num_predict=600 if concise else 900,
        prompt_version=prompt.version,
    )
    try:
        quiz = (await structured_call(backend, req, QuizSet)).value
    except Exception:  # LLMError and anything unexpected; CancelledError is not an Exception
        return generic_quiz(plan, solution)
    return _sanitise(quiz, plan)
