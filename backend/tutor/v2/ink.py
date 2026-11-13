"""Board commands to the iPad's ``tutor_ink_preview`` payloads (Interface.md v2).

One ``BoardCmd`` becomes zero or more ink dicts::

    {"ink_id": str, "style": "write"|"highlight"|"circle",
     "region": [x, y, w, h], "strokes": [[[x, y], ...], ...], "formula"?: str}

All coordinates are board space. ``ink_is_valid`` is a port of the Swift
``Bounds.isValid(ink, within: board)``; every dict this module returns passes it.

Mapping decisions (the iPad's ``InkStyle`` has only write, highlight, circle):

* ``write``     -> ``write``. Hershey strokes from ``payload.strokes`` are translated and scaled into the
                   placed rectangle (aspect preserved, simplified to <= 512 points per stroke). Without strokes the
                   text goes in ``formula`` (<= 200 chars) and the iPad typesets it.
* ``highlight`` -> ``highlight`` with a region 8x6 points larger than the referenced object, no strokes.
* ``underline`` -> a ``write`` ink with ONE hand-drawn stroke under the referenced object. A pen stroke looks the
                   same on every iPad renderer, while a thin "highlight" band depends on how highlights are filled.
* ``shape``     -> ``write`` with generated outline strokes (rect, triangle, brace, line); ``circle`` uses style
                   ``circle``. A shape label (``payload.text``) is added as a separate formula ink inside it.
* ``arrow``     -> ``write`` with two strokes: the shaft and the arrow head. Refs may also be ``"x,y"`` points.
* ``table``     -> one ``write`` ink of grid lines plus one formula ink per row ("a | b | c"); when the table had to be
                   shrunk below 60 points per column it becomes a single formula ink instead.
* ``plot``, ``focus``, ``erase``, ``group`` -> no ink (``[]``). A plot is a scene: use
                   ``tutor.v2.primitives.plot_cmd_to_scene``. ``focus`` and ``erase`` are not part of the v2 contract.

The ``style.color`` of a command is dropped: v2 ink has no colour field.
"""

from __future__ import annotations

import math
import re
from typing import Any, Optional

from ..schemas.board import BoardCmd
from .layout import Placer, Rect, round_up

MAX_STROKES = 256
MAX_POINTS = 512
MAX_FORMULA = 200
EDGE_TOL = 8.0  # Swift: points may sit up to 8 points outside the region

LINE_H = 36.0
CHAR_W = 15.0
PAD = 6.0

_NUM = re.compile(r"-?\d+(?:\.\d+)?")


# ------------------------------------------------------------------- validity


def _finite(*vals: float) -> bool:
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in vals)


def ink_is_valid(ink: dict[str, Any], board: Optional[Rect]) -> bool:
    """Port of ``Bounds.isValid(_ ink: TutorInk, within:)``."""
    try:
        strokes = ink.get("strokes", [])
        formula = ink.get("formula") or ""
        style = ink.get("style", "write")
        region = Rect.from_list(ink["region"])
    except (KeyError, TypeError, ValueError):
        return False
    if len(strokes) > MAX_STROKES or any(len(s) > MAX_POINTS for s in strokes):
        return False
    if len(formula) > MAX_FORMULA:
        return False
    if not (strokes or formula or style != "write"):
        return False
    if not _finite(region.x, region.y, region.w, region.h) or region.w <= 0 or region.h <= 0:
        return False
    if board is not None and not board.contains(region):
        return False
    area = region.inset(-EDGE_TOL, -EDGE_TOL)
    for stroke in strokes:
        for p in stroke:
            if len(p) != 2 or not _finite(p[0], p[1]) or not area.contains_point(p[0], p[1]):
                return False
    return True


def ink_cap(inks: list[dict[str, Any]], n: int, board: Optional[Rect] = None) -> list[dict[str, Any]]:
    """Keep the first ``n`` inks that are valid. Order is preserved (it is the draw order)."""
    out = [i for i in inks if board is None or ink_is_valid(i, board)]
    return out[: max(0, n)]


# -------------------------------------------------------------------- strokes


def _perp_distance(p, a, b) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    if dx == 0 and dy == 0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    return abs(dy * p[0] - dx * p[1] + b[0] * a[1] - b[1] * a[0]) / math.hypot(dx, dy)


def douglas_peucker(points: list[tuple[float, float]], eps: float) -> list[tuple[float, float]]:
    """Iterative Douglas-Peucker (no recursion limit problems on long strokes)."""
    n = len(points)
    if n < 3:
        return list(points)
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        lo, hi = stack.pop()
        best, idx = -1.0, -1
        for i in range(lo + 1, hi):
            d = _perp_distance(points[i], points[lo], points[hi])
            if d > best:
                best, idx = d, i
        if best > eps and idx > 0:
            keep[idx] = True
            stack.append((lo, idx))
            stack.append((idx, hi))
    return [p for p, k in zip(points, keep) if k]


def simplify(stroke: list[tuple[float, float]], limit: int = MAX_POINTS) -> list[tuple[float, float]]:
    """Reduce ``stroke`` to at most ``limit`` points, growing the tolerance until it fits."""
    if len(stroke) <= limit:
        return list(stroke)
    eps = 0.25
    out = stroke
    while len(out) > limit and eps < 1e4:
        out = douglas_peucker(stroke, eps)
        eps *= 1.6
    if len(out) > limit:  # pathological: sample evenly, keeping both ends
        step = (len(stroke) - 1) / (limit - 1)
        out = [stroke[round(i * step)] for i in range(limit)]
    return out


def _round(p: tuple[float, float]) -> list[float]:
    return [round(p[0], 1), round(p[1], 1)]


def _bbox(strokes: list[list[tuple[float, float]]]) -> Optional[Rect]:
    pts = [p for s in strokes for p in s]
    if not pts:
        return None
    x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
    y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
    return Rect(x0, y0, x1 - x0, y1 - y0)


def _tight_ink(ink_id: str, style: str, strokes: list[list[tuple[float, float]]], board: Rect,
               formula: Optional[str] = None, min_size: float = 2.0) -> Optional[dict[str, Any]]:
    """Build an ink whose region is the stroke bounding box (rounded outward), clamped to the board."""
    strokes = [simplify(s) for s in strokes if len(s) >= 1][:MAX_STROKES]
    box = _bbox(strokes)
    if box is None:
        return None
    pts = [[_round(p) for p in s] for s in strokes]
    x0, y0 = math.floor(box.minx * 10) / 10, math.floor(box.miny * 10) / 10
    x1, y1 = math.ceil(box.maxx * 10) / 10, math.ceil(box.maxy * 10) / 10
    w, h = max(min_size, x1 - x0), max(min_size, y1 - y0)
    region = Rect(x0, y0, w, h)
    if not board.contains(region):
        inter = region.intersection(board)
        if inter is None:
            return None
        region = inter
    ink: dict[str, Any] = {"ink_id": ink_id, "style": style, "region": region.to_list(), "strokes": pts}
    if formula:
        ink["formula"] = formula[:MAX_FORMULA]
    return ink if ink_is_valid(ink, board) else None


# ----------------------------------------------------------------- generators


def _rect_stroke(r: Rect) -> list[tuple[float, float]]:
    return [(r.minx, r.miny), (r.maxx, r.miny), (r.maxx, r.maxy), (r.minx, r.maxy), (r.minx, r.miny)]


def _ellipse_stroke(r: Rect, n: int = 32) -> list[tuple[float, float]]:
    return [
        (r.midx + r.w / 2 * math.cos(2 * math.pi * i / n), r.midy + r.h / 2 * math.sin(2 * math.pi * i / n))
        for i in range(n + 1)
    ]


def _triangle_stroke(r: Rect) -> list[tuple[float, float]]:
    return [(r.minx, r.maxy), (r.midx, r.miny), (r.maxx, r.maxy), (r.minx, r.maxy)]


def _chaikin(pts: list[tuple[float, float]], rounds: int = 2) -> list[tuple[float, float]]:
    for _ in range(rounds):
        out = [pts[0]]
        for a, b in zip(pts, pts[1:]):
            out.append((0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1]))
            out.append((0.25 * a[0] + 0.75 * b[0], 0.25 * a[1] + 0.75 * b[1]))
        out.append(pts[-1])
        pts = out
    return pts


def _brace_stroke(r: Rect) -> list[tuple[float, float]]:
    """A curly brace. Tall regions get ``}``, wide ones get a brace opening downwards."""
    # (u along the length 0..1, v across the width 0..1); v=0 is the open side.
    base = [(0.0, 0.0), (0.1, 0.5), (0.4, 0.5), (0.5, 1.0), (0.6, 0.5), (0.9, 0.5), (1.0, 0.0)]
    pts = _chaikin(base)
    if r.h >= r.w:
        return [(r.minx + v * r.w, r.miny + u * r.h) for u, v in pts]
    return [(r.minx + u * r.w, r.miny + v * r.h) for u, v in pts]


def _line_stroke(r: Rect) -> list[tuple[float, float]]:
    if r.w >= r.h:
        return [(r.minx, r.midy), (r.maxx, r.midy)]
    return [(r.midx, r.miny), (r.midx, r.maxy)]


def _wavy_underline(r: Rect) -> list[tuple[float, float]]:
    """A slightly uneven pen line along the bottom of ``r`` (deterministic)."""
    y = r.midy
    n = max(3, int(r.w // 40) + 2)
    return [(r.minx + r.w * i / (n - 1), y + (1.5 if i % 2 else -1.5)) for i in range(n)]


# ------------------------------------------------------------------ utilities


def _clamp_to(board: Rect, r: Rect) -> Optional[Rect]:
    return r.intersection(board)


def _text_size(text: str) -> tuple[float, float]:
    lines = text.split("\n") or [""]
    w = min(max(len(ln) for ln in lines) * CHAR_W + 2 * PAD, 460.0)
    return max(40.0, w), LINE_H * len(lines) + 2 * PAD


def _one_line(text: str) -> str:
    return "   ".join(part.strip() for part in text.split("\n") if part.strip())


def _point_ref(spec: Optional[str]) -> Optional[tuple[float, float]]:
    """An arrow end given as a point, ``"x,y"`` or ``"(x, y)"``."""
    if not spec:
        return None
    nums = _NUM.findall(spec)
    if len(nums) == 2 and re.fullmatch(r"\s*\(?\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*\)?\s*", spec):
        return float(nums[0]), float(nums[1])
    return None


def _ink_id(prefix: str, cmd_id: str, k: int | None = None) -> str:
    base = f"{prefix}-{cmd_id}" if prefix else cmd_id
    return base if k is None else f"{base}-{k}"


def _fallback_rect(placer: Placer, size: tuple[float, float]) -> Rect:
    """The board is full: stack at the bottom of the tutor strip at 40% size rather than lose the content."""
    area = placer.layout.strip_inner
    k = min(1.0, 0.4, area.w / max(1.0, size[0]), area.h / max(1.0, size[1]))
    w, h = max(8.0, size[0] * k), max(8.0, size[1] * k)
    return Rect(area.maxx - w, area.maxy - h, w, h)


def _place(cmd: BoardCmd, placer: Placer, size: tuple[float, float]) -> Rect:
    rect = placer.place(cmd, size)
    if rect is None:
        rect = _fallback_rect(placer, size)
        placer.record(cmd.id, rect)
    return rect


def _ref_rect(cmd: BoardCmd, placer: Placer) -> Optional[Rect]:
    ref = cmd.payload.ref or cmd.anchor.ref
    return placer.rect_of(ref) if ref else None


# --------------------------------------------------------------------- the ops


def _write(cmd: BoardCmd, placer: Placer, prefix: str) -> list[dict[str, Any]]:
    board = placer.layout.board_bounds
    p = cmd.payload
    raw = [[(float(x), float(y)) for x, y in s] for s in (p.strokes or []) if s]
    box = _bbox(raw)
    if box is not None:
        w = p.metrics.w if p.metrics and p.metrics.w > 0 else box.w
        h = p.metrics.h if p.metrics and p.metrics.h > 0 else box.h
        w, h = max(w, box.w, 1.0), max(h, box.h, 1.0)
        rect = _place(cmd, placer, (w, h))
        k = min(rect.w / w, rect.h / h, 3.0)
        # tight region: the strokes' own box after scaling, anchored at the placed corner
        tx, ty = rect.x - box.minx * k, rect.y - box.miny * k
        scaled = [[(x * k + tx, y * k + ty) for x, y in s] for s in raw]
        ink = _tight_ink(_ink_id(prefix, cmd.id), "write", scaled, board, formula=None)
        if ink is not None:
            placer.record(cmd.id, Rect.from_list(ink["region"]))
            return [ink]
    text = (p.text or "").strip()
    if not text:
        return []
    size = _text_size(text)
    rect = _place(cmd, placer, size)
    ink = {"ink_id": _ink_id(prefix, cmd.id), "style": "write", "region": rect.to_list(), "strokes": [],
           "formula": _one_line(text)[:MAX_FORMULA]}
    return [ink] if ink_is_valid(ink, board) else []


def _highlight(cmd: BoardCmd, placer: Placer, prefix: str) -> list[dict[str, Any]]:
    board = placer.layout.board_bounds
    ref = _ref_rect(cmd, placer)
    if ref is None:
        return []
    region = _clamp_to(board, ref.inset(-8, -6))
    if region is None or region.is_empty:
        return []
    ink = {"ink_id": _ink_id(prefix, cmd.id), "style": "highlight", "region": region.to_list(), "strokes": []}
    return [ink] if ink_is_valid(ink, board) else []


def _underline(cmd: BoardCmd, placer: Placer, prefix: str) -> list[dict[str, Any]]:
    board = placer.layout.board_bounds
    ref = _ref_rect(cmd, placer)
    if ref is None:
        return []
    band = _clamp_to(board, Rect(ref.minx, ref.maxy + 2, ref.w, 8))
    if band is None:
        return []
    ink = _tight_ink(_ink_id(prefix, cmd.id), "write", [_wavy_underline(band)], board)
    return [ink] if ink else []


_SHAPE_SIZE = {"rect": (160.0, 100.0), "circle": (110.0, 110.0), "triangle": (130.0, 110.0), "brace": (40.0, 120.0),
               "line": (160.0, 12.0), "box": (160.0, 100.0)}


def _shape(cmd: BoardCmd, placer: Placer, prefix: str) -> list[dict[str, Any]]:
    board = placer.layout.board_bounds
    kind = cmd.payload.kind if cmd.payload.kind in _SHAPE_SIZE else "rect"
    rect = _place(cmd, placer, _SHAPE_SIZE[kind])
    if kind == "circle":
        strokes, style = [_ellipse_stroke(rect)], "circle"
    elif kind == "triangle":
        strokes, style = [_triangle_stroke(rect)], "write"
    elif kind == "brace":
        strokes, style = [_brace_stroke(rect)], "write"
    elif kind == "line":
        strokes, style = [_line_stroke(rect)], "write"
    else:
        strokes, style = [_rect_stroke(rect)], "write"
    out = []
    ink = _tight_ink(_ink_id(prefix, cmd.id), style, strokes, board)
    if ink:
        out.append(ink)
    label = (cmd.payload.text or "").strip()
    if label and kind in ("rect", "circle", "triangle") and not rect.inset(6, 6).is_empty:
        region = rect.inset(6, 6)
        lab = {"ink_id": _ink_id(prefix, cmd.id, 1), "style": "write", "region": region.to_list(),
               "strokes": [], "formula": _one_line(label)[:MAX_FORMULA]}
        if ink_is_valid(lab, board):
            out.append(lab)
    return out


def _arrow_endpoints(a: Rect | tuple[float, float], b: Rect | tuple[float, float]) -> tuple[tuple[float, float], tuple[float, float]]:
    ca = (a.midx, a.midy) if isinstance(a, Rect) else a
    cb = (b.midx, b.midy) if isinstance(b, Rect) else b
    dx, dy = cb[0] - ca[0], cb[1] - ca[1]
    gap = 4.0

    def edge(r: Rect | tuple[float, float], c, toward_positive: bool, horizontal: bool):
        if not isinstance(r, Rect):
            return r
        if horizontal:
            return ((r.maxx + gap) if toward_positive else (r.minx - gap), r.midy)
        return (r.midx, (r.maxy + gap) if toward_positive else (r.miny - gap))

    horizontal = abs(dx) >= abs(dy)
    positive = (dx >= 0) if horizontal else (dy >= 0)
    start = edge(a, ca, positive, horizontal)
    end = edge(b, cb, not positive, horizontal)
    return start, end


def _arrow(cmd: BoardCmd, placer: Placer, prefix: str) -> list[dict[str, Any]]:
    board = placer.layout.board_bounds
    p = cmd.payload
    a = placer.rect_of(p.from_) or _point_ref(p.from_)
    b = placer.rect_of(p.to) or _point_ref(p.to)
    if a is None and b is not None and isinstance(b, Rect):  # only the target is known: come in from the left
        a = (b.minx - 60, b.midy)
    if b is None and a is not None and isinstance(a, Rect):  # only the source is known: point right
        b = (a.maxx + 60, a.midy)
    if a is None or b is None:
        return []
    start, end = _arrow_endpoints(a, b)
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    ca = (a.midx, a.midy) if isinstance(a, Rect) else a
    cb = (b.midx, b.midy) if isinstance(b, Rect) else b
    if length < 4 or dx * (cb[0] - ca[0]) + dy * (cb[1] - ca[1]) <= 0:  # too short, or pointing backwards
        return []
    ux, uy = dx / length, dy / length
    head = min(14.0, length * 0.4)
    left = (end[0] - head * (ux * math.cos(0.5) - uy * math.sin(0.5)), end[1] - head * (uy * math.cos(0.5) + ux * math.sin(0.5)))
    right = (end[0] - head * (ux * math.cos(0.5) + uy * math.sin(0.5)), end[1] - head * (uy * math.cos(0.5) - ux * math.sin(0.5)))
    ink = _tight_ink(_ink_id(prefix, cmd.id), "write", [[start, end], [left, end, right]], board)
    if ink:
        placer.record(cmd.id, Rect.from_list(ink["region"]))
        return [ink]
    return []


def _table(cmd: BoardCmd, placer: Placer, prefix: str) -> list[dict[str, Any]]:
    board = placer.layout.board_bounds
    rows = [[str(c) for c in r] for r in (cmd.payload.rows or []) if r]
    if not rows:
        return []
    cols = max(len(r) for r in rows)
    natural = (cols * 110.0, len(rows) * LINE_H)
    rect = _place(cmd, placer, natural)
    out: list[dict[str, Any]] = []
    cw, rh = rect.w / cols, rect.h / len(rows)
    if cw < 60.0:  # too narrow for a grid: one formula, rows separated by a semicolon
        text = "   ;   ".join(" | ".join(r) for r in rows)
        ink = {"ink_id": _ink_id(prefix, cmd.id), "style": "write", "region": rect.to_list(), "strokes": [],
               "formula": text[:MAX_FORMULA]}
        return [ink] if ink_is_valid(ink, board) else []
    grid: list[list[tuple[float, float]]] = []
    for r in range(len(rows) + 1):
        y = rect.miny + r * rh
        grid.append([(rect.minx, y), (rect.maxx, y)])
    for c in range(cols + 1):
        x = rect.minx + c * cw
        grid.append([(x, rect.miny), (x, rect.maxy)])
    g = _tight_ink(_ink_id(prefix, cmd.id, 0), "write", grid, board)
    if g:
        out.append(g)
    for i, row in enumerate(rows):
        region = Rect(rect.minx + 4, rect.miny + i * rh + 2, max(1.0, rect.w - 8), max(1.0, rh - 4))
        ink = {"ink_id": _ink_id(prefix, cmd.id, i + 1), "style": "write", "region": region.to_list(), "strokes": [],
               "formula": " | ".join(row)[:MAX_FORMULA]}
        if ink_is_valid(ink, board):
            out.append(ink)
    return out


_OPS = {
    "write": _write,
    "highlight": _highlight,
    "underline": _underline,
    "shape": _shape,
    "arrow": _arrow,
    "table": _table,
}


def cmd_to_inks(cmd: BoardCmd, placer: Placer, *, ink_id_prefix: str = "ink") -> list[dict[str, Any]]:
    """The v2 ink payloads for one command (see the module docstring). Never raises for odd input."""
    handler = _OPS.get(cmd.op)
    if handler is None:  # plot, focus, erase, group: not ink
        return []
    try:
        return handler(cmd, placer, ink_id_prefix)
    except (ValueError, TypeError, ZeroDivisionError):
        return []


__all__ = ["cmd_to_inks", "ink_is_valid", "ink_cap", "douglas_peucker", "simplify", "round_up"]
