import pytest

from tutor.tools import check_final, relate


def status(result: dict) -> str:
    assert set(result) == {"status", "method", "note"}
    assert result["method"] == "sympy"
    assert len(result["note"]) <= 160
    return result["status"]


@pytest.mark.parametrize(
    "prev,cur,expected",
    [
        ("x**2+5*x+6=0", "(x+2)*(x+3)=0", "verified"),
        ("x**2+5*x+6=0", "(x+2)*(x+3)=1", "failed"),
        ("2*x+3=11", "x=4", "verified"),
        ("x=4", "x=5", "failed"),
        ("x**2+5*x+6=0", "x=-2 or x=-3", "verified"),
        ("x**2+5*x+6=0", "x=-2, x=-3", "verified"),
        ("x**2+5*x+6=0", "x=-2", "failed"),
        ("x**2+5*x+6=0", "x=-2 or x=3", "failed"),
        ("(x+1)**2", "x**2+2*x+1", "verified"),
        ("(x+1)**2", "x**2+1", "failed"),
        ("v=u+a*t", "a=(v-u)/t", "verified"),
        ("v=u+a*t", "a=(v+u)/t", "failed"),
        ("2*x=1", "x=0.5", "verified"),
        ("sin(x)**2+cos(x)**2", "1", "verified"),
        ("2*3", "6", "verified"),
        ("x", "x+1", "failed"),
    ],
)
def test_relate(prev, cur, expected):
    assert status(relate(prev, cur)) == expected


def test_relate_arithmetic_collapsed_equations():
    assert relate("x=1", "2*3=6")["note"] == "arithmetic correct"
    assert status(relate("x=1", "2*3=6")) == "verified"
    assert status(relate("x=1", "2*3=7")) == "failed"
    assert status(relate("2*3=6", "x=4")) == "unverified"


def test_relate_mixed_and_garbage_unverified():
    assert status(relate("x+1", "x=4")) == "unverified"
    assert status(relate("x=4", "x+1")) == "unverified"
    assert status(relate("banana split", "x=1")) == "unverified"
    assert status(relate("x=1", "__import__('os')")) == "unverified"
    assert status(relate("", "x=1")) == "unverified"
    assert status(relate("x<1", "x<2")) == "unverified"


def test_relate_counterexample_in_note():
    result = relate("(x+1)**2", "x**2+1")
    assert "differ at x=" in result["note"]


def test_relate_note_is_short_even_for_big_sets():
    result = relate("x**2=1", "x**2=4")
    assert result["status"] == "failed"
    assert len(result["note"]) <= 160


def test_relate_multi_symbol_unverifiable():
    assert status(relate("x+y=1", "x*y=2")) == "unverified"


@pytest.mark.parametrize(
    "equation,answer,expected",
    [
        ("x**2+5*x+6=0", "x=-2 or x=-3", "verified"),
        ("x**2+5*x+6=0", "x = -2, x = -3", "verified"),
        ("x**2+5*x+6=0", "-2, -3", "verified"),
        ("x**2+5*x+6=0", "x=-2", "unverified"),
        ("x**2+5*x+6=0", "x=-2 or x=3", "failed"),
        ("2*x+3=11", "x=4", "verified"),
        ("2*x+3=11", "4", "verified"),
        ("2*x+3=11", "x=5", "failed"),
        ("x+y=3", "x=1", "unverified"),
        ("x**2+1=0", "x=1", "failed"),
        ("2*x+3=11", "the answer is four", "unverified"),
        ("x+1", "x=0", "unverified"),
    ],
)
def test_check_final(equation, answer, expected):
    assert status(check_final(equation, answer)) == expected


def test_check_final_notes():
    assert "missing roots" in check_final("x**2+5*x+6=0", "x=-2")["note"]
    assert check_final("x**2+5*x+6=0", "x=-2 or x=3")["note"] == "x=3 does not satisfy the equation"
    assert "more than one symbol" in check_final("x+y=3", "x=1")["note"]
