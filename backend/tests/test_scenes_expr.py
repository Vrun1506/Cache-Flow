import json
import math
from pathlib import Path

import pytest

from tutor.scenes.expr import dry_run, evaluate, normalise

VECTORS = Path(__file__).resolve().parents[2] / "contracts" / "fixtures" / "expr_vectors.json"


def test_vectors_file_exists_and_is_nonempty():
    assert len(json.loads(VECTORS.read_text())) >= 40


@pytest.mark.parametrize("v", json.loads(VECTORS.read_text()), ids=lambda v: f"{v['expr']}@{v['x']}")
def test_evaluator_matches_shared_vectors(v):
    got = evaluate(v["expr"], x=v["x"], t=v.get("t", 0.0))
    if v["expected"] is None:
        assert got is None
    else:
        assert got is not None and abs(got - v["expected"]) <= 1e-6 * max(1.0, abs(v["expected"]))


@pytest.mark.parametrize(
    "text,out",
    [
        ("x^2-5x+6", "x^2 - 5*x + 6"),
        ("e^x", "exp(x)"),
        ("2x", "2*x"),
        ("sqrt(x)", "sqrt(x)"),
        ("x**2", "x^2"),
        ("ln(x)", "ln(x)"),
        ("3/4*x", "3*x/4"),
        ("1/x^2", "1/x^2"),
    ],
)
def test_normalise_uses_explicit_wire_grammar(text, out):
    assert normalise(text) == out


@pytest.mark.parametrize(
    "text",
    [
        "x^2 + 3*x - 7", "sin(2*x)+cos(x)^2", "1/(x+1)", "-(x+1)*3", "abs(x-2)/x", "2^-x", "x/(2*t)",
        "0.0000125*x^2", "x^(1/3)", "pi*x^2/2", "sqrt(x^2+1)", "x^3 - 2*x",
    ],
)
def test_normalise_is_value_preserving(text):
    from sympy.parsing.sympy_parser import (
        convert_xor,
        implicit_multiplication_application,
        parse_expr,
        standard_transformations,
    )

    out = normalise(text)
    expr = parse_expr(text, transformations=standard_transformations + (implicit_multiplication_application, convert_xor))
    for x in (-1.7, 0.5, 2.0, 3.3):
        want = complex(expr.subs({"x": x, "t": 1.0}))
        got = evaluate(out, x=x, t=1.0)
        if abs(want.imag) > 1e-12 or want.real != want.real or abs(want.real) == math.inf:
            assert got is None  # complex or undefined in the reals: a gap
        else:
            assert got == pytest.approx(want.real, rel=1e-6, abs=1e-9)


def test_normalise_output_stays_in_the_grammar():
    # No `**`, no implicit multiplication, no uppercase function names.
    out = normalise("2x**2 + Abs(x)")
    assert "**" not in out and "Abs" not in out and "2*x^2" in out


@pytest.mark.parametrize(
    "text",
    [
        "", "   ", "y + 1", "x = 2", "__import__('os')", "open('f')", "x.real", "9**9**9", "1/0",
        "x" * 300, "sin(", "foo(x)", "x < 2", "z^2", "I*x",
    ],
)
def test_normalise_rejects_bad_input(text):
    with pytest.raises(ValueError):
        normalise(text)


def test_normalise_rejects_non_strings():
    with pytest.raises(ValueError):
        normalise(None)  # type: ignore[arg-type]


def test_normalise_enforces_length_limit():
    with pytest.raises(ValueError):
        normalise("+".join(f"{i}*x" for i in range(2, 60)) + "+x^2")


def test_evaluator_domain_errors_are_gaps():
    assert evaluate("sqrt(x)", x=-1) is None
    assert evaluate("1/x", x=0) is None
    assert evaluate("ln(x)", x=0) is None
    assert evaluate("x^0.5", x=-4) is None
    assert evaluate("exp(x)", x=10000) is None


def test_evaluator_precedence_and_associativity():
    assert evaluate("2^3^2") == 512
    assert evaluate("-2^2") == -4
    assert evaluate("2^-1") == 0.5
    assert evaluate("8/2/2") == 2
    assert evaluate("2*3+4") == 10
    assert evaluate("min(3, 1, 2)") == 1


def test_evaluator_rejects_unknown_syntax_without_eval():
    for bad in ("__import__('os')", "x.y", "y", "sin", "2 3", "(1", "min(1)", "sin(1,2)"):
        with pytest.raises(ValueError):
            evaluate(bad)


def test_dry_run_fraction():
    assert dry_run("x^2", (-5, 5)) == 1.0
    assert dry_run("sqrt(x)", (-10, 10)) == pytest.approx(0.5, abs=0.05)
    assert dry_run("ln(x)", (-5, -1)) == 0.0
    assert dry_run("garbage(", (0, 1)) == 0.0
