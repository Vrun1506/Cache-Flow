import json

import pytest

from sample_data import (
    EXPLAIN_PLAN,
    EXPLAIN_SOLUTION,
    ORCHESTRATOR_OK,
    QUESTION,
    SOLUTION_BAD_S2,
    SOLUTION_MISSING_ROOT,
    SOLUTION_OK,
    plan_json,
)
from tutor.agents.orchestrator import extract_equation, fallback_plan, run_orchestrator, statement_is_safe
from tutor.agents.solver import run_solver, wants_thinking
from tutor.agents.verifier import Verifier, has_symbol
from tutor.llm import FakeLLM, LLMError
from tutor.schemas import Plan, Solution
from tutor.tools.worker import ToolTimeout

pytestmark = pytest.mark.anyio


def plan(**kw):
    return Plan.model_validate(json.loads(plan_json(**kw)))


# -- orchestrator ---------------------------------------------------------


async def test_orchestrator_returns_plan_with_template():
    llm = FakeLLM({"orchestrator": [ORCHESTRATOR_OK]})
    result = await run_orchestrator(llm, QUESTION, template="solve_guide")
    assert result.template == "solve_guide"
    assert result.domain == "math" and result.statement_expr == "x**2+5*x+6=0"
    call = llm.calls[0]
    assert call.tier == "router" and call.think is False and call.schema is not None
    assert "<question>" in call.user and QUESTION in call.user


async def test_orchestrator_drops_unsafe_expression_and_extracts_its_own():
    evil = plan_json(statement_expr="__import__('os').system('x')")
    result = await run_orchestrator(FakeLLM({"orchestrator": [evil]}), "Solve 2x + 3 = 11")
    assert result.statement_expr == "2x + 3 = 11"


async def test_orchestrator_drops_expression_when_question_has_none():
    evil = plan_json(statement_expr="open('f')")
    result = await run_orchestrator(FakeLLM({"orchestrator": [evil]}), "Why is the sky blue?")
    assert result.statement_expr is None


async def test_orchestrator_concise_lowers_budget():
    llm = FakeLLM({"orchestrator": [ORCHESTRATOR_OK]})
    await run_orchestrator(llm, QUESTION, concise=True)
    assert llm.calls[0].num_predict == 300


async def test_orchestrator_propagates_failure_so_runtime_can_fall_back():
    llm = FakeLLM({"orchestrator": ["nope", "still nope"]})
    with pytest.raises(LLMError):
        await run_orchestrator(llm, QUESTION)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Solve 2x + 3 = 11", "2x + 3 = 11"),
        ("Find x: x^2 - 5x + 6 = 0.", "x^2 - 5x + 6 = 0"),
        ("What is photosynthesis?", None),
        ("a = b = c", None),
    ],
)
def test_extract_equation(text, expected):
    assert extract_equation(text) == expected


def test_statement_is_safe():
    assert statement_is_safe("x**2+5*x+6=0")
    assert statement_is_safe("(x+1)**2")
    assert not statement_is_safe("x.__class__")
    assert not statement_is_safe("x=")
    assert not statement_is_safe("a=b=c")
    assert not statement_is_safe(None)


def test_fallback_plan_is_rule_based_and_valid():
    eq = fallback_plan("Solve 2x + 3 = 11")
    assert eq.domain == "math" and eq.solution_kind == "compute" and eq.confidence == 0
    general = fallback_plan("Why is the sky blue?")
    assert general.domain == "other" and general.solution_kind == "explain" and general.visuals == []


# -- solver ---------------------------------------------------------------


def test_thinking_only_for_hard_computation():
    assert wants_thinking(plan(solution_kind="compute", complexity=3))
    assert not wants_thinking(plan(solution_kind="compute", complexity=2))
    assert not wants_thinking(plan(solution_kind="explain", complexity=5))


async def test_solver_budget_and_thinking_settings():
    llm = FakeLLM({"solver": [SOLUTION_OK, SOLUTION_OK, SOLUTION_OK]})
    await run_solver(llm, plan(complexity=4))
    await run_solver(llm, plan(complexity=1))
    await run_solver(llm, plan(complexity=4), concise=True)
    hard, easy, concise = llm.calls
    assert (hard.think, hard.num_predict) == (True, 4500)
    assert (easy.think, easy.num_predict) == (False, 1600)
    assert (concise.think, concise.num_predict) == (False, 1000)
    assert hard.tier == "reasoner" and hard.temperature == 0.1


async def test_solver_repair_prompt_names_only_failing_steps():
    llm = FakeLLM({"solver": [SOLUTION_BAD_S2, SOLUTION_OK]})
    p = plan()
    first = await run_solver(llm, p)
    assert isinstance(first, Solution)
    await run_solver(llm, p, repair_hint="s2: not equivalent to the equation", previous=first, failed_ids=["s2"])
    repair_prompt = llm.calls[1].user
    assert "<repair>" in repair_prompt and "s2: not equivalent" in repair_prompt
    assert "s2: Write the quadratic" in repair_prompt and "s1:" not in repair_prompt.split("<repair>")[1]


# -- verifier -------------------------------------------------------------


async def verify(math_worker, plan_obj, solution_json, llm=None):
    verifier = Verifier(llm or FakeLLM({}), math_worker)
    return await verifier.verify(plan_obj, Solution.model_validate_json(solution_json))


async def test_verifier_accepts_a_correct_solution(math_worker):
    report = await verify(math_worker, plan(), SOLUTION_OK)
    by_id = {v.id: v for v in report.steps}
    assert by_id["s1"].method == "none"  # an explanation step is not a "unverified" badge
    assert by_id["s2"].status == "verified" and by_id["s3"].status == "verified"
    assert report.final_answer.status == "verified"
    assert report.overall == "verified" and report.failed_ids == [] and report.repair_hint is None


async def test_verifier_fails_a_wrong_step_and_hints_the_repair(math_worker):
    report = await verify(math_worker, plan(), SOLUTION_BAD_S2)
    assert report.failed_ids[0] == "s2"
    assert report.repair_hint and report.repair_hint.startswith("s2:")
    assert report.overall == "partial"


async def test_verifier_flags_a_missing_root_as_unverified_not_failed(math_worker):
    report = await verify(math_worker, plan(), SOLUTION_MISSING_ROOT)
    assert report.final_answer.status != "verified"
    assert report.final_answer.status != "failed"


async def test_physics_cannot_reach_verified_without_units(math_worker):
    report = await verify(math_worker, plan(domain="physics"), SOLUTION_OK)
    assert all(v.status != "verified" for v in report.steps)
    assert any("units" in v.note for v in report.steps if v.method == "sympy")
    assert report.overall == "unverified"


async def test_tool_timeout_is_unverified_never_failed():
    class Hanging:
        async def acall(self, op, **kw):
            raise ToolTimeout()

    verifier = Verifier(FakeLLM({}), Hanging())
    report = await verifier.verify(plan(), Solution.model_validate_json(SOLUTION_OK))
    assert report.failed_ids == []
    assert {v.status for v in report.steps if v.method == "sympy"} == {"unverified"}


async def test_explain_questions_use_the_critic_and_never_get_verified(math_worker):
    critic = json.dumps(
        {
            "steps": [
                {"id": "s1", "verdict": "supported", "note": ""},
                {"id": "s2", "verdict": "wrong", "note": "carotenoids are not new"},
            ]
        }
    )
    llm = FakeLLM({"verifier_critic": [critic]})
    p = Plan.model_validate(json.loads(EXPLAIN_PLAN))
    report = await verify(math_worker, p, EXPLAIN_SOLUTION, llm)
    statuses = {v.id: v.status for v in report.steps}
    assert statuses == {"s1": "unverified", "s2": "failed"}
    assert all(v.method == "critic" for v in report.steps)
    assert report.repair_hint.startswith("s2:")


async def test_critic_outage_degrades_to_unverified(math_worker):
    llm = FakeLLM({"verifier_critic": [LLMError("down")]})
    p = Plan.model_validate(json.loads(EXPLAIN_PLAN))
    report = await verify(math_worker, p, EXPLAIN_SOLUTION, llm)
    assert {v.status for v in report.steps} == {"unverified"}
    assert report.failed_ids == []


def code_solution(code="def fib(n):\n    a, b = 0, 1\n    for _ in range(n):\n        a, b = b, a + b\n    return a\n",
                  tests=(("fib(10)", "55"),), extra_steps=()):
    steps = [{"id": "s1", "title": "t", "statement": "s", "kind": "code", "code": code,
              "tests": [{"input": i, "expected": e} for i, e in tests]}, *extra_steps]
    return json.dumps({"steps": steps, "final_answer": {"text": "done"}})


CODE_PLAN = dict(domain="cs", solution_kind="code", statement_expr=None)


async def test_code_that_passes_its_tests_is_verified_by_the_sandbox(math_worker):
    report = await verify(math_worker, plan(**CODE_PLAN), code_solution())
    assert report.steps[0].status == "verified" and report.steps[0].method == "python"
    assert report.overall == "verified" and report.failed_ids == []


async def test_code_that_fails_a_test_is_failed_with_counter_evidence(math_worker):
    report = await verify(math_worker, plan(**CODE_PLAN), code_solution(tests=(("fib(10)", "56"),)))
    assert report.failed_ids == ["s1"]
    assert "fib(10)" in report.steps[0].note and "55" in report.steps[0].note
    assert report.repair_hint.startswith("s1:")


async def test_code_that_does_not_compile_is_failed(math_worker):
    report = await verify(math_worker, plan(**CODE_PLAN), code_solution(code="def (:"))
    assert report.steps[0].status == "failed" and "SyntaxError" in report.steps[0].note


async def test_code_without_tests_is_unverified(math_worker):
    report = await verify(math_worker, plan(**CODE_PLAN), code_solution(tests=()))
    assert report.steps[0].status == "unverified" and "no tests" in report.steps[0].note
    assert report.overall == "unverified"


async def test_an_infinite_loop_is_unverified_not_failed(math_worker):
    from tutor.tools.sandbox import run_python

    verifier = Verifier(FakeLLM({}), math_worker, sandbox=lambda code, tests: run_python(code, tests, timeout_s=1.5))
    report = await verifier.verify(
        plan(**CODE_PLAN), Solution.model_validate_json(code_solution(code="while True:\n    pass\n", tests=(("", "1"),)))
    )
    assert report.steps[0].status == "unverified" and "timed out" in report.steps[0].note
    assert report.failed_ids == []


async def test_a_broken_sandbox_is_unverified_never_failed(math_worker):
    def boom(code, tests):
        raise OSError("no fork for you")

    verifier = Verifier(FakeLLM({}), math_worker, sandbox=boom)
    report = await verifier.verify(plan(**CODE_PLAN), Solution.model_validate_json(code_solution()))
    assert report.steps[0].status == "unverified" and "sandbox error" in report.steps[0].note and report.failed_ids == []


async def test_code_steps_without_code_are_not_a_badge(math_worker):
    prose = {"id": "s2", "title": "explain", "statement": "why it works", "kind": "explain"}
    report = await verify(math_worker, plan(**CODE_PLAN), code_solution(extra_steps=(prose,)))
    by_id = {v.id: v for v in report.steps}
    assert by_id["s2"].method == "none" and report.overall == "verified"


async def test_code_steps_run_in_parallel_and_stay_in_order(math_worker):
    second = {"id": "s2", "title": "t", "statement": "s", "kind": "code", "code": "print(2)",
              "tests": [{"input": "", "expected": "3"}]}
    report = await verify(math_worker, plan(**CODE_PLAN), code_solution(extra_steps=(second,)))
    assert [(v.id, v.status) for v in report.steps] == [("s1", "verified"), ("s2", "failed")]


def physics_solution(expr="F = m*a = 6 N", final="F = 6 N", facts=("m = 2 kg", "a = 3 m/s^2")):
    return json.dumps({
        "steps": [{"id": "s1", "title": "Newton", "statement": "F = ma", "kind": "compute", "expr": expr,
                   "facts": list(facts)}],
        "final_answer": {"text": "The force is 6 newtons.", "value": final},
    })


PHYSICS_PLAN = dict(domain="physics", solution_kind="compute", statement_expr=None)


async def test_physics_step_with_consistent_units_and_numbers_is_verified(math_worker):
    report = await verify(math_worker, plan(**PHYSICS_PLAN), physics_solution())
    assert report.steps[0].status == "verified" and report.steps[0].method == "units"
    assert report.final_answer.status == "verified" and report.final_answer.method == "units"
    assert report.overall == "verified"


async def test_physics_step_with_a_unit_error_is_failed(math_worker):
    report = await verify(math_worker, plan(**PHYSICS_PLAN), physics_solution(expr="E = 0.5*m*v^2 = 9 N",
                                                                              facts=("m = 2 kg", "v = 3 m/s")))
    assert report.failed_ids[0] == "s1" and "dimension mismatch" in report.steps[0].note
    assert report.repair_hint.startswith("s1:")


async def test_physics_step_with_a_wrong_number_is_failed(math_worker):
    report = await verify(math_worker, plan(**PHYSICS_PLAN), physics_solution(expr="F = m*a = 7 N", final="7 N"))
    assert report.steps[0].status == "failed"
    assert report.final_answer.status == "unverified"  # matching a failed step proves nothing
    assert report.failed_ids == ["s1"]


async def test_physics_final_answer_that_disagrees_with_the_work_is_failed(math_worker):
    report = await verify(math_worker, plan(**PHYSICS_PLAN), physics_solution(final="9 N"))
    assert report.steps[0].status == "verified" and report.final_answer.status == "failed"
    assert "final" in report.failed_ids


async def test_physics_step_the_units_check_cannot_decide_stays_unverified(math_worker):
    report = await verify(math_worker, plan(**PHYSICS_PLAN), physics_solution(expr="F = m*a", final=None))
    assert report.steps[0].status == "unverified" and "units" in report.steps[0].note
    assert report.failed_ids == []


async def test_chemistry_is_still_capped_at_unverified(math_worker):
    report = await verify(math_worker, plan(domain="chemistry"), SOLUTION_OK)
    assert all(v.status != "verified" for v in report.steps) and report.overall == "unverified"


async def test_physics_units_check_does_not_block_the_event_loop(math_worker):
    import asyncio
    import time

    ticks = []

    async def ticker():
        while True:
            ticks.append(time.monotonic())
            await asyncio.sleep(0.005)

    task = asyncio.create_task(ticker())
    await verify(math_worker, plan(**PHYSICS_PLAN), physics_solution())
    task.cancel()
    assert len(ticks) >= 1


def test_has_symbol():
    assert has_symbol("x=4") and has_symbol("sin(x)")
    assert not has_symbol("2*3=6") and not has_symbol("sqrt(2)*pi")


# -- chemistry (reaction balance) ----------------------------------------------

def chem_solution(exprs, final=None, kind="compute"):
    steps = [{"id": f"s{i}", "title": "t", "statement": "s", "kind": kind, "expr": e}
             for i, e in enumerate(exprs, start=1)]
    return json.dumps({"steps": steps, "final_answer": {"text": "done", "value": final}})


CHEM_PLAN = dict(domain="chemistry", solution_kind="compute", statement_expr=None)


async def test_balanced_reaction_step_and_final_answer_are_verified(math_worker):
    report = await verify(math_worker, plan(**CHEM_PLAN), chem_solution(["H2 + O2 -> H2O", "2H2 + O2 -> 2H2O"], "2H2 + O2 -> 2H2O"))
    assert [(v.status, v.method) for v in report.steps] == [("failed", "sympy"), ("verified", "sympy")]
    assert report.final_answer.status == "verified"
    assert "O differs 2 vs 1" in report.steps[0].note
    assert report.failed_ids == ["s1"] and report.repair_hint.startswith("s1: not balanced")
    assert "2" not in report.steps[0].note.replace("2 vs 1", "")  # the balanced coefficients are never leaked


async def test_all_balanced_reactions_make_the_report_verified(math_worker):
    report = await verify(math_worker, plan(**CHEM_PLAN), chem_solution(["4Fe + 3O2 → 2Fe2O3"], "4Fe + 3O2 → 2Fe2O3"))
    assert report.overall == "verified" and report.failed_ids == []


async def test_unbalanced_final_answer_is_failed(math_worker):
    report = await verify(math_worker, plan(**CHEM_PLAN), chem_solution(["2H2 + O2 -> 2H2O"], "H2 + O2 -> H2O"))
    assert report.steps[0].status == "verified" and report.final_answer.status == "failed"


async def test_unparsable_reaction_is_unverified_never_failed(math_worker):
    report = await verify(math_worker, plan(**CHEM_PLAN), chem_solution(["Fe3+ + e- -> Fe2+", "H2 <=> 2H"]))
    assert all(v.status == "unverified" for v in report.steps) and report.failed_ids == []
    assert "not checked" in report.steps[0].note


async def test_chemistry_steps_without_a_reaction_stay_capped(math_worker):
    report = await verify(math_worker, plan(**CHEM_PLAN, ), SOLUTION_OK)
    assert all(v.status != "verified" for v in report.steps) and report.overall == "unverified"


async def test_mixed_chemistry_solution_checks_only_the_reaction(math_worker):
    sol = json.dumps({
        "steps": [
            {"id": "s1", "title": "t", "statement": "s", "kind": "compute", "expr": "2H2 + O2 -> 2H2O"},
            {"id": "s2", "title": "t", "statement": "s", "kind": "explain"},
        ],
        "final_answer": {"text": "ok", "value": None},
    })
    report = await verify(math_worker, plan(**CHEM_PLAN), sol)
    assert report.steps[0].status == "verified" and report.steps[1].method == "none"


async def test_reaction_in_an_explain_solution_is_still_tool_checked(math_worker):
    sol = chem_solution(["2H2 + O2 -> 2H2O"], kind="explain")
    llm = FakeLLM({"verifier_critic": [json.dumps({"steps": [{"id": "s1", "verdict": "supported", "note": "fine"}]})]})
    report = await Verifier(llm, math_worker).verify(plan(domain="chemistry", solution_kind="explain", statement_expr=None),
                                                     Solution.model_validate_json(sol))
    assert report.steps[0].status == "verified" and report.steps[0].method == "sympy"


async def test_chemistry_checker_error_is_unverified(math_worker, monkeypatch):
    from tutor.agents import verifier as vmod

    def boom(_text):
        raise RuntimeError("checker exploded")

    monkeypatch.setattr(vmod, "check_reaction", boom)
    report = await verify(math_worker, plan(**CHEM_PLAN), chem_solution(["2H2 + O2 -> 2H2O"], "2H2 + O2 -> 2H2O"))
    assert report.steps[0].status == "unverified" and "checker error" in report.steps[0].note
    assert report.final_answer.status == "unverified" and report.failed_ids == []


async def test_other_domains_never_use_the_reaction_check(math_worker):
    report = await verify(math_worker, plan(domain="physics", solution_kind="compute", statement_expr=None),
                          chem_solution(["2H2 + O2 -> 2H2O"], "2H2 + O2 -> 2H2O"))
    assert all(v.status != "verified" for v in report.steps)
    math_report = await verify(math_worker, plan(), SOLUTION_OK)
    assert math_report.overall == "verified"
