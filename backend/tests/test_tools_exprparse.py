import pytest
import sympy

from tutor.tools import UnsafeExpression, check_expr_safe, parse_statement
from tutor.tools.exprparse import normalize, split_alternatives


def test_normalize_unicode_and_strip():
    assert normalize("  x² − 3×y ÷ 2 · z ") == "x**2 - 3*y / 2 * z"
    assert normalize("x³") == "x**3"


@pytest.mark.parametrize(
    "bad",
    ["a" * 201, "x\ny", "x;y", "[1]", "{1}", "'a'", '"a"', "x\\y", "x.__class__"],
)
def test_normalize_rejects(bad):
    with pytest.raises(UnsafeExpression):
        normalize(bad)


@pytest.mark.parametrize(
    "text",
    ["2x+3", "x^2+5x+6", "sin(x)/x", "(x+2)(x+3)", "sqrt(2)*pi", "2.5*x - 1", "x**2", "3(x+1)", "ln(x)+Abs(x)", "-x"],
)
def test_safe_expressions_accepted(text):
    code = check_expr_safe(text)
    assert isinstance(code, str) and code


@pytest.mark.parametrize(
    "text",
    [
        "__import__('os').system('x')",
        "open('f')",
        "x.__class__",
        "().__class__",
        "lambda: 1",
        "9**9**9",
        "x**100000",
        "factorial(100)",
        "[1,2]",
        "a" * 300,
        "10**9999",
        "eval(x)",
        "exec(x)",
        "x.real",
        "123456789012345678",
        "Symbol(x)",
        "x**1001",
        "x = 1",
        "",
    ],
)
def test_hostile_input_rejected_without_evaluation(text):
    with pytest.raises(UnsafeExpression):
        check_expr_safe(text)


def test_check_is_pure_and_does_not_evaluate():
    # Would hang if evaluated; the check must reject or accept instantly.
    with pytest.raises(UnsafeExpression):
        check_expr_safe("9**9**9**9")
    assert check_expr_safe("x**1000")


def test_node_limit():
    with pytest.raises(UnsafeExpression):
        check_expr_safe("+".join(["x"] * 150))


def test_parse_statement_expression_and_equation():
    x = sympy.Symbol("x")
    assert parse_statement("x^2+5x+6") == x**2 + 5 * x + 6
    eq = parse_statement("2x+3=11")
    assert eq == sympy.Eq(2 * x + 3, 11)
    assert parse_statement("2*3=6") == sympy.true
    assert parse_statement("2*3=7") == sympy.false


@pytest.mark.parametrize("bad", ["x==1", "x<1", "x>=1", "x=1=2", "x!=2", "x!"])
def test_parse_statement_unsupported(bad):
    with pytest.raises(UnsafeExpression):
        parse_statement(bad)


def test_parse_statement_runs_with_empty_builtins():
    with pytest.raises(UnsafeExpression):
        parse_statement("__import__('os')")


def test_split_alternatives():
    assert split_alternatives("x=-2 or x=-3") == ["x=-2", "x=-3"]
    assert split_alternatives("x = -2, x = -3") == ["x = -2", "x = -3"]
    assert split_alternatives("x=1 and x=2") == ["x=1", "x=2"]
    assert split_alternatives("max(1,2)=2, x=3") == ["max(1,2)=2", "x=3"]
    assert split_alternatives("x=1 ∨ x=2") == ["x=1", "x=2"]
    assert split_alternatives("  ") == []
