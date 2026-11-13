"""Dimensional analysis and a numeric recompute for physics steps (final-plan.md §16.3, `physics` row).

``verified`` needs BOTH dimensional consistency and a numeric recompute within 1% relative
tolerance, as the spec says. Anything this module cannot parse or cannot compare is
``unverified``; it never guesses. ``failed`` is reserved for a real, demonstrated mismatch.

Strings are parsed by a small recursive-descent parser written for this module. Nothing is
``eval``'d and pint's own expression parser is only used on single, validated unit names.

Grammar (case matters, ``×``/``·`` mean ``*``, ``−`` means ``-``, ``²``/``³`` mean ``^2``/``^3``)::

    expr    := term (('+' | '-') term)*
    term    := unary (('*' | '/') unary)*
    unary   := '-' unary | power
    power   := atom ('^' | '**') unary          # the exponent must be a plain number, |e| <= 12
    atom    := number unit_chain? | name | '(' expr ')'
    unit_chain := unit ('^' int)? (('*' | '/') unit ('^' int)?)*

A number followed by units is one quantity literal, so ``5 N / 2 m`` means (5 N) / (2 m) and
``3 m/s^2`` is one acceleration. A bare name is a *symbol* that must be defined by a fact such as
``m = 2 kg``; a name that is not a fact is an unknown symbol, never silently a unit. That is what
stops ``g`` meaning gram when it means gravity. Facts are self-contained quantities (``a = 3 m/s^2``);
once a fact defines ``m`` it is a symbol everywhere, so ``3 m`` in a step is then not understood and the
step is ``unverified`` rather than guessed.
"""

from __future__ import annotations

import math
import re
import threading
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Sequence

MAX_CHARS = 200
MAX_TOKENS = 120
MAX_DEPTH = 8
MAX_EXPONENT = 12
MAX_MAGNITUDE = 1e300
REL_TOL = 0.01  # the spec's 1% tolerance, relative to the larger of the two values

# Single-letter unit symbols we accept inside a quantity literal. Others (a, d, h, t ...) are
# ambiguous (year, day, hour, tonne) and are treated as unknown.
SINGLE_LETTER_UNITS = frozenset("msgNJWVAKCFTHL")

_NAME = r"[A-Za-z][A-Za-z0-9_]{0,15}"
_NUMBER = r"(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?"
_TOKEN = re.compile(rf"\s*(?:(?P<num>{_NUMBER})|(?P<name>[A-Za-zµΩ°][A-Za-z0-9_µΩ°]*)|(?P<op>\*\*|[-+*/^()]))")
_SUPERSCRIPTS = {"²": "^2", "³": "^3", "⁻¹": "^-1"}
_TRANSLATE = str.maketrans({"×": "*", "·": "*", "−": "-", "–": "-", "÷": "/"})

Status = Literal["verified", "unverified", "failed"]

_lock = threading.RLock()
_registry: Any = None


class UnitsError(ValueError):
    """The text cannot be parsed or evaluated safely."""


class DimensionError(UnitsError):
    """The text parses, but it adds or subtracts quantities of different dimensions."""


@dataclass(frozen=True)
class UnitsResult:
    status: Status
    note: str = ""
    values: tuple[str, ...] = ()  # the evaluated sides, for the caller's evidence line


@dataclass(frozen=True)
class DimensionCheck:
    consistent: bool | None  # None: not enough to decide
    dimensions: tuple[str, ...]
    note: str = ""


def registry():
    """One pint registry per process, built lazily (it takes a moment)."""
    global _registry
    with _lock:
        if _registry is None:
            import pint

            _registry = pint.UnitRegistry(autoconvert_offset_to_baseunit=True)
        return _registry


def warm() -> None:
    registry()


# -- tokens ------------------------------------------------------------------------------


def _prepare(text: str) -> str:
    if not isinstance(text, str):
        raise UnitsError("not text")
    for sup, rep in _SUPERSCRIPTS.items():
        text = text.replace(sup, rep)
    text = text.translate(_TRANSLATE).strip()
    if not text:
        raise UnitsError("empty")
    if len(text) > MAX_CHARS:
        raise UnitsError("too long")
    return text


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(text):
        if text[pos:].strip() == "":
            break
        m = _TOKEN.match(text, pos)
        if not m:
            raise UnitsError(f"unexpected character {text[pos:pos + 1]!r}")
        kind = m.lastgroup or ""
        tokens.append((kind, m.group(kind)))
        pos = m.end()
        if len(tokens) > MAX_TOKENS:
            raise UnitsError("too many tokens")
    return tokens


# -- evaluator ---------------------------------------------------------------------------


class _Parser:
    def __init__(self, text: str, symbols: dict[str, Any]) -> None:
        self.ureg = registry()
        self.toks = _tokenize(_prepare(text))
        self.i = 0
        self.symbols = symbols
        self.depth = 0

    # token helpers
    def peek(self, k: int = 0) -> tuple[str, str] | None:
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else None

    def take(self) -> tuple[str, str]:
        tok = self.peek()
        if tok is None:
            raise UnitsError("unexpected end")
        self.i += 1
        return tok

    def is_op(self, value: str, k: int = 0) -> bool:
        tok = self.peek(k)
        return tok is not None and tok[0] == "op" and tok[1] == value

    def parse(self):
        value = self.expr()
        if self.peek() is not None:
            raise UnitsError(f"unexpected {self.peek()[1]!r}")
        return self.checked(value)

    def checked(self, q):
        mag = q.magnitude
        if not isinstance(mag, (int, float)) or not math.isfinite(mag) or abs(mag) > MAX_MAGNITUDE:
            raise UnitsError("number out of range")
        return q

    # grammar
    def expr(self):
        left = self.term()
        while self.is_op("+") or self.is_op("-"):
            op = self.take()[1]
            right = self.term()
            try:
                left = left + right if op == "+" else left - right
            except Exception as exc:  # pint.DimensionalityError and friends
                if type(exc).__name__ == "DimensionalityError":
                    raise DimensionError(
                        f"cannot {'add' if op == '+' else 'subtract'} {_dimension_text(left)} and {_dimension_text(right)}"
                    ) from exc
                raise UnitsError(f"cannot combine these: {_short(exc)}") from exc
            self.checked(left)
        return left

    def term(self):
        left = self.unary()
        while self.is_op("*") or self.is_op("/"):
            op = self.take()[1]
            right = self.unary()
            left = left * right if op == "*" else self.divide(left, right)
            self.checked(left)
        return left

    def divide(self, a, b):
        if b.magnitude == 0:
            raise UnitsError("division by zero")
        return a / b

    def unary(self):
        if self.is_op("-"):
            self.take()
            return -self.unary()
        return self.power()

    def power(self):
        base = self.atom()
        if self.is_op("^") or self.is_op("**"):
            self.take()
            exp = self.exponent()
            try:
                base = base**exp
            except Exception as exc:
                raise UnitsError(f"bad power: {_short(exc)}") from exc
            self.checked(base)
        return base

    def exponent(self) -> float:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise UnitsError("too deeply nested")
        try:
            q = self.unary()
        finally:
            self.depth -= 1
        if not q.dimensionless:
            raise UnitsError("an exponent must be a plain number")
        e = float(q.to("dimensionless").magnitude)
        if not math.isfinite(e) or abs(e) > MAX_EXPONENT:
            raise UnitsError("exponent too large")
        return int(e) if e == int(e) else e

    def atom(self):
        kind, value = self.take()
        if kind == "num":
            number = float(value)
            return self.unit_chain(number)
        if kind == "name":
            if value in self.symbols:
                return self.symbols[value]
            if value == "pi":
                return self.ureg.Quantity(math.pi)
            raise UnitsError(f"unknown symbol {value!r}")
        if kind == "op" and value == "(":
            self.depth += 1
            if self.depth > MAX_DEPTH:
                raise UnitsError("too deeply nested")
            inner = self.expr()
            self.depth -= 1
            if not self.is_op(")"):
                raise UnitsError("missing )")
            self.take()
            return inner
        raise UnitsError(f"unexpected {value!r}")

    # quantity literals
    def unit_name(self, tok: tuple[str, str] | None):
        """The pint unit for a name token, or None when it is not an acceptable unit name."""
        if tok is None or tok[0] != "name":
            return None
        name = tok[1]
        if name in self.symbols or name == "pi":
            return None
        if len(name) == 1:
            if name not in SINGLE_LETTER_UNITS:
                return None
        elif not re.fullmatch(r"[A-Za-zµΩ°]{2,12}", name):
            return None
        try:
            unit = self.ureg.parse_units(name)
        except Exception:
            return None
        return unit

    def unit_chain(self, number: float):
        """After a number: any units that directly follow it belong to the same quantity."""
        q = self.ureg.Quantity(number)
        first = True
        while True:
            op = None
            if not first:
                if self.is_op("*") or self.is_op("/"):
                    op = self.peek()[1]
                    unit = self.unit_name(self.peek(1))
                    if unit is None:
                        return q  # `2 kg * 3 m` or `2 kg * a`: the chain ends, the operator is the caller's
                    self.i += 1
                else:
                    return q
            elif self.is_op("*") and self.unit_name(self.peek(1)) is not None:
                op = "*"
                unit = self.unit_name(self.peek(1))
                self.i += 1
            else:
                unit = self.unit_name(self.peek())
                if unit is None:
                    return q
            self.i += 1  # the unit name
            power = 1
            if self.is_op("^") or self.is_op("**"):
                power = self.unit_exponent()
            factor = self.ureg.Quantity(1, unit) ** power
            q = q * factor if op in (None, "*") else q / factor
            first = False

    def unit_exponent(self) -> int:
        self.take()  # ^
        paren = self.is_op("(")
        if paren:
            self.take()
        sign = 1
        if self.is_op("-"):
            self.take()
            sign = -1
        tok = self.take()
        if tok[0] != "num" or "." in tok[1] or "e" in tok[1].lower():
            raise UnitsError("a unit exponent must be a whole number")
        n = sign * int(tok[1])
        if abs(n) > 6:
            raise UnitsError("unit exponent too large")
        if paren:
            if not self.is_op(")"):
                raise UnitsError("missing )")
            self.take()
        return n


def _short(exc: BaseException) -> str:
    return " ".join(str(exc).split())[:100]


def evaluate(text: str, symbols: dict[str, Any] | None = None):
    """Evaluate one side of an equation to a pint Quantity. Raises UnitsError."""
    with _lock:
        try:
            return _Parser(text, symbols or {}).parse()
        except UnitsError:
            raise
        except (RecursionError, OverflowError, ValueError, ArithmeticError, TypeError) as exc:
            raise UnitsError(_short(exc) or type(exc).__name__) from exc
        except Exception as exc:  # pint internals: never let one escape as a crash
            raise UnitsError(f"{type(exc).__name__}: {_short(exc)}") from exc


def parse_facts(facts: Iterable[str]) -> dict[str, Any]:
    """``name = quantity`` strings become symbols. Facts that do not parse are skipped, not guessed at."""
    symbols: dict[str, Any] = {}
    for fact in facts:
        if not isinstance(fact, str) or fact.count("=") != 1:
            continue
        name, rhs = (part.strip() for part in fact.split("="))
        if not re.fullmatch(_NAME, name):
            continue
        try:
            symbols[name] = evaluate(rhs, {})  # a fact is a self-contained quantity: `m = 2 kg` must not see itself
        except UnitsError:
            continue
    return symbols


# -- public helpers -------------------------------------------------------------------------


def _dimension_text(q) -> str:
    return "dimensionless" if q.dimensionless else str(q.dimensionality)


def dimension_of(text: str, facts: Iterable[str] = ()) -> str:
    """The dimensions of an expression, such as ``[length] / [time] ** 2``. Raises UnitsError."""
    return _dimension_text(evaluate(text, parse_facts(facts)))


def _sides(expression: str, symbols: dict[str, Any]) -> tuple[list[str], list[Any]]:
    """Evaluate every side of ``a = b = c``. A bare unknown name is a label and is skipped."""
    prepared = _prepare(expression)
    if re.search(r"[<>!]|==|=>|=<", prepared):
        raise UnitsError("only a plain = is supported")
    parts = [p.strip() for p in prepared.split("=")]
    if any(not p for p in parts):
        raise UnitsError("an empty side")
    labels, values = [], []
    for part in parts:
        if re.fullmatch(_NAME, part) and part not in symbols and part != "pi":
            labels.append(part)
            continue
        values.append((part, evaluate(part, symbols)))
    return labels, values


def check_dimensions(expression: str, facts: Iterable[str] = ()) -> DimensionCheck:
    """Are all evaluable sides of ``a = b = c`` dimensionally the same?"""
    try:
        _, values = _sides(expression, parse_facts(facts))
    except DimensionError as exc:
        return DimensionCheck(False, (), str(exc))
    except UnitsError as exc:
        return DimensionCheck(None, (), str(exc))
    dims = tuple(_dimension_text(v) for _, v in values)
    if len(values) < 2:
        return DimensionCheck(None, dims, "only one side can be evaluated")
    same = all(v.dimensionality == values[0][1].dimensionality for _, v in values[1:])
    return DimensionCheck(same, dims, "" if same else "dimension mismatch: " + " vs ".join(dims))


def consistent(lhs: str, rhs: str, facts: Iterable[str] = ()) -> bool:
    """Do two expressions have the same dimensions? Raises UnitsError if either cannot be parsed."""
    symbols = parse_facts(facts)
    return evaluate(lhs, symbols).dimensionality == evaluate(rhs, symbols).dimensionality


_UNIT_TEXT = re.compile(r"^[A-Za-zµΩ°0-9_/*^()\- ]{1,40}$")


def convert(value: float, from_unit: str, to_unit: str) -> float:
    """Convert a number between units. Raises UnitsError on a dimension mismatch or unknown unit."""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise UnitsError("value must be a finite number")
    if not (_UNIT_TEXT.match(from_unit or "") and _UNIT_TEXT.match(to_unit or "")):
        raise UnitsError("unit text not accepted")
    ureg = registry()
    with _lock:
        try:
            result = ureg.Quantity(float(value), ureg.parse_units(from_unit)).to(ureg.parse_units(to_unit)).magnitude
        except Exception as exc:
            raise UnitsError(f"cannot convert {from_unit} to {to_unit}: {_short(exc)}") from exc
    if not math.isfinite(result):
        raise UnitsError("result out of range")
    return float(result)


def _fmt(q) -> str:
    with _lock:
        mag = q.magnitude
        unit = "" if q.dimensionless else f" {q.units:~P}"
        return f"{mag:.4g}{unit}"


def _compare(values: Sequence[tuple[str, Any]]) -> UnitsResult:
    """All values must share dimensions and agree numerically within 1%."""
    texts = tuple(_fmt(v) for _, v in values)
    first = values[0][1]
    for (_, other), text in zip(values[1:], texts[1:]):
        if first.dimensionless != other.dimensionless:
            return UnitsResult("unverified", "one side has units and the other has none", texts)
        if first.dimensionality != other.dimensionality:
            return UnitsResult(
                "failed", f"dimension mismatch: {_dimension_text(first)} vs {_dimension_text(other)}", texts
            )
        with _lock:
            a = float(first.to_base_units().magnitude)
            b = float(other.to_base_units().magnitude)
        if not math.isclose(a, b, rel_tol=REL_TOL, abs_tol=1e-12):
            return UnitsResult("failed", f"{texts[0]} does not match {text} (more than 1% apart)", texts)
    return UnitsResult("verified", "dimensions agree and the numbers match within 1%: " + " = ".join(texts), texts)


def verify_physics_step(step_expr: str, facts: Iterable[str] = ()) -> UnitsResult:
    """Check one physics step such as ``F = 2 kg * 3 m/s^2 = 6 N`` against the given facts.

    ``verified`` needs at least two sides that can be evaluated, the same dimensions on all of
    them, and values within 1%. A side that is just an undefined name (``F`` above) is a label.
    Anything unparsable, unknown or one-sided is ``unverified``; only a demonstrated mismatch of
    dimensions or numbers is ``failed``.
    """
    try:
        _, values = _sides(step_expr, parse_facts(facts))
    except DimensionError as exc:
        return UnitsResult("failed", f"dimension error: {exc}"[:160])
    except UnitsError as exc:
        return UnitsResult("unverified", f"not checked: {exc}"[:160])
    if len(values) < 2:
        only = f" ({_fmt(values[0][1])})" if values else ""
        return UnitsResult("unverified", f"nothing to recompute against{only}"[:160])
    return _compare(values)


def verify_physics_final(final_value: str, step_exprs: Sequence[str], facts: Iterable[str] = ()) -> UnitsResult:
    """Does the final answer match what the last evaluable step computed?"""
    symbols = parse_facts(facts)
    try:
        _, final_sides = _sides(final_value if "=" in final_value else f"ans = {final_value}", symbols)
    except UnitsError as exc:
        return UnitsResult("unverified", f"final answer not checked: {exc}"[:160])
    if not final_sides:
        return UnitsResult("unverified", "final answer has no value")
    answer = final_sides[-1]
    for expr in reversed(list(step_exprs)):
        try:
            _, sides = _sides(expr, symbols)
        except UnitsError:
            continue
        if len(sides) >= 1:
            return _compare([sides[-1], answer])
    return UnitsResult("unverified", "no step computed a value to compare with")
