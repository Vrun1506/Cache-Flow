"""Hershey handwriting strokes for agent ink (final-plan.md §5).

Maths layout is small on purpose: fractions ``a/b`` are stacked, ``^`` raises
its exponent, and ``sqrt(...)`` gets a radical. Everything else is plain text.
Returns ``None`` when the optional ``Hershey-Fonts`` package is missing, in
which case the iPad draws the text with its own handwriting font.

Coordinates are local to the object: origin top-left, y grows downward.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from ..schemas.board import BoardCmd, Metrics

BASE = 40.0  # font size the glyph metrics are measured at
CAP = 30.0  # cap height at BASE
AXIS = 10.0  # height of the fraction bar above the baseline at BASE
FONT_NAME = "futural"
MAX_POINTS = 6000

_ALIASES = {"−": "-", "–": "-", "×": "x", "·": "*", "≤": "<=", "≥": ">=", "≠": "!=", "→": "->", "π": "pi", "°": "o"}
_RUN = re.compile(r"[A-Za-z0-9.]+")

Poly = list[tuple[float, float]]


class _Box:
    """Polylines in glyph units with the baseline at y = 0 and y pointing up."""

    __slots__ = ("polys", "w")

    def __init__(self, polys: list[Poly] | None = None, w: float = 0.0) -> None:
        self.polys = polys or []
        self.w = w

    def moved(self, dx: float, dy: float) -> "_Box":
        return _Box([[(x + dx, y + dy) for x, y in p] for p in self.polys], self.w)

    def scaled(self, k: float) -> "_Box":
        return _Box([[(x * k, y * k) for x, y in p] for p in self.polys], self.w * k)


@lru_cache(maxsize=1)
def _font():
    try:
        from HersheyFonts import HersheyFonts  # optional dependency
    except ImportError:
        return None
    try:
        font = HersheyFonts()
        font.load_default_font(FONT_NAME)
        font.normalize_rendering(BASE)
    except Exception:  # a broken font install must not break a lesson
        return None
    return font


@lru_cache(maxsize=1)
def _metrics() -> tuple[float, float]:
    """(baseline y in font units, left bearing of a letter)."""
    font = _font()
    strokes = list(font.strokes_for_text("H"))
    baseline = min(p[1] for s in strokes for p in s)
    lb = min(p[0] for s in list(font.strokes_for_text("x")) for p in s)
    return baseline, lb


def _text_box(text: str) -> _Box:
    font = _font()
    baseline, lb = _metrics()
    text = "".join(_ALIASES.get(c, c) for c in text)
    strokes = [[(p[0], p[1] - baseline) for p in s] for s in font.strokes_for_text(text) if s]
    # The advance is where a marker letter would start, minus its own left bearing.
    marked = list(font.strokes_for_text(text + "x"))
    width = min(p[0] for s in marked[-2:] for p in s) - lb if marked else 0.0
    if not strokes and not text.strip():
        width = max(width, BASE * 0.4 * len(text))
    return _Box(strokes, max(width, 0.0))


# ---- parsing: atoms are ("t", str) | ("grp", atoms) | ("sqrt", atoms) | ("pow", base, exp) | ("frac", num, den)


def _split_run(run: str) -> tuple[str, str]:
    """Split a word into the part before an exponent's base and the base itself."""
    if run[-1].isalpha():
        return run[:-1], run[-1]
    m = re.search(r"[\d.]+$", run)
    return run[: m.start()], m.group()


def _atom(s: str, i: int, exponent: bool = False) -> tuple[list, int]:
    while i < len(s) and s[i] == " ":
        i += 1
    if i >= len(s):
        return [], i
    if s[i] == "(":
        inner, i = _seq(s, i + 1, ")")
        return [("grp", inner)], min(i + 1, len(s))
    if s.startswith("sqrt(", i):
        inner, i = _seq(s, i + 5, ")")
        return [("sqrt", inner)], min(i + 1, len(s))
    neg = ""
    if s[i] == "-" and i + 1 < len(s) and s[i + 1].isalnum():
        neg, i = "-", i + 1
    m = _RUN.match(s, i)
    if not m:
        return [("t", s[i])], i + 1
    run = m.group()
    if exponent:  # x^10 takes the digits, x^n takes one letter
        m2 = re.match(r"\d+|[A-Za-z]", run)
        run = m2.group()
    i += len(run)
    atoms: list = [("t", neg + run)]
    if i < len(s) and s[i] == "^" and not exponent:
        prefix, base = _split_run(neg + run)
        exp, i = _atom(s, i + 1, exponent=True)
        atoms = ([("t", prefix)] if prefix else []) + [("pow", [("t", base)], exp)]
    return atoms, i


def _is_operand(atom: tuple) -> bool:
    return atom[0] in ("grp", "pow", "sqrt", "frac") or (atom[0] == "t" and bool(_RUN.fullmatch(atom[1].lstrip("-"))))


def _seq(s: str, i: int, close: str | None) -> tuple[list, int]:
    atoms: list = []
    while i < len(s):
        c = s[i]
        if close and c == close:
            return atoms, i
        if c == "(":
            inner, i = _seq(s, i + 1, ")")
            atoms.append(("grp", inner))
            i = min(i + 1, len(s))
        elif s.startswith("sqrt(", i):
            inner, i = _seq(s, i + 5, ")")
            atoms.append(("sqrt", inner))
            i = min(i + 1, len(s))
        elif c == "√":
            nxt, i = _atom(s, i + 1)
            if len(nxt) == 1 and nxt[0][0] == "grp":
                nxt = nxt[0][1]
            atoms.append(("sqrt", nxt))
        elif c == "^" and atoms:
            exp, i = _atom(s, i + 1, exponent=True)
            last = atoms.pop()
            if last[0] == "t" and _RUN.fullmatch(last[1]):
                prefix, base = _split_run(last[1])
                if prefix:
                    atoms.append(("t", prefix))
                atoms.append(("pow", [("t", base)], exp))
            else:
                atoms.append(("pow", [last], exp))
        elif c == "/" and atoms and _is_operand(atoms[-1]) and i + 1 < len(s) and s[i + 1] != "/":
            den, j = _atom(s, i + 1)
            if not den:
                atoms.append(("t", "/"))
                i += 1
                continue
            num: list = []
            while atoms and _is_operand(atoms[-1]):
                num.insert(0, atoms.pop())
            num = [x for a in num for x in (a[1] if a[0] == "grp" and len(num) == 1 else [a])]
            den = [x for a in den for x in (a[1] if a[0] == "grp" and len(den) == 1 else [a])]
            atoms.append(("frac", num, den))
            i = j
        elif _RUN.match(s, i):
            m = _RUN.match(s, i)
            atoms.append(("t", m.group()))
            i = m.end()
        else:
            atoms.append(("t", c))
            i += 1
    return atoms, i


# ---- layout


def _row(boxes: list[_Box]) -> _Box:
    out, x = [], 0.0
    for b in boxes:
        out.extend(b.moved(x, 0).polys)
        x += b.w
    return _Box(out, x)


def _layout(atoms: list) -> _Box:
    boxes: list[_Box] = []
    for atom in atoms:
        kind = atom[0]
        if kind == "t":
            boxes.append(_text_box(atom[1]))
        elif kind == "grp":
            boxes.append(_row([_text_box("("), _layout(atom[1]), _text_box(")")]))
        elif kind == "pow":
            base, exp = _layout(atom[1]), _layout(atom[2]).scaled(0.6)
            raised = exp.moved(base.w, CAP * 0.55)
            boxes.append(_Box(base.polys + raised.polys, base.w + exp.w))
        elif kind == "frac":
            num, den = _layout(atom[1]).scaled(0.8), _layout(atom[2]).scaled(0.8)
            w = max(num.w, den.w) + 6
            parts = num.moved((w - num.w) / 2, AXIS + 6).polys + den.moved((w - den.w) / 2, AXIS - 6 - CAP * 0.8).polys
            parts.append([(1.0, AXIS), (w - 1.0, AXIS)])
            boxes.append(_Box(parts, w + 2))
        elif kind == "sqrt":
            inner = _layout(atom[1])
            top = CAP + 4
            radical = [(0.0, 12.0), (3.0, 9.0), (7.0, -4.0), (11.0, top), (11.0 + inner.w + 4, top)]
            boxes.append(_Box([radical] + inner.moved(12.0, 0).polys, inner.w + 18))
    return _row(boxes)


def text_to_strokes(text: str, height: float = 40) -> dict[str, Any] | None:
    """Handwriting strokes for ``text``: ``{"strokes": [[[x, y], ...]], "w": .., "h": ..}``.

    ``height`` is the line height in output units. Returns ``None`` when the
    font package is unavailable or the text produces no strokes.
    """
    if _font() is None or not text or not text.strip():
        return None
    lines = [ln for ln in text.splitlines() if ln.strip()][:3]
    rows: list[_Box] = []
    for ln in lines:
        atoms, _ = _seq(ln, 0, None)
        rows.append(_layout(atoms))
    polys: list[Poly] = []
    line_h = BASE * 1.5
    for n, box in enumerate(rows):
        polys.extend(box.moved(0, -n * line_h).polys)
    polys = [p for p in polys if len(p) >= 1]
    if not polys:
        return None
    xs = [x for p in polys for x, _ in p]
    ys = [y for p in polys for _, y in p]
    x0, y1, y0 = min(xs), max(ys), min(ys)
    k = height / BASE
    strokes = [[[round((x - x0) * k, 1), round((y1 - y) * k, 1)] for x, y in p] for p in polys]
    if sum(len(s) for s in strokes) > MAX_POINTS:
        return None
    return {"strokes": strokes, "w": round((max(xs) - x0) * k, 1), "h": round((y1 - y0) * k, 1)}


def attach_strokes(cmds: list[BoardCmd], height: float = 40) -> list[BoardCmd]:
    """Fill ``payload.strokes`` and ``payload.metrics`` on every ``write`` command that can have them."""
    for cmd in cmds:
        if cmd.op != "write" or not cmd.payload.text:
            continue
        result = text_to_strokes(cmd.payload.text, height)
        if result:
            cmd.payload.strokes = result["strokes"]
            cmd.payload.metrics = Metrics(w=result["w"], h=result["h"])
    return cmds
