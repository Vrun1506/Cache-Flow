"""Maths verification (final-plan.md §16.3). Runs inside the worker process.

Every function returns a plain dict ``{status, method, note}``. ``verified``
only ever comes from a proof or an exact comparison here, never from an LLM.
"""

from __future__ import annotations

import random
from typing import Optional

import sympy
from sympy import Eq, FiniteSet, Rational, S, Symbol, simplify, solveset
from sympy.logic.boolalg import BooleanAtom

from .exprparse import UnsafeExpression, parse_statement, split_alternatives

_TOL = 1e-9


def _res(status: str, note: str = "") -> dict:
    return {"status": status, "method": "sympy", "note": note[:160]}


def _is_eq(x) -> bool:
    return isinstance(x, Eq)


def _symbols(*things) -> list[Symbol]:
    out: set[Symbol] = set()
    for t in things:
        if isinstance(t, Eq):
            out |= t.lhs.free_symbols | t.rhs.free_symbols
        elif isinstance(t, sympy.Basic) and not isinstance(t, BooleanAtom):
            out |= t.free_symbols
    return sorted(out, key=str)


def _to_complex(expr) -> Optional[complex]:
    try:
        value = complex(sympy.N(expr))
    except Exception:
        return None
    if value != value or abs(value) == float("inf"):
        return None
    return value


def _close(a: complex, b: complex) -> bool:
    return abs(a - b) <= _TOL * (1 + abs(a))


def _numeric_compare(a, b, syms: list[Symbol]):
    """Return (valid_points, mismatch_description_or_None)."""
    rng = random.Random(42)
    needed = 1 if not syms else 3
    valid = 0
    for _ in range(20):
        point = {s: Rational(rng.randint(-30, 30), rng.randint(1, 10)) for s in syms}
        va, vb = _to_complex(a.subs(point)), _to_complex(b.subs(point))
        if va is None or vb is None:
            continue
        valid += 1
        if not _close(va, vb):
            where = ", ".join(f"{s}={p}" for s, p in point.items()) or "constants"
            return valid, f"differ at {where}: {_fmt(va)} vs {_fmt(vb)}"
        if valid >= needed and not syms:
            break
        if valid >= max(needed, 5):
            break
    return valid, None


def _fmt(v: complex) -> str:
    return f"{v.real:.6g}" if abs(v.imag) < _TOL else f"{v:.6g}"


def _sets_equal(a, b) -> Optional[bool]:
    """True / False when decidable, None otherwise."""
    if isinstance(a, FiniteSet) and isinstance(b, FiniteSet):
        if len(a) != len(b):
            return False
        va = sorted((_to_complex(x) for x in a), key=lambda c: (c.real, c.imag) if c is not None else (0, 0))
        vb = sorted((_to_complex(x) for x in b), key=lambda c: (c.real, c.imag) if c is not None else (0, 0))
        if any(v is None for v in va + vb):
            return True if a == b else None
        return all(_close(x, y) for x, y in zip(va, vb))
    if a == b:
        return True
    return None


def _solution_set(eqs: list[Eq], s: Symbol):
    result = S.EmptySet
    for eq in eqs:
        result = result.union(solveset(eq, s, S.Reals))
    return result


def _rel_expr(a, b) -> dict:
    syms = _symbols(a, b)
    zero = simplify(a - b) == 0
    valid, mismatch = _numeric_compare(a, b, syms)
    needed = 1 if not syms else 3
    if mismatch:
        return _res("failed", mismatch)
    if zero:
        return _res("verified", "equivalent" if valid >= needed else "equivalent (symbolic only)")
    if valid >= needed:
        return _res("unverified", "could not prove equal; numeric checks agree")
    return _res("unverified", "could not evaluate")


def _rel_multi(a: Eq, b: Eq) -> dict:
    """Several symbols: check ``sym = rhs`` against the other equation by substitution."""
    for src, dst in ((b, a), (a, b)):
        for sym, rhs in ((src.lhs, src.rhs), (src.rhs, src.lhs)):
            if isinstance(sym, Symbol) and sym not in rhs.free_symbols and sym in (dst.lhs.free_symbols | dst.rhs.free_symbols):
                left, right = dst.lhs.subs(sym, rhs), dst.rhs.subs(sym, rhs)
                if simplify(left - right) == 0:
                    return _res("verified", f"consistent after substituting {sym}")
                syms = _symbols(left, right)
                valid, mismatch = _numeric_compare(left, right, syms)
                if mismatch:
                    return _res("failed", f"not consistent: {mismatch}")
                return _res("unverified", "could not prove consistent")
    return _res("unverified", "could not relate equations with several symbols")


def relate(prev: str, cur: str) -> dict:
    try:
        A = [parse_statement(p) for p in split_alternatives(prev)]
        B = [parse_statement(p) for p in split_alternatives(cur)]
    except UnsafeExpression as exc:
        return _res("unverified", f"could not parse: {exc}")
    except Exception as exc:
        return _res("unverified", f"could not parse ({type(exc).__name__})")
    if not A or not B:
        return _res("unverified", "empty statement")

    if len(B) == 1 and isinstance(B[0], BooleanAtom):
        if B[0] == sympy.true:
            return _res("verified", "arithmetic correct")
        return _res("failed", "statement is false")
    if any(isinstance(x, BooleanAtom) for x in A + B):
        return _res("unverified", "arithmetic-only statement")

    all_eq = all(_is_eq(x) for x in A + B)
    none_eq = not any(_is_eq(x) for x in A + B)
    if not all_eq and not none_eq:
        return _res("unverified", "cannot relate an equation to an expression")

    if none_eq:
        if len(A) != 1 or len(B) != 1:
            return _res("unverified", "lists of expressions are not supported")
        return _rel_expr(A[0], B[0])

    syms = _symbols(*A, *B)
    if len(syms) == 1:
        s = syms[0]
        sa, sb = _solution_set(A, s), _solution_set(B, s)
        same = _sets_equal(sa, sb)
        if same:
            return _res("verified", f"same solutions {sb}")
        if same is False and isinstance(sa, FiniteSet) and isinstance(sb, FiniteSet):
            return _res("failed", f"solutions differ: {sa} vs {sb}")
        return _res("unverified", "could not compare solution sets")
    if len(A) == 1 and len(B) == 1:
        return _rel_multi(A[0], B[0])
    return _res("unverified", "alternatives over several symbols are not supported")


def check_final(equation: str, answer: str) -> dict:
    try:
        eq = parse_statement(equation)
        if not _is_eq(eq):
            return _res("unverified", "the original statement is not an equation")
        syms = _symbols(eq)
        if len(syms) != 1:
            return _res("unverified", "need exactly one symbol" if not syms else "more than one symbol")
        s = syms[0]
        values = []
        for alt in split_alternatives(answer):
            st = parse_statement(alt)
            if _is_eq(st):
                if st.lhs == s and s not in st.rhs.free_symbols:
                    values.append(st.rhs)
                elif st.rhs == s and s not in st.lhs.free_symbols:
                    values.append(st.lhs)
                else:
                    return _res("unverified", "unrecognised answer format")
            elif isinstance(st, sympy.Basic) and not isinstance(st, BooleanAtom) and not st.free_symbols:
                values.append(st)
            else:
                return _res("unverified", "unrecognised answer format")
    except UnsafeExpression as exc:
        return _res("unverified", f"could not parse: {exc}")
    except Exception as exc:
        return _res("unverified", f"could not parse ({type(exc).__name__})")
    if not values:
        return _res("unverified", "no answer to check")

    for v in values:
        diff = simplify(eq.lhs.subs(s, v) - eq.rhs.subs(s, v))
        if diff != 0:
            num = _to_complex(diff)
            if num is None or abs(num) > _TOL:
                return _res("failed", f"{s}={v} does not satisfy the equation")

    sol = solveset(eq, s, S.Reals)
    if not isinstance(sol, FiniteSet):
        return _res("unverified", "could not prove the answer complete")
    answer_set = FiniteSet(*values)
    if _sets_equal(answer_set, sol):
        return _res("verified", "all roots found")
    if len(answer_set) < len(sol):
        return _res("unverified", f"missing roots: expected {sol}")
    return _res("unverified", "could not compare with the full solution set")
