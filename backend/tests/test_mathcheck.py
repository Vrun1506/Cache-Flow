import pytest

from tutor.tools.mathcheck import (
    REASONS,
    STEP_KINDS,
    VERDICTS,
    StepCheck,
    acheck_math_step,
    check_math_step,
    check_student_line,
    infer_step_kind,
    normalise_student_text,
)
from tutor.tools.worker import MathWorker, ToolError, ToolTimeout

QUAD = "x^2 + 5x + 6 = 0"


def line(w, problem, text, previous=None):
    return check_student_line(w, problem, text, previous)


def step(w, problem, kind, text, previous=None, **kw):
    return check_math_step(w, problem, kind, text, previous, **kw)


def expect(res, verdict, reason=None, kind=None):
    assert res.verdict == verdict, res
    if reason is not None:
        assert res.reason_code == reason, res
    if kind is not None:
        assert res.step_kind == kind, res


# ------------------------------------------------------------------ text normalisation


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("x − 2", "x - 2"),
        ("3 × 4", "3 * 4"),
        ("x²+5x+6", "x**2+5x+6"),
        ("x³", "x**3"),
        ("{-2, -3}", "-2, -3"),
        ("{−2,−3}", "-2,-3"),
        ("x = 1; x = 2", "x = 1, x = 2"),
        ("x = 1 OR x = 2", "x = 1 or x = 2"),
        ("= 14", "14"),
        ("√(x+2) = x", "sqrt(x+2) = x"),
        ("√9", "sqrt(9)"),
        ("  x  =   4  ", "x = 4"),
        ("6 ÷ 3", "6 / 3"),
    ],
)
def test_normalise_student_text(raw, expected):
    assert normalise_student_text(raw) == expected


def test_normalise_is_total_on_junk():
    assert normalise_student_text(None) == ""
    assert normalise_student_text("") == ""


# ---------------------------------------------------------------------- kind inference


@pytest.mark.parametrize(
    "problem,text,kind",
    [
        (QUAD, "(x+2)(x+3)=0", "factorization"),
        (QUAD, "(x+2)(x+3)", "factorization"),
        (QUAD, "x^2+5x+6", "expression_identity"),
        (QUAD, "x = -2", "candidate_root"),
        (QUAD, "-2", "candidate_root"),
        (QUAD, "x=-2 or x=-3", "solution_set"),
        (QUAD, "{-2,-3}", "solution_set"),
        (QUAD, "x=-2, x=-3", "solution_set"),
        (QUAD, "x^2 = -5x - 6", "equivalent_equation"),
        ("2x+3=11", "2x = 8", "equivalent_equation"),
        ("2x+3=11", "x = 4", "candidate_root"),
        ("3*4+2", "14", "numeric_result"),
        ("3*4+2", "= 14", "numeric_result"),
        (QUAD, "therefore the roots are nice", "explanation"),
        (QUAD, "", "explanation"),
    ],
)
def test_infer_step_kind(problem, text, kind):
    assert infer_step_kind(problem, text, None) == kind


# ------------------------------------------------------------ the fixture lesson (x^2+5x+6=0)


def test_correct_factorization_is_valid(math_worker):
    r = line(math_worker, QUAD, "(x+2)(x+3)=0")
    expect(r, "valid", "ok", "factorization")
    assert r.normalized == "(x+2)*(x+3)=0"


def test_unicode_input_is_accepted(math_worker):
    expect(line(math_worker, "x² + 5x + 6 = 0", "(x + 2)(x + 3) = 0"), "valid", "ok")
    expect(line(math_worker, QUAD, "x = −2"), "partial", "partial_root")


def test_flipped_signs_is_sign_mismatch(math_worker):
    expect(line(math_worker, QUAD, "(x − 2)(x − 3) = 0"), "invalid", "sign_mismatch", "factorization")


def test_one_wrong_sign_is_still_sign_mismatch(math_worker):
    expect(line(math_worker, QUAD, "(x + 2)(x − 3) = 0"), "invalid", "sign_mismatch", "factorization")


def test_expression_with_flipped_sign_is_sign_mismatch(math_worker):
    expect(line(math_worker, QUAD, "(x-2)(x-3)"), "invalid", "sign_mismatch")
    expect(step(math_worker, QUAD, "expression_identity", "x^2 - 5x + 6"), "invalid", "sign_mismatch")


def test_expression_rewrite_is_valid(math_worker):
    expect(step(math_worker, QUAD, "expression_identity", "x² + 5x + 6"), "valid", "ok")
    expect(step(math_worker, QUAD, "factorization", "(x+3)(x+2)"), "valid", "ok")


def test_wrong_but_not_sign_expression_is_not_equivalent(math_worker):
    expect(step(math_worker, QUAD, "factorization", "(x+1)(x+6)"), "invalid", "not_equivalent")


def test_single_root_is_partial(math_worker):
    r = line(math_worker, QUAD, "x = −2")
    expect(r, "partial", "partial_root", "candidate_root")
    assert r.normalized == "x=-2"


def test_other_single_root_is_partial_too(math_worker):
    expect(line(math_worker, QUAD, "x = -3"), "partial", "partial_root")


@pytest.mark.parametrize("text", ["x=-2 or x=-3", "x = −3 or x = −2", "{−2,−3}", "{-2, -3}", "x=-2, x=-3", "-2, -3", "x=-2 and x=-3"])
def test_complete_solution_set_is_valid(math_worker, text):
    r = line(math_worker, QUAD, text)
    expect(r, "valid", "ok", "solution_set")
    assert r.normalized == "{-3,-2}"


def test_subset_solution_set_is_partial(math_worker):
    expect(step(math_worker, QUAD, "solution_set", "{-2}"), "partial", "partial_root", "solution_set")


def test_wrong_root_is_a_sign_mismatch_when_the_negation_is_a_root(math_worker):
    expect(line(math_worker, QUAD, "x = 2"), "invalid", "sign_mismatch", "candidate_root")


def test_set_of_negated_roots_is_sign_mismatch(math_worker):
    expect(line(math_worker, QUAD, "{2, 3}"), "invalid", "sign_mismatch", "solution_set")


def test_unrelated_wrong_root_is_extraneous(math_worker):
    expect(line(math_worker, QUAD, "x = 7"), "invalid", "extraneous_root")


def test_set_with_a_false_member_is_extraneous(math_worker):
    expect(line(math_worker, QUAD, "x = -2 or x = 5"), "invalid", "extraneous_root", "solution_set")


def test_superset_with_extra_root_is_invalid(math_worker):
    expect(step(math_worker, QUAD, "solution_set", "x=-2 or x=-3 or x=1"), "invalid", "extraneous_root")


def test_duplicate_values_count_once(math_worker):
    expect(step(math_worker, QUAD, "solution_set", "x=-2 or x=-2 or x=-3"), "valid", "ok")


def test_equation_with_no_real_roots(math_worker):
    expect(step(math_worker, "x^2 + 1 = 0", "candidate_root", "x = 1"), "invalid", "extraneous_root")


# --------------------------------------------------------------------- linear equation


def test_linear_steps(math_worker):
    p = "2x+3=11"
    expect(line(math_worker, p, "2x = 8", p), "valid", "ok", "equivalent_equation")
    expect(line(math_worker, p, "x = 4", "2x = 8"), "valid", "ok", "candidate_root")
    expect(line(math_worker, p, "x = 4"), "valid", "ok")
    expect(line(math_worker, p, "2x = 14", p), "invalid", "not_equivalent")
    expect(line(math_worker, p, "x = -4"), "invalid", "sign_mismatch")


def test_linear_with_fraction_answer(math_worker):
    expect(step(math_worker, "2x = 1", "candidate_root", "x = 1/2"), "valid", "ok")
    expect(step(math_worker, "2x = 1", "candidate_root", "x = 0.5"), "valid", "ok")


# ---------------------------------------------------------------- domains and extraneous roots


def test_squaring_both_sides_adds_an_extraneous_root(math_worker):
    r = step(math_worker, "√(x+2) = x", "equivalent_equation", "x + 2 = x^2")
    expect(r, "invalid", "extraneous_root", "equivalent_equation")


def test_squaring_step_checked_against_the_previous_line(math_worker):
    expect(step(math_worker, "√(x+2) = x", "equivalent_equation", "x+2 = x^2", "√(x+2) = x"), "invalid", "extraneous_root")


def test_dividing_by_a_possibly_zero_variable_changes_the_domain(math_worker):
    r = step(math_worker, "x^2 = 3x", "equivalent_equation", "x = 3", "x^2 = 3x")
    expect(r, "invalid", "domain_changed")


def test_clearing_a_denominator_adds_an_excluded_value(math_worker):
    r = step(math_worker, "(x^2-1)/(x-1) = 0", "equivalent_equation", "x^2 - 1 = 0")
    expect(r, "invalid", "extraneous_root")


def test_cancelling_a_denominator_correctly_is_valid(math_worker):
    expect(step(math_worker, "(x^2-1)/(x-1) = 0", "equivalent_equation", "x + 1 = 0"), "valid", "ok")


def test_expression_identity_that_changes_the_domain(math_worker):
    r = step(math_worker, "(x^2-1)/(x-1)", "expression_identity", "x + 1")
    expect(r, "invalid", "domain_changed")


def test_scaled_equation_is_valid_via_solution_sets(math_worker):
    r = step(math_worker, "2x^2 + 10x + 12 = 0", "factorization", "(x+2)(x+3) = 0")
    expect(r, "valid", "ok")


def test_trig_identity_and_counterexample(math_worker):
    expect(step(math_worker, "sin(x)^2 + cos(x)^2", "expression_identity", "1"), "valid", "ok")
    expect(step(math_worker, "sin(x)", "expression_identity", "cos(x)"), "invalid", "not_equivalent")


def test_infinite_solution_sets_only_support_a_candidate_check(math_worker):
    expect(step(math_worker, "sin(x) = 0", "candidate_root", "x = pi"), "valid", "ok")
    expect(step(math_worker, "sin(x) = 0", "solution_set", "x = 0 or x = pi"), "unknown", "unsupported")


# ---------------------------------------------------------------------- numeric results


def test_arithmetic_result(math_worker):
    r = line(math_worker, "3*4+2", "14")
    expect(r, "valid", "ok", "numeric_result")
    expect(line(math_worker, "3*4+2", "= 14"), "valid", "ok")
    expect(line(math_worker, "3*4+2", "3*4+2 = 14"), "valid", "ok")


def test_wrong_arithmetic_result(math_worker):
    expect(line(math_worker, "3*4+2", "15"), "invalid", "not_equivalent")
    expect(line(math_worker, "3*4-20", "8"), "invalid", "sign_mismatch")


def test_numeric_tolerance_and_rounding(math_worker):
    expect(step(math_worker, "10/3", "numeric_result", "3.333333333"), "valid", "ok")
    expect(step(math_worker, "10/3", "numeric_result", "3.33"), "valid", "ok")  # rounded to 2 places
    expect(step(math_worker, "10/3", "numeric_result", "3.3"), "valid", "ok")
    expect(step(math_worker, "10/3", "numeric_result", "3"), "invalid", "not_equivalent")  # no decimals typed
    expect(step(math_worker, "10/3", "numeric_result", "3.34"), "invalid", "not_equivalent")
    expect(step(math_worker, "10/3", "numeric_result", "3.2"), "invalid", "not_equivalent")


def test_numeric_result_respects_a_custom_tolerance(math_worker):
    assert step(math_worker, "100", "numeric_result", "100.4", rel_tol=1e-2).verdict == "valid"
    assert step(math_worker, "100", "numeric_result", "100.4", rel_tol=1e-6).verdict == "invalid"


def test_numeric_root_of_an_equation(math_worker):
    expect(step(math_worker, "2x + 3 = 11", "numeric_result", "x = 4"), "valid", "ok")
    expect(step(math_worker, "x^2 = 2", "numeric_result", "1.4142135"), "partial", "partial_root")


def test_numeric_with_units_is_not_graded_here(math_worker):
    expect(step(math_worker, "9.8*2", "numeric_result", "19.6 m"), "unknown", "unsupported")


def test_numeric_results_of_physics_arithmetic(math_worker):
    expect(line(math_worker, "9.8*2", "19.6"), "valid", "ok")
    expect(line(math_worker, "0.5*2*3^2", "9"), "valid", "ok")


# --------------------------------------------------------------------- unknown inputs


ATTACKS = [
    "__import__('os').system('id')",
    "9**9**9",
    "open('x')",
    "x.__class__",
    "x.real",
    "(1).__add__(2)",
    "exec('1')",
    "lambda: 1",
    "[x for x in range(3)]",
    "x; import os",
    "eval('2+2')",
    "9**999999",
]


@pytest.mark.parametrize("kind", [k for k in STEP_KINDS if k != "explanation"])
@pytest.mark.parametrize("attack", ATTACKS)
def test_hostile_text_is_unknown_and_never_valid(math_worker, kind, attack):
    r = step(math_worker, QUAD, kind, attack)
    assert r.verdict == "unknown" and r.reason_code in ("ambiguous_input", "unsupported"), r


def test_hostile_problem_text_is_unknown_too(math_worker):
    for attack in ATTACKS:
        r = step(math_worker, attack, "equivalent_equation", "x = 1")
        assert r.verdict == "unknown", (attack, r)


def test_attack_is_never_executed(math_worker, tmp_path):
    marker = tmp_path / "pwned"
    payload = f"__import__('os').system('touch {marker}')"
    for kind in ("candidate_root", "equivalent_equation", "factorization", "numeric_result", "solution_set"):
        step(math_worker, QUAD, kind, payload)
        step(math_worker, payload, kind, "x = 1")
    assert not marker.exists()


def test_student_line_with_an_attack_is_not_valid(math_worker):
    for attack in ATTACKS:
        assert line(math_worker, QUAD, attack).verdict == "unknown"


@pytest.mark.parametrize(
    "text",
    ["", "   ", "(x+", "x = = 2", "x ≠ 2", "x < 3", "x >= 1", "2 +* 3", "x = ", "= =", "(x + 2)(x + ?)", "x² + 5x + □ = 0", "±2"],
)
def test_malformed_input_is_unknown(math_worker, text):
    for kind in ("equivalent_equation", "candidate_root", "solution_set", "factorization", "numeric_result"):
        r = step(math_worker, QUAD, kind, text)
        assert r.verdict == "unknown" and r.reason_code in ("ambiguous_input", "unsupported"), (kind, text, r)


def test_ambiguous_handwriting_is_ambiguous_input(math_worker):
    r = line(math_worker, QUAD, "(x + 2)(x + ?)")
    expect(r, "unknown", "ambiguous_input")


def test_a_different_variable_is_ambiguous(math_worker):
    expect(step(math_worker, QUAD, "candidate_root", "y = 3"), "unknown", "ambiguous_input")
    expect(step(math_worker, QUAD, "equivalent_equation", "y^2 + 5y + 6 = 0"), "unknown", "ambiguous_input")


def test_too_long_input_is_ambiguous(math_worker):
    expect(step(math_worker, QUAD, "equivalent_equation", "x+" * 400 + "1 = 0"), "unknown", "ambiguous_input")


def test_multi_variable_problems_are_unsupported(math_worker):
    expect(step(math_worker, "x + y = 3", "candidate_root", "x = 1"), "unknown", "unsupported")


def test_expression_problem_cannot_check_a_root(math_worker):
    expect(step(math_worker, "x^2 + 5x + 6", "candidate_root", "x = -2"), "unknown", "unsupported")


def test_equation_vs_expression_mismatch_is_unsupported(math_worker):
    expect(step(math_worker, QUAD, "expression_identity", "x^2 + 5x + 6 = 0", "x^2+5x+6"), "unknown", "unsupported")


def test_numberless_equation_is_unsupported(math_worker):
    expect(step(math_worker, QUAD, "equivalent_equation", "2 = 2"), "unknown", "unsupported")


def test_explanation_and_unknown_kinds(math_worker):
    expect(step(math_worker, QUAD, "explanation", "because"), "unknown", "unsupported", "explanation")
    expect(step(math_worker, QUAD, "made_up", "x = 1"), "unknown", "unsupported")
    expect(line(math_worker, QUAD, "therefore the roots are nice"), "unknown", "unsupported", "explanation")


# ------------------------------------------------------------------ timeouts and failures


class _Sleepy(MathWorker):
    """A real worker whose every call hangs, so the real kill timeout fires."""

    def call(self, op, **kwargs):
        return super().call("_sleep", secs=30)


def test_a_hang_is_killed_and_reported_as_a_timeout():
    w = _Sleepy(timeout=0.4)
    try:
        r = check_math_step(w, QUAD, "equivalent_equation", "x = 1")
    finally:
        w.close()
    expect(r, "unknown", "timeout", "equivalent_equation")
    assert r.normalized is None


class _Fake:
    def __init__(self, exc):
        self.exc = exc

    def call(self, op, **kwargs):
        raise self.exc


def test_timeout_and_worker_errors_never_become_valid():
    expect(check_math_step(_Fake(ToolTimeout("t")), QUAD, "candidate_root", "x=-2"), "unknown", "timeout")
    expect(check_math_step(_Fake(ToolError("boom")), QUAD, "candidate_root", "x=-2"), "unknown", "unsupported")


def test_worker_recovers_after_a_timeout():
    w = _Sleepy(timeout=0.3)
    try:
        check_math_step(w, QUAD, "candidate_root", "x = -2")
        # a fresh call on the same object starts a new child; swap in the real op to prove it
        w.__class__ = MathWorker
        expect(check_math_step(w, QUAD, "candidate_root", "x = -2"), "partial", "partial_root")
    finally:
        w.close()


# ------------------------------------------------------------------------ misc contract


def test_notes_never_contain_the_answer(math_worker):
    for text in ("x = -2", "x = 2", "x = 7", "(x-2)(x-3)=0", "{-2}", "x=-2 or x=5"):
        r = line(math_worker, QUAD, text)
        assert "-3" not in r.note and "-2" not in r.note.replace(text, ""), (text, r.note)


def test_step_check_is_a_frozen_value_with_a_dict_form():
    r = StepCheck("valid", "ok", "candidate_root", "x=1", "n")
    assert r.to_dict() == {"verdict": "valid", "reason_code": "ok", "step_kind": "candidate_root", "normalized": "x=1", "note": "n"}
    with pytest.raises(Exception):
        r.verdict = "invalid"
    assert r.is_valid


def test_vocabularies_match_the_ipad_contract():
    assert set(VERDICTS) == {"valid", "invalid", "partial", "unknown"}
    assert {"ok", "sign_mismatch", "partial_root", "extraneous_root", "domain_changed", "ambiguous_input", "unsupported", "timeout"} <= set(REASONS)
    assert set(STEP_KINDS) == {
        "equivalent_equation", "factorization", "expression_identity", "candidate_root",
        "solution_set", "numeric_result", "explanation",
    }


def test_every_result_uses_known_vocabulary(math_worker):
    lines = ["(x+2)(x+3)=0", "x=-2", "x=2", "{-2,-3}", "(x+", "foo bar", "x<3", "x=7", "x^2=3x"]
    for text in lines:
        r = line(math_worker, QUAD, text)
        assert r.verdict in VERDICTS and r.reason_code in REASONS and r.step_kind in STEP_KINDS


def test_valid_is_never_returned_for_unknown_reasons(math_worker):
    for text in ("(x+", "x = ", "y = 3", "x<3"):
        r = line(math_worker, QUAD, text)
        assert r.verdict != "valid"


@pytest.mark.anyio
async def test_async_wrapper(math_worker):
    r = await acheck_math_step(math_worker, QUAD, "candidate_root", "x = -2")
    expect(r, "partial", "partial_root")
