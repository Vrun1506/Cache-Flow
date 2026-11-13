"""The shared expression grammar for plots and scenes (final-plan.md §16.7).

Wire format (what the iPad evaluates): numbers, variables ``x`` and ``t``,
operators ``+ - * / ^`` and parentheses, functions
``sin cos tan asin acos atan exp ln log sqrt abs floor ceil min max`` and
constants ``pi`` and ``e``. Every multiplication is explicit. ``log`` is the
natural log, the same as ``ln``. Unary minus binds looser than ``^``, so
``-x^2`` is ``-(x^2)``, and ``^`` is right associative (``2^3^2 = 512``).
Limits: 120 characters, parenthesis depth 8. A domain error is a gap (None).

``normalise`` turns model-written text into that format through SymPy, using
the safe parser from ``tutor.tools.exprparse``. ``evaluate`` is the reference
evaluator the Swift one must match (``contracts/fixtures/expr_vectors.json``).
It never uses ``eval``.
"""

from __future__ import annotations

import math
import re
from decimal import Decimal
from functools import lru_cache

import sympy

from ..tools.exprparse import parse_statement

MAX_LEN = 120
MAX_DEPTH = 8
_VARS = ("x", "t")

# precedence of a printed piece
_ADD, _MUL, _NEG, _POW, _ATOM = 1, 2, 3, 4, 5


def _float_str(f: float) -> str:
    if f == int(f) and abs(f) < 1e15:
        return str(int(abs(f)))
    return format(Decimal(repr(abs(f))), "f")


def _wrap(piece: tuple[str, int], min_prec: int) -> str:
    text, prec = piece
    return text if prec >= min_prec else f"({text})"


def _print(e: sympy.Basic) -> tuple[str, int]:
    if e.is_Integer:
        v = int(e)
        return (str(v), _ATOM if v >= 0 else _NEG)
    if e.is_Rational:
        s = f"{abs(e.p)}/{e.q}"
        return (("-" + s, _NEG) if e.p < 0 else (s, _MUL))
    if e.is_Float:
        f = float(e)
        s = _float_str(f)
        return (("-" + s, _NEG) if f < 0 else (s, _ATOM))
    if e is sympy.pi:
        return ("pi", _ATOM)
    if e is sympy.E:
        return ("e", _ATOM)
    if e.is_Symbol:
        if e.name not in _VARS:
            raise ValueError(f"unknown variable {e.name!r}")
        return (e.name, _ATOM)
    if e.is_Add:
        parts = [_print(a) for a in e.as_ordered_terms()]
        out = parts[0][0]
        for text, _ in parts[1:]:
            out += f" - {text[1:]}" if text.startswith("-") else f" + {text}"
        return (out, _ADD)
    if e.is_Mul:
        return _print_mul(e)
    if e.is_Pow:
        return _print_pow(e.base, e.exp)
    if isinstance(e, sympy.exp):
        return (f"exp({_print(e.args[0])[0]})", _ATOM)
    name = _FUNCS.get(type(e))
    if name is not None and e.args:
        if isinstance(e, sympy.log) and len(e.args) != 1:
            raise ValueError("log with a base is not supported")
        return (f"{name}({', '.join(_print(a)[0] for a in e.args)})", _ATOM)
    raise ValueError(f"unsupported term {type(e).__name__}")


_FUNCS = {
    sympy.sin: "sin", sympy.cos: "cos", sympy.tan: "tan",
    sympy.asin: "asin", sympy.acos: "acos", sympy.atan: "atan",
    sympy.log: "ln", sympy.Abs: "abs", sympy.floor: "floor", sympy.ceiling: "ceil",
    sympy.Min: "min", sympy.Max: "max",
}


def _positive_power(base: sympy.Basic, exp: sympy.Basic) -> tuple[str, int]:
    if exp == 1:
        return _print(base)
    if exp == sympy.Rational(1, 2):
        return (f"sqrt({_print(base)[0]})", _ATOM)
    exp_piece = _print(exp)
    return (f"{_wrap(_print(base), _ATOM)}^{_wrap(exp_piece, _ATOM)}", _POW)


def _print_pow(base: sympy.Basic, exp: sympy.Basic) -> tuple[str, int]:
    if base is sympy.E:
        return (f"exp({_print(exp)[0]})", _ATOM)
    if exp.is_Number and exp.is_negative:
        den = _positive_power(base, -exp)
        return (f"1/{_wrap(den, _POW)}", _MUL)
    return _positive_power(base, exp)


def _print_mul(e: sympy.Basic) -> tuple[str, int]:
    coeff, rest = e.as_coeff_Mul()
    negative = coeff < 0
    coeff = -coeff if negative else coeff
    num: list[str] = []
    den: list[str] = []
    if coeff.is_Rational:
        if coeff.p != 1:
            num.append(str(coeff.p))
        if coeff.q != 1:
            den.append(str(coeff.q))
    elif coeff != 1:
        num.append(_float_str(float(coeff)))
    for f in sympy.Mul.make_args(rest):
        if f.is_Pow and f.base is not sympy.E and f.exp.is_Number and f.exp.is_negative:
            den.append(_wrap(_positive_power(f.base, -f.exp), _POW))
        else:
            num.append(_wrap(_print(f), _MUL))
    text = "*".join(num) if num else "1"
    if den:
        text += "/" + (den[0] if len(den) == 1 else "(" + "*".join(den) + ")")
    if negative:
        return ("-" + text, _NEG)
    return (text, _MUL)


def _depth(text: str) -> int:
    depth = best = 0
    for ch in text:
        if ch == "(":
            depth += 1
            best = max(best, depth)
        elif ch == ")":
            depth -= 1
    return best


def normalise(expr: str) -> str:
    """Re-print ``expr`` in the wire grammar with explicit ``*``. Raises ValueError."""
    if not isinstance(expr, str) or not expr.strip():
        raise ValueError("empty expression")
    text = re.sub(r"\be\b", "E", expr.strip())  # lowercase e is Euler's number here
    try:
        value = parse_statement(text)
    except ValueError:
        raise
    except Exception as exc:  # sympy raises many things on odd input
        raise ValueError(f"could not parse ({type(exc).__name__})") from None
    if not isinstance(value, sympy.Expr):
        raise ValueError("expected an expression, not an equation")
    if value.has(sympy.I, sympy.zoo, sympy.oo, sympy.nan, -sympy.oo):
        raise ValueError("not a real, finite expression")
    out = _print(value)[0]
    if len(out) > MAX_LEN:
        raise ValueError(f"longer than {MAX_LEN} characters")
    if _depth(out) > MAX_DEPTH:
        raise ValueError("nested too deeply")
    return out


# ---------------------------------------------------------------- evaluator

_TOKEN = re.compile(r"\s*(?:(\d+\.?\d*|\.\d+)|([A-Za-z_][A-Za-z_0-9]*)|(.))")
_ONE_ARG = {
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "exp": math.exp, "ln": math.log, "log": math.log, "sqrt": math.sqrt,
    "abs": abs, "floor": math.floor, "ceil": math.ceil,
}
_CONSTS = {"pi": math.pi, "e": math.e}
_MAX_EVAL_LEN = 200
_MAX_PARSE_DEPTH = 24


class _Domain(Exception):
    pass


class _Parser:
    def __init__(self, text: str):
        self.tokens: list[str] = []
        pos = 0
        text = text.rstrip()
        while pos < len(text):
            m = _TOKEN.match(text, pos)
            if not m:
                break
            self.tokens.append(m.group(1) or m.group(2) or m.group(3))
            pos = m.end()
        self.i = 0
        self.depth = 0

    def peek(self) -> str | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self) -> str:
        tok = self.peek()
        if tok is None:
            raise ValueError("unexpected end of expression")
        self.i += 1
        return tok

    def parse(self):
        node = self.expr()
        if self.peek() is not None:
            raise ValueError(f"unexpected {self.peek()!r}")
        return node

    def expr(self):
        node = self.term()
        while self.peek() in ("+", "-"):
            node = (self.take(), node, self.term())
        return node

    def term(self):
        node = self.unary()
        while self.peek() in ("*", "/"):
            node = (self.take(), node, self.unary())
        return node

    def unary(self):
        if self.peek() in ("-", "+"):
            op = self.take()
            inner = self.unary()
            return ("neg", inner) if op == "-" else inner
        return self.power()

    def power(self):
        base = self.atom()
        if self.peek() == "^":
            self.take()
            return ("^", base, self.exponent())
        return base

    def exponent(self):
        if self.peek() in ("-", "+"):
            op = self.take()
            inner = self.exponent()
            return ("neg", inner) if op == "-" else inner
        return self.power()

    def atom(self):
        self.depth += 1
        if self.depth > _MAX_PARSE_DEPTH:
            raise ValueError("nested too deeply")
        try:
            tok = self.take()
            if tok[0].isdigit() or tok[0] == ".":
                return ("num", float(tok))
            if tok == "(":
                node = self.expr()
                if self.take() != ")":
                    raise ValueError("expected )")
                return node
            if tok in _VARS:
                return ("var", tok)
            if tok in _CONSTS and self.peek() != "(":
                return ("num", _CONSTS[tok])
            if tok in _ONE_ARG or tok in ("min", "max"):
                if self.take() != "(":
                    raise ValueError(f"{tok} needs (")
                args = [self.expr()]
                while self.peek() == ",":
                    self.take()
                    args.append(self.expr())
                if self.take() != ")":
                    raise ValueError("expected )")
                if tok in ("min", "max"):
                    if len(args) < 2:
                        raise ValueError(f"{tok} needs two arguments")
                elif len(args) != 1:
                    raise ValueError(f"{tok} takes one argument")
                return ("fn", tok, args)
            raise ValueError(f"unknown name {tok!r}")
        finally:
            self.depth -= 1


@lru_cache(maxsize=512)
def _compile(expr: str):
    if len(expr) > _MAX_EVAL_LEN:
        raise ValueError("expression too long")
    return _Parser(expr).parse()


def _ev(node, x: float, t: float) -> float:
    kind = node[0]
    if kind == "num":
        return node[1]
    if kind == "var":
        return x if node[1] == "x" else t
    if kind == "neg":
        return -_ev(node[1], x, t)
    if kind == "fn":
        args = [_ev(a, x, t) for a in node[2]]
        if node[1] == "min":
            return min(args)
        if node[1] == "max":
            return max(args)
        return float(_ONE_ARG[node[1]](args[0]))
    a, b = _ev(node[1], x, t), _ev(node[2], x, t)
    if kind == "+":
        return a + b
    if kind == "-":
        return a - b
    if kind == "*":
        return a * b
    if kind == "/":
        if b == 0:
            raise _Domain
        return a / b
    return math.pow(a, b)  # "^": ValueError for a negative base with a fractional exponent


def evaluate(expr: str, x: float = 0.0, t: float = 0.0) -> float | None:
    """Value of ``expr`` at x, t, or None where it is undefined or not finite.

    Raises ValueError if ``expr`` is not valid in the grammar.
    """
    node = _compile(expr)
    try:
        value = _ev(node, float(x), float(t))
    except (_Domain, ValueError, OverflowError, ZeroDivisionError):
        return None
    return value if math.isfinite(value) else None


def dry_run(expr: str, x_range: tuple[float, float], n: int = 50) -> float:
    """Fraction of ``n`` evenly spaced samples (t = x) where ``expr`` is finite."""
    try:
        _compile(expr)
    except ValueError:
        return 0.0
    lo, hi = x_range
    n = max(2, n)
    ok = sum(
        1
        for i in range(n)
        if evaluate(expr, x=(v := lo + (hi - lo) * i / (n - 1)), t=v) is not None
    )
    return ok / n
