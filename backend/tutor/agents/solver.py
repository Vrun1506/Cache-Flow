"""Solver: one structured, worked solution (final-plan.md §3, §16.4)."""

from __future__ import annotations

from ..llm import LLMBackend, LLMRequest, structured_call
from ..prompts import load_prompt
from ..schemas import Plan, Solution, inline_schema

THINK_KINDS = {"compute", "derive", "code"}


def wants_thinking(plan: Plan) -> bool:
    """Reasoning mode only for hard computational questions (§16.4)."""
    return plan.solution_kind in THINK_KINDS and plan.complexity >= 3


def _failing_digest(previous: Solution | None, failed_ids: list[str]) -> str:
    if previous is None:
        return ""
    lines = []
    for step in previous.steps:
        if step.id in failed_ids:
            lines.append(f"{step.id}: {step.statement} | expr: {step.expr or 'none'}")
    if "final" in failed_ids:
        lines.append(f"final: {previous.final_answer.text} | value: {previous.final_answer.value or 'none'}")
    return "\n".join(lines[:6])


async def run_solver(
    backend: LLMBackend,
    plan: Plan,
    *,
    repair_hint: str | None = None,
    previous: Solution | None = None,
    failed_ids: list[str] | None = None,
    concise: bool = False,
) -> Solution:
    prompt = load_prompt("solver")
    think = wants_thinking(plan) and not concise
    num_predict = 4500 if think else (1000 if concise else 1600)

    user = (
        f"<question>\n{plan.question_clean}\n</question>\n"
        f"<context>\ndomain: {plan.domain}\nlevel: {plan.level}\nkind: {plan.solution_kind}\n"
        f"statement: {plan.statement_expr or 'none'}\n</context>\n"
    )
    if repair_hint:
        user += f"<repair>\n{repair_hint}\n{_failing_digest(previous, failed_ids or [])}\n</repair>\n"
    user += "<task>Write the worked solution.</task>"

    req = LLMRequest(
        agent="solver",
        tier="reasoner",
        system=prompt.text,
        user=user,
        schema=inline_schema(Solution),
        temperature=0.1,
        think=think,
        num_predict=num_predict,
        prompt_version=prompt.version,
    )
    return (await structured_call(backend, req, Solution)).value
