"""Safe parsing of model-written maths (final-plan.md §16.3, §15.4).

Model output is untrusted. ``check_expr_safe`` validates one expression with an
AST whitelist and never evaluates it. ``parse_statement`` evaluates only after
that check, with empty builtins, and is meant to run inside the worker process
(which gives it a kill timeout).
"""

from __future__ import annotations

import ast
import re

import sympy
from sympy.parsing.sympy_parser import (
    convert_xor,
    implicit_multiplication_application,
    standard_transformations,
    stringify_expr,
)

MAX_LEN = 200
MAX_NODES = 200
MAX_EXPONENT = 1000
MAX_DIGITS = 15

_TRANSFORMS = standard_transformations + (implicit_multiplication_application, convert_xor)

# Names the user-facing text may contain, mapped to sympy objects.
_USER_NAMES = {
    "sin": sympy.sin, "cos": sympy.cos, "tan": sympy.tan,
    "asin": sympy.asin, "acos": sympy.acos, "atan": sympy.atan,
    "exp": sympy.exp, "log": sympy.log, "ln": sympy.log,
    "sqrt": sympy.sqrt, "Abs": sympy.Abs, "abs": sympy.Abs,
    "floor": sympy.floor, "ceiling": sympy.ceiling, "ceil": sympy.ceiling,
    "Min": sympy.Min, "Max": sympy.Max, "min": sympy.Min, "max": sympy.Max,
    "pi": sympy.pi, "E": sympy.E,
}
_CONSTANT_NAMES = {"pi", "E"}
# Names the transformations emit; never accepted from the raw text.
_INTERNAL = {
    "Symbol": sympy.Symbol, "Integer": sympy.Integer,
    "Float": sympy.Float, "Rational": sympy.Rational,
}
_CALLABLES = set(_USER_NAMES) - _CONSTANT_NAMES | set(_INTERNAL)
_EVAL_GLOBALS = {**_USER_NAMES, **_INTERNAL}

_ALLOWED_NODES = (
    ast.Expression, ast.Call, ast.Name, ast.Constant, ast.BinOp, ast.UnaryOp,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.USub, ast.UAdd, ast.Load,
)
_FORBIDDEN_CHARS = ("__", "\n", "\r", ";", "[", "]", "{", "}", "'", '"', "\\")
_WORD = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
_NUMBER_STR = re.compile(r"^(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*$")


class UnsafeExpression(ValueError):
    """The text is not a supported, safe expression."""


def normalize(text: str) -> str:
    if not isinstance(text, str):
        raise UnsafeExpression("not text")
    text = (
        text.replace("−", "-").replace("×", "*").replace("·", "*").replace("÷", "/")
        .replace("²", "**2").replace("³", "**3").strip()
    )
    if len(text) > MAX_LEN:
        raise UnsafeExpression(f"longer than {MAX_LEN} characters")
    for bad in _FORBIDDEN_CHARS:
        if bad in text:
            raise UnsafeExpression("forbidden character")
    return text


def _check_words(text: str) -> None:
    """Reject long unknown words.

    The transformations split an unknown word into a product of one-letter
    symbols (``factorial`` becomes ``f*a*c*...``). That is harmless but never
    what the author meant, so reject words of three or more letters that are
    not whitelisted. Short words such as ``xy`` stay legal implicit products.
    """
    for word in _WORD.findall(text):
        if len(word) >= 3 and word not in _USER_NAMES:
            raise UnsafeExpression(f"unknown name '{word[:20]}'")


def _numeric_value(node: ast.Constant, parent_is_float: bool = False) -> float | int | None:
    value = node.value
    if isinstance(value, bool):
        raise UnsafeExpression("unexpected constant")
    if isinstance(value, int):
        if len(str(abs(value))) > MAX_DIGITS:
            raise UnsafeExpression("number too long")
        return value
    if isinstance(value, float):
        if abs(value) > 10**MAX_DIGITS:
            raise UnsafeExpression("number too large")
        return value
    return None


def _check_tree(tree: ast.AST) -> None:
    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_NODES:
        raise UnsafeExpression("expression too large")
    for node in nodes:
        if not isinstance(node, _ALLOWED_NODES):
            raise UnsafeExpression(f"unsupported syntax: {type(node).__name__}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _CALLABLES:
                raise UnsafeExpression("call to a non-whitelisted function")
            if node.keywords:
                raise UnsafeExpression("keyword arguments not allowed")
            if node.func.id in ("Symbol", "Float"):
                for arg in node.args:
                    if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                        raise UnsafeExpression(f"bad argument to {node.func.id}")
                    text = arg.value
                    if node.func.id == "Symbol":
                        if len(text) > 20 or not _IDENT.match(text):
                            raise UnsafeExpression("bad symbol name")
                    else:
                        if not _NUMBER_STR.match(text):
                            raise UnsafeExpression("bad number")
                        mantissa = re.split(r"[eE]", text)[0].replace(".", "")
                        if len(mantissa.lstrip("0")) > MAX_DIGITS:
                            raise UnsafeExpression("number too long")
                        if abs(float(text)) > 10**MAX_DIGITS:
                            raise UnsafeExpression("number too large")
        elif isinstance(node, ast.Name):
            if node.id not in _CALLABLES and node.id not in _CONSTANT_NAMES:
                raise UnsafeExpression(f"unknown name '{node.id[:20]}'")
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                continue  # only valid as an argument of Symbol/Float; checked above
            _numeric_value(node)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            for sub in ast.walk(node.right):
                if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.Pow):
                    raise UnsafeExpression("nested powers are not allowed")
                if isinstance(sub, ast.Constant) and not isinstance(sub.value, str):
                    if abs(sub.value) > MAX_EXPONENT:
                        raise UnsafeExpression("exponent too large")
                if (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Name)
                    and sub.func.id == "Float"
                    and sub.args
                    and isinstance(sub.args[0], ast.Constant)
                    and isinstance(sub.args[0].value, str)
                    and abs(float(sub.args[0].value)) > MAX_EXPONENT
                ):
                    raise UnsafeExpression("exponent too large")


def _compile_safe(text: str) -> tuple[str, ast.Expression]:
    text = normalize(text)
    if not text:
        raise UnsafeExpression("empty expression")
    if "=" in text:
        raise UnsafeExpression("expected a single expression")
    _check_words(text)
    try:
        # The constructors must be known to the transformations, otherwise implicit
        # multiplication turns ``Integer (2)`` into ``Integer*(2)``. Raw user text
        # can never name them: ``_check_words`` above rejects any such word.
        code = stringify_expr(text, {}, dict(_EVAL_GLOBALS), _TRANSFORMS)
        tree = ast.parse(code, mode="eval")
    except SyntaxError:
        raise UnsafeExpression("could not parse") from None
    except UnsafeExpression:
        raise
    except Exception as exc:  # transformation errors on odd input
        raise UnsafeExpression(f"could not parse ({type(exc).__name__})") from None
    _check_tree(tree)
    return code, tree


def check_expr_safe(text: str) -> str:
    """Validate one expression without evaluating it. Returns the checked code."""
    return _compile_safe(text)[0]


def _evaluate(text: str):
    _, tree = _compile_safe(text)
    return eval(compile(tree, "<expr>", "eval"), {"__builtins__": {}, **_EVAL_GLOBALS}, {})  # noqa: S307


_INEQUALITY = re.compile(r"[<>!]")


def parse_statement(text: str):
    """Parse ``lhs = rhs`` into ``Eq`` (possibly collapsed to true/false), or an expression."""
    text = normalize(text)
    if not text:
        raise UnsafeExpression("empty statement")
    if _INEQUALITY.search(text) or "==" in text:
        raise UnsafeExpression("unsupported")
    parts = text.split("=")
    if len(parts) == 1:
        return _evaluate(text)
    if len(parts) != 2:
        raise UnsafeExpression("unsupported")
    lhs, rhs = _evaluate(parts[0]), _evaluate(parts[1])
    return sympy.Eq(lhs, rhs)


def split_alternatives(text: str) -> list[str]:
    """Split ``x=-2 or x=-3`` / ``x=-2, x=-3`` into statements (top-level only)."""
    text = re.sub(r"\s+(or|and)\s+", ",", text.replace("∨", ","))
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]
