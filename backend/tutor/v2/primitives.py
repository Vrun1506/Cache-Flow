"""Scene specs to the iPad's ``scene_preview`` payload (Interface.md v2).

The iPad draws a *static* set of approved primitives; it has no timeline. So this
module renders the **final frame** of a ``SceneSpec`` (the state after every
tween has finished) and drops the animation. If the iPad later grows a timeline
the original spec still carries it.

Output::

    {"scene_id": str, "title": str, "region": [x, y, w, h],
     "primitives": [{"kind": "axes"|"plot"|"point"|"label"|"arrow"|"segment"|"box"|"number_line",
                     "points": [[x, y], ...], "text"?: str}, ...]}

Conventions (these follow the mock's scenes in ``MockLessonScript.swift``):

* ``axes``: four points: x-axis start, x-axis end, y-axis start (bottom), y-axis end (top).
* ``plot``: a polyline. A curve with gaps becomes several ``plot`` primitives.
* ``point``: one point, optional ``text`` label. ``label``: one anchor point and the text.
* ``arrow``, ``segment``: two points. ``box``: two opposite corners.
* ``number_line``: two end points of the line; its ticks are ``segment`` + ``label`` primitives.

Limits enforced here (they match ``Bounds.isValid(scene)``): at most 64 primitives, 1024 points, text of at most
80 characters, every point inside the region. The scene is scaled uniformly to fit the region with a 10 point margin.
Circles are drawn as their bounding ``box`` and arc-shaped vectors as straight arrows: the primitive set has neither.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

from ..schemas.scene import SceneObject, SceneSpec
from ..schemas.text import trim_sentence
from .layout import Rect

APPROVED = {"axes", "plot", "point", "label", "arrow", "segment", "box", "number_line"}
MAX_PRIMITIVES = 64
MAX_POINTS = 1024
MAX_TEXT = 80
EDGE_TOL = 8.0
MARGIN = 10.0
CURVE_SAMPLES = 200
MAX_PLOT_PIECES = 4
_KNOWN = {"axes", "curve", "point", "vector", "shape", "label", "cell", "node", "edge", "bar", "tick"}

# lower number = more important when the 64-primitive budget forces drops
_PRIORITY = {"axes": 0, "plot": 0, "number_line": 0, "box": 1, "point": 1, "arrow": 1, "segment": 2, "label": 3}


@dataclass
class _Prim:
    kind: str
    points: list[tuple[float, float]]
    text: Optional[str] = None
    rank: int = 0  # extra demotion (tick marks are the first to go)


# ------------------------------------------------------------------ final frame


def _final_state(spec: SceneSpec) -> dict[str, dict[str, float]]:
    state: dict[str, dict[str, float]] = {}
    for o in spec.objects:
        state[o.id] = {"x": o.init.x, "y": o.init.y, "opacity": o.init.opacity, "scale": o.init.scale,
                       "progress": float("nan"), "value": float("nan"), "highlight": 0.0}
    for tw in sorted(spec.timeline, key=lambda t: (t.t1_ms, t.t0_ms)):
        st = state.get(tw.target)
        if st is not None:
            st[tw.prop] = float(tw.to)
    return state


def _num(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _text(v: Any) -> Optional[str]:
    if v is None:
        return None
    t = " ".join(str(v).split())
    if not t:
        return None
    return trim_sentence(t, MAX_TEXT)


# -------------------------------------------------------------------- mapping


class _Frame:
    """Maps scene canvas coordinates into the board region (uniform scale, centred, clamped)."""

    def __init__(self, region: Rect, cw: float, ch: float) -> None:
        self.region = region
        inner = region.inset(MARGIN, MARGIN)
        if inner.w <= 1 or inner.h <= 1:
            inner = region
        self.k = min(inner.w / max(cw, 1.0), inner.h / max(ch, 1.0))
        self.ox = inner.x + (inner.w - cw * self.k) / 2
        self.oy = inner.y + (inner.h - ch * self.k) / 2

    def pt(self, p: tuple[float, float]) -> list[float]:
        x = min(max(self.ox + p[0] * self.k, self.region.minx), self.region.maxx)
        y = min(max(self.oy + p[1] * self.k, self.region.miny), self.region.maxy)
        return [round(x, 1), round(y, 1)]


# ------------------------------------------------------------------ the curves


def _axes_of(spec: SceneSpec, ref: Any) -> Optional[SceneObject]:
    objs = {o.id: o for o in spec.objects}
    if ref in objs and objs[ref].type == "axes":
        return objs[ref]
    return next((o for o in spec.objects if o.type == "axes"), None)


def _curve_pieces(spec: SceneSpec, obj: SceneObject, st: dict[str, dict[str, float]]) -> list[list[tuple[float, float]]]:
    from ..scenes.expr import evaluate

    axes = _axes_of(spec, obj.props.get("axes"))
    if axes is None:
        return []
    ap = axes.props
    ax, ay = axes.init.x, axes.init.y
    aw, ah = _num(ap.get("w"), 800), _num(ap.get("h"), 420)
    xmin, xmax = _num(ap.get("x_min"), -10), _num(ap.get("x_max"), 10)
    ymin, ymax = _num(ap.get("y_min"), -10), _num(ap.get("y_max"), 10)
    if xmax <= xmin or ymax <= ymin or aw <= 0 or ah <= 0:
        return []
    cx0, cx1 = _num(obj.props.get("x_min"), xmin), _num(obj.props.get("x_max"), xmax)
    progress = st.get(obj.id, {}).get("progress", float("nan"))
    if not math.isnan(progress):
        cx1 = cx0 + max(0.0, min(1.0, progress)) * (cx1 - cx0)
    expr = obj.props.get("expr")
    if not isinstance(expr, str) or cx1 <= cx0:
        return []
    pieces: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []
    slack = 0.05 * (ymax - ymin)
    for i in range(CURVE_SAMPLES):
        x = cx0 + (cx1 - cx0) * i / (CURVE_SAMPLES - 1)
        try:
            y = evaluate(expr, x=x)
        except ValueError:
            return []
        if y is None or y < ymin - slack or y > ymax + slack:
            if len(cur) >= 2:
                pieces.append(cur)
            cur = []
            continue
        px = ax + (x - xmin) / (xmax - xmin) * aw
        py = ay + ah - (y - ymin) / (ymax - ymin) * ah
        cur.append((px, py))
    if len(cur) >= 2:
        pieces.append(cur)
    return pieces[:MAX_PLOT_PIECES]


def _curve_position(spec: SceneSpec, curve: SceneObject, progress: float) -> Optional[tuple[float, float]]:
    from ..scenes.expr import evaluate

    axes = _axes_of(spec, curve.props.get("axes"))
    expr = curve.props.get("expr")
    if axes is None or not isinstance(expr, str):
        return None
    ap = axes.props
    ax, ay = axes.init.x, axes.init.y
    aw, ah = _num(ap.get("w"), 800), _num(ap.get("h"), 420)
    xmin, xmax = _num(ap.get("x_min"), -10), _num(ap.get("x_max"), 10)
    ymin, ymax = _num(ap.get("y_min"), -10), _num(ap.get("y_max"), 10)
    if xmax <= xmin or ymax <= ymin:
        return None
    cx0, cx1 = _num(curve.props.get("x_min"), xmin), _num(curve.props.get("x_max"), xmax)
    x = cx0 + max(0.0, min(1.0, progress)) * (cx1 - cx0)
    try:
        y = evaluate(expr, x=x)
    except ValueError:
        return None
    if y is None:
        return None
    return ax + (x - xmin) / (xmax - xmin) * aw, ay + ah - (y - ymin) / (ymax - ymin) * ah


# --------------------------------------------------------------- object mapping


def _build(spec: SceneSpec) -> tuple[list[_Prim], bool]:
    """Primitives in canvas coordinates. The flag is False when an unknown object type was met."""
    st = _final_state(spec)
    objs = {o.id: o for o in spec.objects}
    prims: list[_Prim] = []
    ok = True

    def centre(o: SceneObject) -> tuple[float, float]:
        s = st[o.id]
        if o.type == "node":
            return s["x"], s["y"]
        return s["x"] + _num(o.props.get("w")) / 2, s["y"] + _num(o.props.get("h")) / 2

    for o in spec.objects:
        s = st[o.id]
        if o.type not in _KNOWN:
            ok = False
            continue
        if s["opacity"] < 0.05 or o.id == "title":
            continue
        p, x, y = o.props, s["x"], s["y"]
        if o.type == "axes":
            aw, ah = _num(p.get("w"), 800), _num(p.get("h"), 420)
            xmin, xmax = _num(p.get("x_min"), -10), _num(p.get("x_max"), 10)
            ymin, ymax = _num(p.get("y_min"), -10), _num(p.get("y_max"), 10)
            y0 = y + ah - (0 - ymin) / (ymax - ymin) * ah if ymax > ymin and ymin <= 0 <= ymax else y + ah
            x0 = x + (0 - xmin) / (xmax - xmin) * aw if xmax > xmin and xmin <= 0 <= xmax else x
            prims.append(_Prim("axes", [(x, y0), (x + aw, y0), (x0, y + ah), (x0, y)]))
            if _text(p.get("x_label")):
                prims.append(_Prim("label", [(x + aw - 10, min(y0 + 26, y + ah + 26))], _text(p.get("x_label"))))
            if _text(p.get("y_label")):
                prims.append(_Prim("label", [(min(x0 + 14, x + aw), y - 12)], _text(p.get("y_label"))))
        elif o.type == "curve":
            for piece in _curve_pieces(spec, o, st):
                prims.append(_Prim("plot", piece))
        elif o.type == "point":
            pos = (x, y)
            follow = objs.get(str(p.get("follow"))) if p.get("follow") else None
            if follow is not None and follow.type == "curve":
                prog = s["progress"] if not math.isnan(s["progress"]) else 0.0
                pos = _curve_position(spec, follow, prog) or pos
            prims.append(_Prim("point", [pos], _text(p.get("label"))))
        elif o.type == "vector":
            prims.append(_Prim("arrow", [(x, y), (x + _num(p.get("dx")), y + _num(p.get("dy")))], _text(p.get("label"))))
        elif o.type == "shape":
            w, h = _num(p.get("w")), _num(p.get("h"))
            kind = p.get("kind")
            if kind == "line":
                if spec.kind == "number_line" and o.id == "line":
                    prims.append(_Prim("number_line", [(x, y), (x + w, y + h)]))
                else:
                    prims.append(_Prim("segment", [(x, y), (x + w, y + h)]))
            else:  # rect; a circle is drawn as its bounding box (no circle primitive)
                prims.append(_Prim("box", [(x, y), (x + max(w, 1.0), y + max(h, 1.0))]))
        elif o.type == "label":
            t = _text(p.get("text"))
            if t:
                prims.append(_Prim("label", [(x, y)], t))
        elif o.type == "cell":
            w, h = _num(p.get("w"), 70), _num(p.get("h"), 70)
            prims.append(_Prim("box", [(x, y), (x + w, y + h)]))
            if _text(p.get("text")):
                prims.append(_Prim("label", [(x + w / 2, y + h / 2)], _text(p.get("text"))))
        elif o.type == "node":
            prims.append(_Prim("point", [(x, y)], _text(p.get("label"))))
        elif o.type == "edge":
            a, b = objs.get(str(p.get("from"))), objs.get(str(p.get("to")))
            if a is not None and b is not None:
                prims.append(_Prim("segment", [centre(a), centre(b)]))
        elif o.type == "bar":
            w, mh, mv = _num(p.get("w"), 60), _num(p.get("max_h"), 300), _num(p.get("max_value"), 1.0) or 1.0
            val = s["value"] if not math.isnan(s["value"]) else 0.0
            h = max(1.0, max(0.0, min(val / mv, 1.0)) * mh)
            prims.append(_Prim("box", [(x, y - h), (x + w, y)]))
            if _text(p.get("text")):
                prims.append(_Prim("label", [(x + w / 2, y - h - 12)], _text(p.get("text"))))
        elif o.type == "tick":
            h = _num(p.get("h"), 16)
            prims.append(_Prim("segment", [(x, y - h / 2), (x, y + h / 2)], rank=2))
            if _text(p.get("label")):
                prims.append(_Prim("label", [(x, y + h / 2 + 16)], _text(p.get("label")), rank=1))
    return prims, ok


def _card(spec: SceneSpec) -> list[_Prim]:
    """A concept-card style fallback: a box and the text lines of the scene."""
    lines: list[str] = []
    for o in spec.objects:
        for key in ("text", "label"):
            t = _text(o.props.get(key)) if isinstance(o.props, dict) else None
            if t and t not in lines and o.id != "title":
                lines.append(t)
    lines = lines[:5] or ["Let's look at this idea step by step."]
    prims = [_Prim("box", [(60.0, 100.0), (940.0, 100.0 + 85.0 * len(lines) + 30.0)])]
    for i, t in enumerate(lines):
        prims.append(_Prim("label", [(90.0, 150.0 + 85.0 * i)], t))
    return prims


def _wrap_card(prims: list[_Prim]) -> list[_Prim]:
    """For diagram scenes (concept cards): put a box around the label lines."""
    labels = [p for p in prims if p.kind == "label"]
    if not labels or any(p.kind == "box" for p in prims):
        return prims
    xs = [p.points[0][0] for p in labels]
    ys = [p.points[0][1] for p in labels]
    box = _Prim("box", [(min(xs) - 30, min(ys) - 45), (min(max(xs) + 600, 960), max(ys) + 45)])
    return [box, *prims]


# ----------------------------------------------------------------------- limits


def _enforce(prims: list[_Prim]) -> list[_Prim]:
    prims = [p for p in prims if p.points and p.kind in APPROVED]
    # points budget: thin the plots first, then drop whole primitives
    total = sum(len(p.points) for p in prims)
    if total > MAX_POINTS:
        plots = [p for p in prims if p.kind == "plot"]
        spare = MAX_POINTS - (total - sum(len(p.points) for p in plots))
        if plots and spare >= 2 * len(plots):
            keep = max(2, spare // len(plots))
            for p in plots:
                if len(p.points) > keep:
                    step = (len(p.points) - 1) / (keep - 1)
                    p.points = [p.points[round(i * step)] for i in range(keep)]
    if len(prims) > MAX_PRIMITIVES:
        order = sorted(range(len(prims)), key=lambda i: (_PRIORITY.get(prims[i].kind, 3) + prims[i].rank, i))
        keep_idx = set(order[:MAX_PRIMITIVES])
        prims = [p for i, p in enumerate(prims) if i in keep_idx]
    while sum(len(p.points) for p in prims) > MAX_POINTS and prims:
        worst = max(range(len(prims)), key=lambda i: (_PRIORITY.get(prims[i].kind, 3) + prims[i].rank, i))
        prims.pop(worst)
    return prims


# ------------------------------------------------------------------- the entry


def scene_to_primitives(spec: SceneSpec, region: Rect) -> dict[str, Any]:
    """The ``scene_preview`` payload for ``spec`` drawn into ``region`` (board space). Never raises."""
    try:
        prims, ok = _build(spec)
        if not ok or not prims:
            prims = _card(spec)
        elif spec.kind == "diagram":
            prims = _wrap_card(prims)
    except Exception:  # a malformed spec must never block a lesson
        prims = _card(spec)
    prims = _enforce(prims)
    frame = _Frame(region, float(spec.canvas.w), float(spec.canvas.h))
    out = []
    for p in prims:
        item: dict[str, Any] = {"kind": p.kind, "points": [frame.pt(q) for q in p.points]}
        if p.text:
            item["text"] = p.text
        out.append(item)
    return {"scene_id": str(spec.id), "title": _text(spec.title) or "", "region": region.to_list(), "primitives": out}


def plot_cmd_to_scene(cmd, region: Rect) -> Optional[dict[str, Any]]:
    """A ``plot`` board command (expr, x range, optional y range) as a scene payload, or None if it can't be drawn."""
    from ..scenes.templates import TemplateError, fn_plot
    from ..schemas.scene import SceneParams

    p = cmd.payload
    if not p.expr:
        return None
    try:
        params = SceneParams(
            expr=p.expr,
            x_min=p.x[0] if p.x and len(p.x) >= 1 else None,
            x_max=p.x[1] if p.x and len(p.x) >= 2 else None,
            y_min=p.y[0] if p.y and len(p.y) >= 1 else None,
            y_max=p.y[1] if p.y and len(p.y) >= 2 else None,
        )
        spec = fn_plot(p.text or "Graph", params, [])
    except (TemplateError, ValueError):
        return None
    payload = scene_to_primitives(spec, region)
    payload["scene_id"] = f"scene-{cmd.id}"
    return payload


# ---------------------------------------------------------------------- validity


def _finite(*vals: float) -> bool:
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in vals)


def scene_is_valid(scene: dict[str, Any], board: Optional[Rect]) -> bool:
    """Port of ``Bounds.isValid(_ scene: TutorScene, within:)``."""
    try:
        region = Rect.from_list(scene["region"])
        prims = scene["primitives"]
    except (KeyError, TypeError, ValueError):
        return False
    if len(prims) > MAX_PRIMITIVES or sum(len(p.get("points", [])) for p in prims) > MAX_POINTS:
        return False
    if any(p.get("kind") not in APPROVED or len(p.get("text") or "") > MAX_TEXT for p in prims):
        return False
    if not _finite(region.x, region.y, region.w, region.h) or region.w <= 0 or region.h <= 0:
        return False
    if board is not None and not board.contains(region):
        return False
    area = region.inset(-EDGE_TOL, -EDGE_TOL)
    for p in prims:
        for pt in p.get("points", []):
            if len(pt) != 2 or not _finite(pt[0], pt[1]) or not area.contains_point(pt[0], pt[1]):
                return False
    return True


__all__ = ["scene_to_primitives", "plot_cmd_to_scene", "scene_is_valid", "APPROVED"]
