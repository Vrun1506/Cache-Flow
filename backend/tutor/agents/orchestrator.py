"""Orchestrator: classifies the question; it never answers it (final-plan.md §16.1).

The endpoint the iPad called decides the template. The LLM only classifies
domain, level, kind and visuals, and extracts the main equation. If the model
fails, ``fallback_plan`` produces a rule-based plan so the run still proceeds.
"""

from __future__ import annotations

import re

from ..llm import LLMBackend, LLMRequest, structured_call
from ..prompts import load_prompt
from ..schemas import Plan, PlanDraft, inline_schema
from ..tools.exprparse import UnsafeExpression, check_expr_safe, normalize

MAX_QUESTION_CHARS = 1500
_BARE_EQ = re.compile(r"(?<![<>=!])=(?!=)")


def statement_is_safe(text: str | None) -> bool:
    """True if ``text`` is one equation/expression the safe grammar accepts."""
    if not text:
        return False
    try:
        norm = normalize(text)
        sides = _BARE_EQ.split(norm)
        if len(sides) > 2:
            return False
        for side in sides:
            if not side.strip():
                return False
            check_expr_safe(side)
    except UnsafeExpression:
        return False
    except Exception:  # a hostile string must never crash the run
        return False
    return True


def extract_equation(question: str) -> str | None:
    """Pull ``2*x+3=11`` out of ``Solve 2x + 3 = 11`` using plain rules."""
    if question.count("=") != 1:
        return None
    # "Find x: x^2 = 4" -> keep what follows the colon, so the lead-in "x" is not glued on.
    if ":" in question:
        question = question.rsplit(":", 1)[1]
    tokens = question.replace("?", " ").split()
    alpha_word = re.compile(r"^[A-Za-z]{2,}[.,]?$")
    while tokens and alpha_word.match(tokens[0]):
        tokens.pop(0)
    while tokens and alpha_word.match(tokens[-1]):
        tokens.pop()
    candidate = " ".join(tokens).rstrip(".,")
    return candidate if statement_is_safe(candidate) else None


def fallback_plan(question: str, template: str = "solve_teach") -> Plan:
    """Rule-based plan used when the Orchestrator model fails twice."""
    equation = extract_equation(question)
    return Plan(
        template=template,
        domain="math" if equation else "other",
        question_clean=question.strip()[:600],
        statement_expr=equation,
        solution_kind="compute" if equation else "explain",
        level="secondary",
        complexity=3,
        safety="ok",
        visuals=[],
        confidence=0.0,
    )


async def run_orchestrator(
    backend: LLMBackend,
    question: str,
    *,
    template: str = "solve_teach",
    level_hint: str | None = None,
    summary: str = "",
    concise: bool = False,
) -> Plan:
    prompt = load_prompt("orchestrator")
    user = (
        f"<question>\n{question.strip()[:MAX_QUESTION_CHARS]}\n</question>\n"
        f"<context>\nlevel hint: {level_hint or 'none'}\nearlier work: {summary or 'none'}\n</context>\n"
        "<task>Classify the question.</task>"
    )
    req = LLMRequest(
        agent="orchestrator",
        tier="router",
        system=prompt.text,
        user=user,
        schema=inline_schema(PlanDraft),
        temperature=0.0,
        think=False,
        num_predict=300 if concise else 600,
        prompt_version=prompt.version,
    )
    draft = (await structured_call(backend, req, PlanDraft)).value
    data = draft.model_dump()
    # Never trust a model-supplied expression: keep it only if the safe grammar accepts it.
    if not statement_is_safe(data.get("statement_expr")):
        data["statement_expr"] = extract_equation(question)
    return Plan(template=template, **data)
