"""Canned agent outputs shared by the agent and runtime tests."""

import json

QUESTION = "Solve x^2 + 5x + 6 = 0"

ORCHESTRATOR_OK = json.dumps(
    {
        "domain": "math",
        "subdomain": "quadratics",
        "level": "secondary",
        "question_clean": "Solve x^2 + 5x + 6 = 0",
        "statement_expr": "x**2+5*x+6=0",
        "solution_kind": "compute",
        "complexity": 3,
        "safety": "ok",
        "is_new_topic": False,
        "visuals": [{"concept": "parabola crossing the x axis", "kind_hint": "plot"}],
        "confidence": 0.9,
    }
)


def plan_json(**overrides):
    data = json.loads(ORCHESTRATOR_OK)
    data.update(overrides)
    return json.dumps(data)


def _solution(factored="(x+2)*(x+3)=0", roots="x=-2 or x=-3"):
    return {
        "steps": [
            {
                "id": "s1",
                "title": "Find the factor pair",
                "statement": "Find two numbers that multiply to 6 and add to 5.",
                "rationale": "2 and 3 work.",
                "kind": "compute",
                "expr": None,
                "board_text": ["2*3=6", "2+3=5"],
                "facts": ["2*3=6", "2+3=5"],
            },
            {
                "id": "s2",
                "title": "Factorise",
                "statement": "Write the quadratic as two brackets.",
                "rationale": "Brackets use the pair.",
                "kind": "compute",
                "expr": factored,
            },
            {
                "id": "s3",
                "title": "Zero product rule",
                "statement": "One bracket must be zero.",
                "rationale": "A product is zero only if a factor is.",
                "kind": "compute",
                "expr": roots,
            },
        ],
        "final_answer": {"text": "x is -2 or -3.", "value": roots},
        "key_concepts": ["factorising", "zero product rule"],
        "misconceptions": [{"wrong": "x=2", "why": "Signs flip when you solve each bracket."}],
    }


SOLUTION_OK = json.dumps(_solution())
# s2 is wrong (the right-hand side is 1, not 0), so the checker must fail it.
SOLUTION_BAD_S2 = json.dumps(_solution(factored="(x+2)*(x+3)=1"))
# Both steps are fine but the final answer misses a root.
SOLUTION_MISSING_ROOT = json.dumps(_solution(roots="x=-2"))

EXPLAIN_PLAN = plan_json(
    domain="biology",
    solution_kind="explain",
    question_clean="Why do leaves change colour in autumn?",
    statement_expr=None,
    visuals=[],
)
EXPLAIN_SOLUTION = json.dumps(
    {
        "steps": [
            {"id": "s1", "title": "Chlorophyll fades", "statement": "Green chlorophyll breaks down.", "kind": "explain"},
            {"id": "s2", "title": "Other pigments show", "statement": "Carotenoids were always there.", "kind": "explain"},
        ],
        "final_answer": {"text": "Chlorophyll fades and other pigments show."},
    }
)
