"""Step-aware maths checking: the ``check_math_step`` tool (team plan §6).

Given the original problem and one line of student (or model) working, decide
whether that line is ``valid``, ``invalid``, ``partial`` or ``unknown`` and say
why with a reason code. The caller declares the *kind* of step, because only an
``equivalent_equation`` step may be judged by comparing solution sets.

Safety rules (final-plan.md §6, §15.4):

* Untrusted text is only ever parsed by :mod:`tutor.tools.exprparse` (AST
  whitelist, no builtins). Nothing here calls ``parse_expr`` itself.
* All SymPy work runs inside the worker process (``MathWorker``, 2 s kill
  timeout). The parent only normalises text and maps failures.
* Anything malformed, ambiguous, unsupported or timed out is ``unknown`` and
  can never be ``valid``. Numeric sampling may find a counterexample but never
  establishes an exact identity.
* ``note`` is for the tutor and the logs. It never contains the answer, so it
  can be shown to the student without leaking it.

``normalized`` is an ASCII, space-free rendering with explicit ``*``:
``(x-2)*(x-3)=0``, ``x**2+5*x+6=0``, ``x=-2``, ``{-3,-2}``.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import asdict, dataclass
from typing import Optional

import sympy
from sympy import Eq, FiniteSet, S, Symbol, simplify, solveset
from sympy.logic.boolalg import BooleanAtom

from .exprparse import UnsafeExpression, parse_statement, split_alternatives
from .worker import MathWorker, ToolError, ToolTimeout

VERSION = "mathcheck-1"

STEP_KINDS = (
    "equivalent_equation",
    "factorization",
    "expression_identity",
    "candidate_root",
    "solution_set",
    "numeric_result",
    "explanation",
)
VERDICTS = ("valid", "invalid", "partial", "unknown")
# `not_equivalent` is an additive code for a plainly wrong step. The iPad treats reason codes as an open vocabulary.
REASONS = (
    "ok", "sign_mismatch", "partial_root", "extraneous_root", "domain_changed",
    "ambiguous_input", "unsupported", "timeout", "not_equivalent",
)

MAX_TEXT = 600
_TOL = 1e-9


@dataclass(frozen=True)
class StepCheck:
    verdict: str
    reason_code: str
    step_kind: str
    normalized: Optional[str] = None
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def is_valid(self) -> bool:
        return self.verdict == "valid"


def _unknown(kind: str, reason: str, note: str) -> StepCheck:
    return StepCheck("unknown", reason, kind, None, note[:160])


# --------------------------------------------------------------------- text normalisation

_SUPERSCRIPTS = {"²": "**2", "³": "**3", "⁴": "**4", "⁵": "**5", "⁶": "**6"}
_FUNCS = (
    "sin", "cos", "tan", "asin", "acos", "atan", "exp", "log", "ln", "sqrt", "abs", "Abs",
    "floor", "ceil", "ceiling", "min", "max", "Min", "Max", "pi", "E", "or", "and",
)


def normalise_student_text(text: str) -> str:
    """Turn student-style maths into text the safe parser accepts.

    Handles unicode minus/times/division, superscripts, ``√``, set braces,
    ``;`` separators, ``OR``/``AND`` and a leading ``=``. It does not decide
    whether the result is safe; the parser does that.
    """
    if not isinstance(text, str):
        return ""
    t = text.strip()
    for a, b in (("−", "-"), ("–", "-"), ("—", "-"), ("×", "*"), ("·", "*"), ("⋅", "*"), ("÷", "/"), ("＝", "=")):
        t = t.replace(a, b)
    for a, b in _SUPERSCRIPTS.items():
        t = t.replace(a, b)
    t = re.sub(r"√\s*\(", "sqrt(", t)
    t = re.sub(r"√\s*([0-9]+(?:\.[0-9]+)?|[A-Za-z])", r"sqrt(\1)", t)
    t = t.replace("∨", ",").replace(";", ",")
    t = re.sub(r"\s+(?i:or|and)\s+", lambda m: f" {m.group(0).strip().lower()} ", t)
    t = t.replace("{", "").replace("}", "")
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"^=\s*", "", t)  # "= 14" is a bare result
    return t.strip(" ,")


def _has_word(text: str) -> bool:
    """Prose such as 'therefore' is not maths: any 3+ letter word that is not a known function."""
    return any(len(w) >= 3 and w not in _FUNCS for w in re.findall(r"[A-Za-z]+", text))


def _strip_functions(text: str) -> str:
    return re.sub(r"\b(?:sin|cos|tan|asin|acos|atan|exp|log|ln|sqrt|abs|Abs|floor|ceil|ceiling|min|max|Min|Max)\s*\(", "@(", text)


def _has_variable(text: str) -> bool:
    cleaned = re.sub(r"\b(?:pi|E)\b", "", _strip_functions(text))
    return bool(re.search(r"[A-Za-z]", cleaned))


def _is_assignment_or_value(part: str) -> bool:
    """``x = -2``, ``-2 = x`` or a bare number-ish value (no variable)."""
    p = part.strip()
    m = re.fullmatch(r"([A-Za-z])\s*=\s*(.+)", p)
    if m and not _has_variable(m.group(2)):
        return True
    m = re.fullmatch(r"(.+?)\s*=\s*([A-Za-z])", p)
    if m and not _has_variable(m.group(1)):
        return True
    return "=" not in p and not _has_variable(p) and bool(p)


def _is_product_form(part: str) -> bool:
    s = _strip_functions(part)
    return bool(re.search(r"\)\s*\*?\s*\(", s) or re.search(r"[A-Za-z0-9]\s*\*?\s*\(", s))


def infer_step_kind(problem: str, step: str, previous: Optional[str] = None) -> str:
    """Deterministic step-kind guess from the text alone (nothing is evaluated)."""
    t = normalise_student_text(step)
    if not t or (_has_word(t) and not re.search(r"[0-9=+*/^]", t)):
        return "explanation"
    if _has_word(t):
        return "explanation"
    prob = normalise_student_text(problem)
    prob_is_eq = "=" in prob
    prob_has_var = _has_variable(prob)
    parts = split_alternatives(t)
    if len(parts) > 1:
        if all(_is_assignment_or_value(p) for p in parts):
            return "solution_set"
        return "equivalent_equation"
    p = parts[0] if parts else t
    if "=" in p:
        if _is_assignment_or_value(p) and prob_has_var and prob_is_eq:
            return "candidate_root"
        if not prob_has_var:
            return "numeric_result"
        if _is_product_form(p):
            return "factorization"
        return "equivalent_equation"
    if not _has_variable(p):
        return "candidate_root" if (prob_has_var and prob_is_eq) else "numeric_result"
    if _is_product_form(p) or "(" in p:
        return "factorization"
    return "expression_identity"


# ----------------------------------------------------------------------- parent-side API


def _call(worker: MathWorker, kind: str, **kwargs) -> StepCheck:
    try:
        raw = worker.call("check_step", step_kind=kind, **kwargs)
    except ToolTimeout:
        return _unknown(kind, "timeout", "the check took too long and was stopped")
    except ToolError as exc:
        return _unknown(kind, "unsupported", f"the checker failed: {str(exc)[:100]}")
    return StepCheck(
        verdict=raw["verdict"], reason_code=raw["reason_code"], step_kind=raw["step_kind"],
        normalized=raw.get("normalized"), note=raw.get("note", ""),
    )


def check_math_step(
    worker: MathWorker,
    problem: str,
    step_kind: str,
    step: str,
    previous: Optional[str] = None,
    *,
    rel_tol: float = 1e-6,
) -> StepCheck:
    """Check one step of working against the original ``problem``.

    Never raises for bad input: it returns ``unknown`` with ``ambiguous_input``,
    ``unsupported`` or ``timeout``.
    """
    if step_kind not in STEP_KINDS:
        return _unknown("explanation", "unsupported", f"unknown step kind '{str(step_kind)[:30]}'")
    if step_kind == "explanation":
        return _unknown(step_kind, "unsupported", "an explanation cannot be checked by the maths tool")
    p, s = normalise_student_text(problem), normalise_student_text(step)
    prev = normalise_student_text(previous) if previous else None
    if not s:
        return _unknown(step_kind, "ambiguous_input", "nothing to check")
    if not p:
        return _unknown(step_kind, "unsupported", "no problem to check against")
    if max(len(p), len(s), len(prev or "")) > MAX_TEXT:
        return _unknown(step_kind, "ambiguous_input", "that line is too long to check")
    return _call(worker, step_kind, problem=p, step=s, previous=prev, rel_tol=float(rel_tol))


def check_student_line(
    worker: MathWorker, problem: str, line: str, previous: Optional[str] = None, *, rel_tol: float = 1e-6
) -> StepCheck:
    """Infer the step kind from the line, then check it."""
    return check_math_step(worker, problem, infer_step_kind(problem, line, previous), line, previous, rel_tol=rel_tol)


async def acheck_math_step(worker: MathWorker, *args, **kwargs) -> StepCheck:
    return await asyncio.to_thread(check_math_step, worker, *args, **kwargs)


async def acheck_student_line(worker: MathWorker, *args, **kwargs) -> StepCheck:
    return await asyncio.to_thread(check_student_line, worker, *args, **kwargs)


# ============================================================ worker side (runs in the child process)


class _Unsupported(Exception):
    pass


class _Ambiguous(Exception):
    pass


def _out(verdict: str, reason: str, kind: str, normalized: Optional[str] = None, note: str = "") -> dict:
    return {"verdict": verdict, "reason_code": reason, "step_kind": kind, "normalized": normalized, "note": note[:160]}


def _num(expr) -> Optional[complex]:
    try:
        v = complex(sympy.N(expr))
    except Exception:
        return None
    if v != v or abs(v) == float("inf"):
        return None
    return v


def _real(expr) -> Optional[float]:
    v = _num(expr)
    if v is None or abs(v.imag) > _TOL * (1 + abs(v.real)):
        return None
    return v.real


def _close(a: float, b: float, rel: float = _TOL) -> bool:
    return abs(a - b) <= rel * (1 + abs(a))


def _parse_alts(text: str) -> list:
    parts = split_alternatives(text)
    if not parts:
        raise UnsafeExpression("empty")
    return [parse_statement(p) for p in parts]


def _symbols(items) -> list[Symbol]:
    out: set[Symbol] = set()
    for it in items:
        if isinstance(it, BooleanAtom):
            continue
        if isinstance(it, Eq):
            out |= it.lhs.free_symbols | it.rhs.free_symbols
        else:
            out |= it.free_symbols
    return sorted(out, key=str)


def _plain(expr) -> str:
    text = sympy.sstr(expr).replace(" ", "")
    # Floats print with 15 digits ("3.33000000000000"); show what the student wrote.
    text = re.sub(r"(\d+\.\d*?)0+(?=\D|$)", r"\1", text)
    return re.sub(r"(\d+)\.(?=\D|$)", r"\1", text)


def _show(item) -> str:
    return f"{_plain(item.lhs)}={_plain(item.rhs)}" if isinstance(item, Eq) else _plain(item)


def _show_all(items) -> str:
    return " or ".join(_show(i) for i in items)


def _show_set(values) -> str:
    ordered = sorted(values, key=lambda v: (_real(v) is None, _real(v) or 0.0, str(v)))
    return "{" + ",".join(_plain(v) for v in ordered) + "}"


def _elements(s) -> Optional[list]:
    if s == S.EmptySet:
        return []
    if isinstance(s, FiniteSet):
        return list(s)
    return None


def _roots(items, sym: Symbol):
    result = S.EmptySet
    for it in items:
        result = result.union(solveset(it, sym, S.Reals))
    return result


def _compare(step_set, ref_set) -> Optional[str]:
    """'equal', 'subset' (step lost solutions), 'superset' (step added some), 'differ', or None if undecidable."""
    a, b = _elements(step_set), _elements(ref_set)
    if a is not None and b is not None:
        va, vb = [_num(x) for x in a], [_num(x) for x in b]
        if any(v is None for v in va + vb):
            return "equal" if step_set == ref_set else None
        in_b = all(any(abs(x - y) <= _TOL * (1 + abs(x)) for y in vb) for x in va)
        in_a = all(any(abs(x - y) <= _TOL * (1 + abs(x)) for x in va) for y in vb)
        if in_a and in_b:
            return "equal"
        if in_b:
            return "subset"
        if in_a:
            return "superset"
        return "differ"
    if step_set == ref_set:
        return "equal"
    try:
        sub, sup = step_set.is_subset(ref_set), ref_set.is_subset(step_set)
    except Exception:
        return None
    if sub is True and sup is True:
        return "equal"
    if sub is True:
        return "subset"
    if sup is True:
        return "superset"
    if sub is False and sup is False:
        return "differ"
    return None


def _sign_flipped(a: list, b: list) -> bool:
    """Same size, same absolute values, but not the same numbers: a sign error."""
    va, vb = [_real(x) for x in a], [_real(x) for x in b]
    if not a or len(a) != len(b) or any(v is None for v in va + vb):
        return False
    if sorted(abs(v) for v in va) != sorted(abs(v) for v in vb):
        # allow tolerance
        if not all(_close(x, y) for x, y in zip(sorted(abs(v) for v in va), sorted(abs(v) for v in vb))):
            return False
    same = all(_close(x, y) for x, y in zip(sorted(va), sorted(vb)))
    return not same


def _require_single_symbol(items, what: str) -> Symbol:
    syms = _symbols(items)
    if len(syms) == 0:
        raise _Unsupported(f"{what} has no variable to work with")
    if len(syms) > 1:
        raise _Unsupported(f"{what} uses more than one variable")
    return syms[0]


def _reject_booleans(items, what: str) -> None:
    if any(isinstance(i, BooleanAtom) for i in items):
        raise _Unsupported(f"{what} has no variable, so there is nothing to solve")


def _as_equations(items, what: str) -> list:
    _reject_booleans(items, what)
    if not all(isinstance(i, Eq) for i in items):
        raise _Unsupported(f"{what} is an expression, not an equation")
    return list(items)


# ----- equivalent_equation


def _equivalent_equation(problem: str, step: str, previous: Optional[str]) -> dict:
    kind = "equivalent_equation"
    ref_items = _as_equations(_parse_alts(previous or problem), "the previous line" if previous else "the problem")
    step_items = _as_equations(_parse_alts(step), "the step")
    ref_syms, step_syms = _symbols(ref_items), _symbols(step_items)
    if len(ref_syms) != 1:
        raise _Unsupported("the reference equation must have exactly one variable")
    sym = ref_syms[0]
    if step_syms and step_syms != [sym]:
        raise _Ambiguous(f"the step uses '{step_syms[0]}' but the problem uses '{sym}'")
    norm = _show_all(step_items)
    step_set, ref_set = _roots(step_items, sym), _roots(ref_items, sym)
    rel = _compare(step_set, ref_set)
    if rel == "equal":
        return _out("valid", "ok", kind, norm, "same solutions as the previous line")
    if rel == "superset":
        return _out("invalid", "extraneous_root", kind, norm, "this step introduces solutions the original does not have")
    if rel == "subset":
        return _out("invalid", "domain_changed", kind, norm, "this step loses solutions, for example by dividing by something that can be zero")
    if rel == "differ":
        a, b = _elements(step_set), _elements(ref_set)
        if a is not None and b is not None and _sign_flipped(a, b):
            return _out("invalid", "sign_mismatch", kind, norm, "the solutions match the original except for their signs")
        return _out("invalid", "not_equivalent", kind, norm, "this step does not have the same solutions as the previous line")
    raise _Unsupported("could not compare the solution sets")


# ----- factorization / expression_identity


def _poles(expr, sym: Symbol):
    try:
        denom = sympy.fraction(sympy.together(expr))[1]
    except Exception:
        return None
    if not denom.has(sym):
        return S.EmptySet
    return solveset(denom, sym, S.Reals)


def _identical(a, b) -> bool:
    d = a - b
    if d == 0:
        return True
    if sympy.expand(d) == 0:
        return True
    return simplify(d) == 0


def _coefficient_sign_flip(a, b) -> bool:
    """Same terms, same sizes, some signs different (x^2-5x+6 vs x^2+5x+6)."""
    try:
        ta, tb = sympy.expand(a).as_coefficients_dict(), sympy.expand(b).as_coefficients_dict()
    except Exception:
        return False
    if set(ta) != set(tb) or ta == tb:
        return False
    return all(abs(ta[k]) == abs(tb[k]) for k in ta)


def _counterexample(a, b, syms: list[Symbol]) -> bool:
    import random

    rng = random.Random(42)
    for _ in range(25):
        point = {s: sympy.Rational(rng.randint(-30, 30), rng.randint(1, 10)) for s in syms}
        va, vb = _num(a.subs(point)), _num(b.subs(point))
        if va is None or vb is None:
            continue
        if abs(va - vb) > _TOL * (1 + abs(va)):
            return True
    return False


def _forms(item, other_is_eq: bool):
    """The expression(s) a step claims to equal."""
    if isinstance(item, Eq):
        d = item.lhs - item.rhs
        return [d] if other_is_eq else [item.lhs, item.rhs, d]
    return [item]


def _identity(kind: str, problem: str, step: str, previous: Optional[str]) -> dict:
    ref_alts = _parse_alts(previous or problem)
    step_alts = _parse_alts(step)
    if len(ref_alts) != 1 or len(step_alts) != 1:
        raise _Unsupported("an identity check compares one line with one line")
    ref, st = ref_alts[0], step_alts[0]
    if isinstance(ref, BooleanAtom) or isinstance(st, BooleanAtom):
        raise _Unsupported("no variable to check")
    syms = _symbols([ref, st])
    ref_syms, step_syms = _symbols([ref]), _symbols([st])
    if len(ref_syms) > 1:
        raise _Unsupported("more than one variable")
    if step_syms and ref_syms and step_syms != ref_syms:
        raise _Ambiguous(f"the step uses '{step_syms[0]}' but the problem uses '{ref_syms[0]}'")
    norm = _show(st)
    both_eq = isinstance(ref, Eq) and isinstance(st, Eq)
    if isinstance(st, Eq) and not isinstance(ref, Eq):
        raise _Unsupported("cannot compare an equation with an expression")
    step_form = (st.lhs - st.rhs) if isinstance(st, Eq) else st
    ref_forms = _forms(ref, both_eq)
    for target in ref_forms:
        if _identical(step_form, target):
            sym = ref_syms[0] if ref_syms else None
            if sym is not None:
                pa, pb = _poles(step_form, sym), _poles(target, sym)
                if pa is not None and pb is not None and pa != pb:
                    return _out("invalid", "domain_changed", kind, norm, "equal where both are defined, but the domain changes")
            return _out("valid", "ok", kind, norm, "exact symbolic identity")
    # Not an identical rewrite. An equation step may still be a valid equivalent equation.
    if both_eq:
        try:
            eq_result = _equivalent_equation(problem, step, previous)
        except (_Unsupported, _Ambiguous):
            eq_result = None
        if eq_result and eq_result["verdict"] == "valid":
            return _out("valid", "ok", kind, norm, "not an identical rewrite, but it has the same solutions")
        if eq_result and eq_result["reason_code"] in ("sign_mismatch", "extraneous_root", "domain_changed"):
            return _out("invalid", eq_result["reason_code"], kind, norm, eq_result["note"])
    target = ref_forms[-1] if both_eq else ref_forms[0]
    if _coefficient_sign_flip(step_form, target):
        return _out("invalid", "sign_mismatch", kind, norm, "the terms match the original except for a sign")
    if both_eq and len(ref_syms) == 1:
        sym = ref_syms[0]
        try:
            a, b = _elements(_roots([st], sym)), _elements(_roots([ref], sym))
        except Exception:
            a = b = None
        if a is not None and b is not None and _sign_flipped(a, b):
            return _out("invalid", "sign_mismatch", kind, norm, "a sign is wrong somewhere in the factors")
    if _counterexample(step_form, target, syms):
        return _out("invalid", "not_equivalent", kind, norm, "this is not equal to the expression it should rewrite")
    raise _Unsupported("could not prove the identity or find a counterexample")


# ----- candidate_root / solution_set / numeric_result


def _value_of(item, sym: Symbol):
    if isinstance(item, BooleanAtom):
        raise _Ambiguous("not a value")
    if isinstance(item, Eq):
        l, r = item.lhs, item.rhs
        if l == sym and not r.free_symbols:
            return r
        if r == sym and not l.free_symbols:
            return l
        if isinstance(l, Symbol) and l != sym and not r.free_symbols:
            raise _Ambiguous(f"the step names '{l}' but the problem uses '{sym}'")
        raise _Unsupported("not of the form x = value")
    if item.free_symbols:
        raise _Unsupported("not a plain value")
    return item


def _is_root(eq: Eq, sym: Symbol, value) -> bool:
    d = eq.lhs.subs(sym, value) - eq.rhs.subs(sym, value)
    if d.has(sympy.zoo) or d.has(sympy.nan) or d.has(sympy.oo) or d.has(-sympy.oo):
        return False
    if simplify(d) == 0:
        return True
    n = _num(d)
    return n is not None and abs(n) <= _TOL * 10


def _problem_equation(problem: str) -> tuple[list, Symbol]:
    items = _as_equations(_parse_alts(problem), "the problem")
    return items, _require_single_symbol(items, "the problem")


def _all_roots(items, sym) -> Optional[list]:
    try:
        return _elements(_roots(items, sym))
    except NotImplementedError:
        return None


def _same(a, b) -> bool:
    ra, rb = _real(a), _real(b)
    if ra is not None and rb is not None:
        return _close(ra, rb)
    return a == b


def _values(step: str, sym: Symbol) -> list:
    out: list = []
    for st in _parse_alts(step):
        v = _value_of(st, sym)
        if not any(_same(v, x) for x in out):
            out.append(v)
    return out


def _check_values(kind: str, problem: str, step: str, previous: Optional[str]) -> dict:
    items, sym = _problem_equation(problem)
    values = _values(step, sym)
    norm = f"{sym}={_plain(values[0])}" if (kind == "candidate_root" and len(values) == 1) else _show_set(values)
    bad = [v for v in values if not any(_is_root(eq, sym, v) for eq in items)]
    roots = _all_roots(items, sym)
    if bad:
        flipped = [v for v in bad if any(_is_root(eq, sym, -v) for eq in items)]
        if len(flipped) == len(bad) and roots is not None and (
            len(values) == len(bad) or _sign_flipped(values, roots)
        ):
            return _out("invalid", "sign_mismatch", kind, norm, "check the sign of the value")
        return _out("invalid", "extraneous_root", kind, norm, "a value does not satisfy the original equation")
    if roots is None:
        if kind == "solution_set":
            raise _Unsupported("each value is a root but completeness could not be established")
        return _out("valid", "ok", kind, norm, "the value is a root; completeness was not established")
    missing = [r for r in roots if not any(_same(r, v) for v in values)]
    if not missing:
        return _out("valid", "ok", kind, norm, "all roots found")
    word = "root remains" if len(missing) == 1 else "roots remain"
    return _out("partial", "partial_root", kind, norm, f"correct so far, but {len(missing)} {word}")


def _decimals(text: str) -> int:
    m = re.search(r"\d+\.(\d+)", text)
    return len(m.group(1)) if m else 0


def _numeric_text(step: str) -> str:
    """The value part of ``14``, ``3*4+2 = 14``, ``x = 4`` or ``4 = x``. Anything stranger is ambiguous."""
    parts = [p.strip() for p in step.split("=")]
    if len(parts) == 1:
        return parts[0]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise _Ambiguous("expected one value, optionally written as 'name = value'")
    left, right = parts
    if re.fullmatch(r"[A-Za-z]", left) or not _has_variable(left):
        return right
    if re.fullmatch(r"[A-Za-z]", right):
        return left
    raise _Ambiguous("expected one value, optionally written as 'name = value'")


def _numeric_result(problem: str, step: str, previous: Optional[str], rel_tol: float) -> dict:
    kind = "numeric_result"
    prob_items = _parse_alts(problem)
    value_text = _numeric_text(step)
    got_items = _parse_alts(value_text)
    if len(got_items) != 1:
        raise _Ambiguous("expected a single number")
    got = got_items[0]
    if isinstance(got, (BooleanAtom, Eq)) or got.free_symbols:
        raise _Unsupported("units and variables are not checked here")
    g = _real(got)
    if g is None:
        raise _Ambiguous("not a real number")
    norm = _plain(got)
    decimals = _decimals(value_text)

    def matches(expected: float) -> Optional[str]:
        if abs(g - expected) <= rel_tol * (1 + abs(expected)):
            return "exact"
        if decimals and round(expected, decimals) == round(g, decimals) and abs(g - expected) <= 0.5 * 10 ** (-decimals) + 1e-12:
            return "rounded"
        return None

    if len(prob_items) == 1 and not isinstance(prob_items[0], Eq) and not isinstance(prob_items[0], BooleanAtom):
        target = prob_items[0]
        if target.free_symbols:
            raise _Unsupported("the problem still has a variable")
        e = _real(target)
        if e is None:
            raise _Unsupported("the problem does not evaluate to a real number")
        how = matches(e)
        if how:
            return _out("valid", "ok", kind, norm, "matches the recomputed value" + (" (rounded)" if how == "rounded" else ""))
        if _close(g, -e, rel_tol):
            return _out("invalid", "sign_mismatch", kind, norm, "the size is right but the sign is wrong")
        return _out("invalid", "not_equivalent", kind, norm, "does not match the recomputed value")
    items, sym = _problem_equation(problem)
    roots = _all_roots(items, sym)
    if roots is None:
        raise _Unsupported("could not solve the problem numerically")
    reals = [x for x in (_real(r) for r in roots) if x is not None]
    hit = [r for r in reals if matches(r)]
    if hit:
        rest = len(reals) - len(hit)
        if rest > 0:
            return _out("partial", "partial_root", kind, norm, f"correct so far, but {rest} more value{'s' if rest > 1 else ''} to find")
        return _out("valid", "ok", kind, norm, "matches the recomputed value")
    if any(_close(g, -r, rel_tol) for r in reals):
        return _out("invalid", "sign_mismatch", kind, norm, "the size is right but the sign is wrong")
    return _out("invalid", "not_equivalent", kind, norm, "does not match the recomputed value")


# ----- dispatch


def _dispatch(problem: str, step_kind: str, step: str, previous: Optional[str], rel_tol: float) -> dict:
    if step_kind == "equivalent_equation":
        return _equivalent_equation(problem, step, previous)
    if step_kind in ("factorization", "expression_identity"):
        return _identity(step_kind, problem, step, previous)
    if step_kind in ("candidate_root", "solution_set"):
        return _check_values(step_kind, problem, step, previous)
    if step_kind == "numeric_result":
        return _numeric_result(problem, step, previous, rel_tol)
    raise _Unsupported("this kind of step cannot be checked")


def run_check(problem: str, step_kind: str, step: str, previous: Optional[str] = None, rel_tol: float = 1e-6) -> dict:
    """Entry point registered as the worker op ``check_step``."""
    try:
        return _dispatch(problem, step_kind, step, previous, rel_tol)
    except UnsafeExpression as exc:
        return _out("unknown", "ambiguous_input", step_kind, None, f"could not read that safely: {exc}")
    except _Ambiguous as exc:
        return _out("unknown", "ambiguous_input", step_kind, None, str(exc))
    except _Unsupported as exc:
        return _out("unknown", "unsupported", step_kind, None, str(exc))
    except (NotImplementedError, RecursionError, ArithmeticError, ValueError, TypeError, AttributeError) as exc:
        return _out("unknown", "unsupported", step_kind, None, f"could not check this ({type(exc).__name__})")
