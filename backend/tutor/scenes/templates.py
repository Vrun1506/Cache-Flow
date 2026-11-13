"""Code-computed scene templates (final-plan.md §16.7).

The LLM picks a template and fills flat parameters. Everything that moves is
computed here: trajectories, algorithm step sequences, layouts, timing. Each
builder clamps its inputs so the result fits the 1000 x 600 canvas and the
object/tween limits. A builder raises ``TemplateError`` only when a parameter
it cannot do without is missing or unusable (no expression, no array). The
``concept_card`` builder never raises.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Callable

from ..schemas.scene import (
    CANVAS_H,
    CANVAS_W,
    MAX_DURATION_MS,
    Cue,
    Init,
    SceneObject,
    SceneParams,
    SceneSpec,
    Tween,
)
from ..schemas.text import trim_sentence
from .expr import evaluate, normalise


class TemplateError(ValueError):
    """The parameters cannot produce this template."""


AX, AY, AW, AH = 100, 80, 800, 420  # the shared plot area
STEP_MS = 700


# ------------------------------------------------------------------ helpers


def _fmt(v: float) -> str:
    if abs(v - round(v)) < 1e-9 and abs(v) < 1e12:
        return str(int(round(v)))
    return f"{v:.4g}"


def _clean(text: object, limit: int) -> str:
    s = re.sub(r"[\x00-\x1f\x7f]+", " ", str(text))
    return trim_sentence(" ".join(s.split()), limit)


def _finite(values: list) -> list[float]:
    out = []
    for v in values:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            out.append(f)
    return out


def _clamp(v: float | None, lo: float, hi: float, default: float) -> float:
    if v is None or not math.isfinite(v):
        return default
    return max(lo, min(hi, float(v)))


def _linspace(lo: float, hi: float, n: int) -> list[float]:
    return [lo + (hi - lo) * i / (n - 1) for i in range(n)]


def _range(lo: float | None, hi: float | None, dlo: float, dhi: float) -> tuple[float, float]:
    if lo is None or hi is None or not (math.isfinite(lo) and math.isfinite(hi)):
        return dlo, dhi
    if lo > hi:
        lo, hi = hi, lo
    if hi - lo < 1e-9:
        return dlo, dhi
    lim = 1e6
    return max(lo, -lim), min(hi, lim)


class _Builder:
    def __init__(self) -> None:
        self.objects: list[SceneObject] = []
        self.tweens: list[Tween] = []
        self.auto_cues: list[tuple[int, str]] = []

    def add(self, id: str, type: str, x: float = 0.0, y: float = 0.0, opacity: float = 1.0, **props) -> None:
        self.objects.append(
            SceneObject(id=id, type=type, props=props, init=Init(x=round(x, 2), y=round(y, 2), opacity=opacity))
        )

    def title(self, text: str) -> None:
        self.add("title", "label", CANVAS_W / 2, 36, text=_clean(text, 60), size=28, anchor="center")

    def tween(self, target: str, prop: str, t0: float, t1: float, to: float, frm: float | None = None,
              ease: str = "inOut") -> None:
        t0 = int(max(0, t0))
        t1 = int(max(t0 + 1, t1))
        self.tweens.append(
            Tween(t0_ms=t0, t1_ms=t1, target=target, prop=prop, **{"from": frm}, to=round(float(to), 4), ease=ease)
        )

    def fade_in(self, target: str, t0: float, dur: float = 350) -> None:
        self.tween(target, "opacity", t0, t0 + dur, 1.0, 0.0)

    def cue(self, at_ms: float, text: str) -> None:
        self.auto_cues.append((int(at_ms), _clean(text, 120)))

    def finish(self, template: str, kind: str, title: str, captions: list[str], tail: int = 800) -> SceneSpec:
        end = max((t.t1_ms for t in self.tweens), default=0) + tail
        duration = max(1500, min(end, MAX_DURATION_MS))
        caps = [_clean(c, 120) for c in captions if str(c).strip()][:6]
        if caps:
            n = len(caps)
            cues = [Cue(at_ms=int(duration * 0.85 * i / n), caption=c) for i, c in enumerate(caps)]
        else:
            auto = [(min(t, duration), c) for t, c in sorted(self.auto_cues, key=lambda tc: tc[0]) if c]
            if len(auto) > 6:
                auto = [auto[round(i * (len(auto) - 1) / 5)] for i in range(6)]
            cues = [Cue(at_ms=t, caption=c) for t, c in auto]
        sid = f"scene-{template}-" + hashlib.sha1(f"{template}|{title}".encode()).hexdigest()[:6]
        return SceneSpec(
            id=sid, title=_clean(title, 60) or "Animation", kind=kind, duration_ms=duration,
            objects=self.objects, timeline=self.tweens, cues=cues,
        )


# --------------------------------------------------------------------- plots


def _plot_px(x: float, y: float, xr: tuple[float, float], yr: tuple[float, float]) -> tuple[float, float]:
    return (
        AX + (x - xr[0]) / (xr[1] - xr[0]) * AW,
        AY + AH - (y - yr[0]) / (yr[1] - yr[0]) * AH,
    )


def _axes(b: _Builder, xr, yr, x_label: str | None, y_label: str | None) -> None:
    b.add("axes", "axes", AX, AY, opacity=0.0, w=AW, h=AH, x_min=xr[0], x_max=xr[1], y_min=yr[0], y_max=yr[1],
          x_label=_clean(x_label or "x", 30), y_label=_clean(y_label or "y", 30))
    b.fade_in("axes", 0, 400)


def fn_plot(title: str, p: SceneParams, captions: list[str]) -> SceneSpec:
    if not p.expr:
        raise TemplateError("fn_plot needs an expression")
    expr = normalise(p.expr)
    xr = _range(p.x_min, p.x_max, -10.0, 10.0)
    samples = [evaluate(expr, x=x) for x in _linspace(xr[0], xr[1], 101)]
    ys = sorted(v for v in samples if v is not None)
    if len(ys) < 26:
        raise TemplateError("expression is undefined over most of the range")
    yr = None
    if p.y_min is not None and p.y_max is not None:
        yr = _range(p.y_min, p.y_max, 0.0, 0.0)
        yr = yr if yr[1] - yr[0] > 1e-9 else None
    if yr is None:
        lo, hi = ys[int(0.01 * (len(ys) - 1))], ys[int(0.99 * (len(ys) - 1))]
        if hi - lo < 1e-9:
            lo, hi = lo - 1, hi + 1
        pad = 0.08 * (hi - lo)
        yr = (lo - pad, hi + pad)
    b = _Builder()
    b.title(title)
    _axes(b, xr, yr, p.x_label, p.y_label)
    b.add("curve", "curve", expr=expr, axes="axes", x_min=xr[0], x_max=xr[1], color="agent")
    b.tween("curve", "progress", 400, 2800, 1.0, 0.0, ease="linear")
    b.cue(0, "Here is the graph being drawn.")
    k = 0
    for x in _finite(p.points):
        y = evaluate(expr, x=x)
        if y is None or not (xr[0] <= x <= xr[1]) or not (yr[0] <= y <= yr[1]) or k >= 4:
            continue
        px, py = _plot_px(x, y, xr, yr)
        b.add(f"pt{k}", "point", px, py, opacity=0.0, radius=7, color="accent", label=f"({_fmt(x)}, {_fmt(y)})")
        b.fade_in(f"pt{k}", 400 + 2400 * (x - xr[0]) / (xr[1] - xr[0]))
        k += 1
    if k:
        b.cue(2900, "Look at the marked points.")
    return b.finish("fn_plot", "plot", title, captions)


def projectile_stats(v0: float, angle_deg: float, g: float) -> dict[str, float]:
    a = math.radians(angle_deg)
    return {
        "range": v0 * v0 * math.sin(2 * a) / g,
        "peak": (v0 * math.sin(a)) ** 2 / (2 * g),
        "time": 2 * v0 * math.sin(a) / g,
    }


def _decimal(v: float) -> str:
    from decimal import Decimal

    return format(Decimal(repr(float(f"{v:.10g}"))), "f")


def projectile(title: str, p: SceneParams, captions: list[str]) -> SceneSpec:
    v0 = _clamp(p.v0, 1.0, 200.0, 20.0)
    angle = _clamp(p.angle, 5.0, 85.0, 45.0)
    g = _clamp(p.g, 0.5, 30.0, 9.81)
    s = projectile_stats(v0, angle, g)
    rng, peak, time_s = s["range"], s["peak"], s["time"]
    a = math.radians(angle)
    # y(x) = x tan(a) - g x^2 / (2 v0^2 cos^2(a)); with constant horizontal speed x is linear in time.
    expr = normalise(f"{_decimal(math.tan(a))}*x - {_decimal(g / (2 * v0 * v0 * math.cos(a) ** 2))}*x^2")
    xr, yr = (0.0, rng * 1.08), (0.0, peak * 1.35)
    dur = max(3000.0, min(8000.0, time_s * 600))
    t0 = 500.0
    b = _Builder()
    b.title(title)
    _axes(b, xr, yr, p.x_label or "distance (m)", p.y_label or "height (m)")
    b.add("curve", "curve", expr=expr, axes="axes", x_min=0.0, x_max=rng, color="agent")
    b.tween("curve", "progress", t0, t0 + dur, 1.0, 0.0, ease="linear")
    b.add("ball", "point", AX, AY + AH, radius=9, color="accent", follow="curve")
    b.tween("ball", "progress", t0, t0 + dur, 1.0, 0.0, ease="linear")
    px, py = _plot_px(rng / 2, peak, xr, yr)
    b.add("peak", "point", px, py, opacity=0.0, radius=6, color="warn", label=f"peak {_fmt(peak)} m")
    b.fade_in("peak", t0 + dur / 2 - 100)
    rx, _ = _plot_px(rng, 0, xr, yr)
    b.add("range", "label", rx, AY + AH + 28, opacity=0.0, text=f"range {_fmt(rng)} m", size=20, anchor="center")
    b.fade_in("range", t0 + dur - 100)
    b.cue(t0, f"Launched at {_fmt(v0)} m/s and {_fmt(angle)} degrees.")
    b.cue(t0 + dur / 2, "At the top the vertical speed is zero.")
    b.cue(t0 + dur, f"It lands {_fmt(rng)} m away.")
    return b.finish("projectile", "motion", title, captions)


# -------------------------------------------------------------------- arrays

PITCH, CELLW, CELL_Y = 80, 70, 300
ALGOS = ("linear_search", "binary_search", "bubble_sort", "two_pointers")


def bubble_trace(values: list[float]) -> tuple[list[float], list[tuple[str, int]]]:
    """Bubble sort with early exit. Ops are ("cmp", i) and ("swap", i) on positions i, i+1."""
    a = list(values)
    ops: list[tuple[str, int]] = []
    for end in range(len(a) - 1, 0, -1):
        swapped = False
        for i in range(end):
            ops.append(("cmp", i))
            if a[i] > a[i + 1]:
                a[i], a[i + 1] = a[i + 1], a[i]
                ops.append(("swap", i))
                swapped = True
        if not swapped:
            break
    return a, ops


def binary_trace(values: list[float], target: float) -> tuple[list[tuple[int, int, int]], int | None]:
    """Binary search over sorted ``values``: ((lo, hi, mid) per probe, found index or None)."""
    lo, hi = 0, len(values) - 1
    probes: list[tuple[int, int, int]] = []
    while lo <= hi:
        mid = (lo + hi) // 2
        probes.append((lo, hi, mid))
        if values[mid] == target:
            return probes, mid
        if values[mid] < target:
            lo = mid + 1
        else:
            hi = mid - 1
    return probes, None


def two_pointer_trace(values: list[float], target: float) -> tuple[list[tuple[int, int]], tuple[int, int] | None]:
    """Pair-sum search over sorted ``values``: ((left, right) per probe, found pair or None)."""
    lo, hi = 0, len(values) - 1
    probes: list[tuple[int, int]] = []
    while lo < hi:
        probes.append((lo, hi))
        s = values[lo] + values[hi]
        if s == target:
            return probes, (lo, hi)
        if s < target:
            lo += 1
        else:
            hi -= 1
    return probes, None


def _algo(name: str | None) -> str:
    n = (name or "linear_search").lower()
    if n.strip() in ("sort", "sorting"):
        return "bubble_sort"
    for key, algo in (("linear", "linear_search"), ("binary", "binary_search"), ("bubble", "bubble_sort"),
                      ("two", "two_pointers"), ("pointer", "two_pointers")):
        if key in n:
            return algo
    raise TemplateError(f"unknown algorithm {name!r}")


def _cells(b: _Builder, vals: list[float]) -> float:
    n = len(vals)
    x0 = (CANVAS_W - (n * PITCH - (PITCH - CELLW))) / 2
    for i, v in enumerate(vals):
        b.add(f"c{i}", "cell", x0 + i * PITCH, CELL_Y, text=_fmt(v), w=CELLW, h=CELLW, index=i)
    return x0


def _cursor(b: _Builder, id: str, x0: float, index: int, span: int = 1, color: str = "accent") -> None:
    b.add(id, "shape", x0 + index * PITCH - 4, CELL_Y - 8, kind="rect", w=CELLW + 8 + (span - 1) * PITCH, h=CELLW + 16,
          color=color)


def array_walk(title: str, p: SceneParams, captions: list[str]) -> SceneSpec:
    algo = _algo(p.algo)
    vals = _finite(p.values)
    if len(vals) < 2:
        raise TemplateError("array_walk needs at least two values")
    b = _Builder()
    b.title(title)
    if algo == "bubble_sort":
        vals = vals[:5]  # keeps worst-case swaps inside the 40-tween budget
        final, ops = bubble_trace(vals)
        x0 = _cells(b, vals)
        _cursor(b, "cursor", x0, 0, span=2)
        order = list(range(len(vals)))  # cell index at each position
        t, at = 300.0, 0
        for op, i in ops:
            if op == "cmp":
                if i != at:
                    b.tween("cursor", "x", t, t + 400, x0 + i * PITCH - 4, x0 + at * PITCH - 4)
                    at = i
                b.cue(t, f"Compare {_fmt(vals[order[i]])} and {_fmt(vals[order[i + 1]])}.")
                t += STEP_MS
            else:
                a, c = order[i], order[i + 1]
                b.tween(f"c{a}", "x", t, t + 500, x0 + (i + 1) * PITCH, x0 + i * PITCH)
                b.tween(f"c{c}", "x", t, t + 500, x0 + i * PITCH, x0 + (i + 1) * PITCH)
                order[i], order[i + 1] = c, a
                b.cue(t, "Out of order, so swap them.")
                t += 600
        for k in range(len(vals)):
            b.tween(f"c{k}", "highlight", t, t + 300, 1.0, 0.0)
        b.cue(t, "Sorted: " + ", ".join(_fmt(v) for v in final) + ".")
        return b.finish("array_walk", "array", title, captions)

    vals = vals[:12]
    if algo != "linear_search":
        vals = sorted(vals)
    n = len(vals)
    default_target = {"linear_search": vals[(2 * n) // 3], "binary_search": vals[n // 4],
                      "two_pointers": (vals[1] + vals[-2]) if n >= 4 else vals[0] + vals[-1]}[algo]
    target = float(p.target) if p.target is not None and math.isfinite(p.target) else default_target
    x0 = _cells(b, vals)
    b.add("target", "label", CANVAS_W / 2, 190, text=("Target sum: " if algo == "two_pointers" else "Looking for ") + _fmt(target),
          size=24, anchor="center")
    t = 300.0

    if algo == "linear_search":
        _cursor(b, "cursor", x0, 0)
        found = None
        for i, v in enumerate(vals):
            if i:
                b.tween("cursor", "x", t, t + 400, x0 + i * PITCH - 4, x0 + (i - 1) * PITCH - 4)
            b.cue(t, f"Check {_fmt(v)}.")
            t += STEP_MS
            if v == target:
                found = i
                break
        if found is not None:
            b.tween(f"c{found}", "highlight", t - 300, t, 1.0, 0.0)
            b.cue(t, f"Found {_fmt(target)} at position {found}.")
        else:
            b.cue(t, f"{_fmt(target)} is not in the list.")
    elif algo == "binary_search":
        probes, found = binary_trace(vals, target)
        _cursor(b, "cursor", x0, probes[0][2])
        b.cue(t, "The list is sorted, so start in the middle.")
        prev = probes[0][2]
        for step, (lo, hi, mid) in enumerate(probes):
            if step:
                b.tween("cursor", "x", t, t + 400, x0 + mid * PITCH - 4, x0 + prev * PITCH - 4)
            prev = mid
            b.cue(t, f"Look at {_fmt(vals[mid])}.")
            t += STEP_MS
            if vals[mid] == target:
                b.tween(f"c{mid}", "highlight", t - 300, t, 1.0, 0.0)
                b.cue(t, f"Found {_fmt(target)} at position {mid}.")
                break
            dim = range(lo, mid + 1) if vals[mid] < target else range(mid, hi + 1)
            for k in dim:
                b.tween(f"c{k}", "opacity", t - 300, t, 0.25, 1.0)
            b.cue(t, "Too small, so drop the left half." if vals[mid] < target else "Too big, so drop the right half.")
        else:
            b.cue(t + 500, f"{_fmt(target)} is not in the list.")
    else:  # two_pointers
        probes, pair = two_pointer_trace(vals, target)
        _cursor(b, "left", x0, 0, color="accent")
        _cursor(b, "right", x0, n - 1, color="warn")
        pos = {"left": 0, "right": n - 1}
        for lo, hi in probes:
            for name, idx in (("left", lo), ("right", hi)):
                if idx != pos[name]:
                    b.tween(name, "x", t - 300, t, x0 + idx * PITCH - 4, x0 + pos[name] * PITCH - 4)
                    pos[name] = idx
            b.cue(t, f"{_fmt(vals[lo])} + {_fmt(vals[hi])} = {_fmt(vals[lo] + vals[hi])}.")
            t += STEP_MS
        if pair:
            for k in pair:
                b.tween(f"c{k}", "highlight", t - 300, t, 1.0, 0.0)
            b.cue(t, f"Found the pair {_fmt(vals[pair[0]])} and {_fmt(vals[pair[1]])}.")
        else:
            b.cue(t, "No pair adds up to the target.")
    return b.finish("array_walk", "array", title, captions)


# -------------------------------------------------------------------- graphs


def traverse(nodes: list[str], edges: list[tuple[int, int]], algo: str, start: int = 0
             ) -> tuple[list[int], list[tuple[int, int]]]:
    """Visit order and tree edges (parent, child) for bfs or dfs. Neighbours go in node order."""
    adj: dict[int, list[int]] = {i: [] for i in range(len(nodes))}
    for a, c in edges:
        adj[a].append(c)
        adj[c].append(a)
    for k in adj:
        adj[k].sort()
    order: list[int] = []
    tree: list[tuple[int, int]] = []
    seen = {start}
    if algo == "bfs":
        queue = [start]
        while queue:
            cur = queue.pop(0)
            order.append(cur)
            for nxt in adj[cur]:
                if nxt not in seen:
                    seen.add(nxt)
                    tree.append((cur, nxt))
                    queue.append(nxt)
    else:
        def visit(cur: int) -> None:
            order.append(cur)
            for nxt in adj[cur]:
                if nxt not in seen:
                    seen.add(nxt)
                    tree.append((cur, nxt))
                    visit(nxt)

        visit(start)
    return order, tree


def graph_traverse(title: str, p: SceneParams, captions: list[str]) -> SceneSpec:
    names: list[str] = []
    for n in p.nodes:
        n = _clean(n, 8)
        if n and n not in names:
            names.append(n)
    names = names[:8]
    if len(names) < 2:
        raise TemplateError("graph_traverse needs at least two nodes")
    index = {n: i for i, n in enumerate(names)}
    edges: list[tuple[int, int]] = []
    for raw in p.edges:
        parts = re.split(r"\s*(?:->|–|—|-)\s*", str(raw).strip(), maxsplit=1)
        if len(parts) != 2:
            continue
        a, c = index.get(parts[0].strip()), index.get(parts[1].strip())
        if a is None or c is None or a == c:
            continue
        e = (min(a, c), max(a, c))
        if e not in edges:
            edges.append(e)
    edges = edges[:12]
    if not edges:
        raise TemplateError("graph_traverse needs at least one valid edge")
    algo = "dfs" if "dfs" in (p.algo or "").lower() or "depth" in (p.algo or "").lower() else "bfs"
    start = index.get(_clean(p.start, 8), 0) if p.start else 0
    order, tree = traverse(names, edges, algo, start)

    b = _Builder()
    b.title(title)
    n = len(names)
    for i, name in enumerate(names):
        ang = -math.pi / 2 + 2 * math.pi * i / n
        b.add(f"n{i}", "node", 500 + 230 * math.cos(ang), 330 + 200 * math.sin(ang), label=name, radius=30)
    for a, c in edges:
        b.add(f"e{a}_{c}", "edge", opacity=0.45, **{"from": f"n{a}", "to": f"n{c}"}, color="agent")
    t = 500.0
    parent = {c: (a, c) for a, c in tree}
    b.cue(0, f"Start at {names[order[0]]} and explore with {'breadth' if algo == 'bfs' else 'depth'}-first search.")
    for k, node in enumerate(order):
        if node in parent:
            a, c = parent[node]
            b.tween(f"e{min(a, c)}_{max(a, c)}", "highlight", t - 300, t, 1.0, 0.0)
        b.tween(f"n{node}", "highlight", t, t + 300, 1.0, 0.0)
        b.cue(t, f"Visit {names[node]}.")
        t += STEP_MS + 100
    return b.finish("graph_traverse", "graph", title, captions)


# ------------------------------------------------- number line, bars, timeline


def _nice_step(raw: float) -> float:
    raw = max(raw, 1e-9)
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if m * mag >= raw:
            return m * mag
    return 10 * mag


def number_line(title: str, p: SceneParams, captions: list[str]) -> SceneSpec:
    points = _finite(p.points)[:4]
    jumps = [j for j in _finite(p.jumps) if j != 0 and abs(j) <= 1e6][:5]
    if not points and not jumps:
        raise TemplateError("number_line needs points or jumps")
    start = points[0] if points else 0.0
    stops = [start]
    for j in jumps:
        stops.append(stops[-1] + j)
    every = stops + points
    lo, hi = math.floor(min(every)), math.ceil(max(every))
    if hi - lo < 1:
        lo, hi = lo - 1, hi + 1
    lo, hi = lo - 1, hi + 1
    step = _nice_step((hi - lo) / 10)
    lo, hi = math.floor(lo / step) * step, math.ceil(hi / step) * step

    def px(v: float) -> float:
        return 60 + (v - lo) / (hi - lo) * 880

    base = 330
    b = _Builder()
    b.title(title)
    b.add("line", "shape", 60, base, kind="line", w=880, h=0, color="agent")
    first = math.ceil(lo / step - 1e-9) * step
    for k in range(11):
        v = first + k * step
        if v > hi + 1e-9:
            break
        b.add(f"tk{k}", "tick", px(v), base, label=_fmt(v), h=16)
    for k, v in enumerate(points):
        b.add(f"mk{k}", "point", px(v), base, opacity=0.0, radius=6, color="accent", label=_fmt(v))
        b.fade_in(f"mk{k}", 300 + 150 * k)
    b.cue(0, "Here is the number line.")
    t = 1000.0
    if jumps:
        b.add("hopper", "point", px(start), base, radius=9, color="warn")
        for k, j in enumerate(jumps):
            a, c = px(stops[k]), px(stops[k + 1])
            b.add(f"jp{k}", "vector", a, base, opacity=1.0, dx=round(c - a, 2), dy=0, arc=True,
                  label=("+" if j > 0 else "") + _fmt(j), color="accent")
            b.tween(f"jp{k}", "progress", t, t + 600, 1.0, 0.0)
            b.tween("hopper", "x", t, t + 600, c, a)
            b.cue(t, f"Jump {('+' if j > 0 else '')}{_fmt(j)} to land on {_fmt(stops[k + 1])}.")
            t += 800
    return b.finish("number_line", "number_line", title, captions)


def bar_compare(title: str, p: SceneParams, captions: list[str]) -> SceneSpec:
    values = [max(v, 0.0) for v in _finite(p.values)][:6]
    if not values:
        raise TemplateError("bar_compare needs values")
    labels = [_clean(l, 16) for l in p.labels][:6]
    labels += [chr(65 + i) for i in range(len(labels), len(values))]
    top = max(values) or 1.0
    n = len(values)
    w = min(110, int(800 / n) - 20)
    pitch = 800 / n
    base, max_h = 500, 340
    b = _Builder()
    b.title(title)
    for i, v in enumerate(values):
        x = 100 + pitch * i + (pitch - w) / 2
        b.add(f"bar{i}", "bar", x, base, w=w, max_h=max_h, max_value=top, text=_fmt(v), color="agent")
        b.add(f"lb{i}", "label", x + w / 2, base + 30, text=labels[i], size=20, anchor="center")
        b.tween(f"bar{i}", "value", 400 + 400 * i, 1000 + 400 * i, v, 0.0)
        b.cue(1000 + 400 * i, f"{labels[i]} is {_fmt(v)}.")
    return b.finish("bar_compare", "bar", title, captions)


def timeline(title: str, p: SceneParams, captions: list[str]) -> SceneSpec:
    events = [e for e in (_clean(e, 48) for e in p.events) if e][:8]
    if not events:
        raise TemplateError("timeline needs events")
    n = len(events)
    b = _Builder()
    b.title(title)
    b.add("line", "shape", 80, 300, kind="line", w=840, h=0, color="agent")
    for i, text in enumerate(events):
        x = 500 if n == 1 else 120 + 760 * i / (n - 1)
        y = 240 if i % 2 == 0 else 360
        t = 400 + 700 * i
        b.add(f"tk{i}", "tick", x, 300, opacity=0.0, h=16)
        b.add(f"ev{i}", "label", x, y, opacity=0.0, text=text, size=20, anchor="center")
        b.fade_in(f"tk{i}", t)
        b.fade_in(f"ev{i}", t)
        b.cue(t, text)
    return b.finish("timeline", "timeline", title, captions)


# ------------------------------------------------------------------ the card


def concept_card(title: str, bullets: list[str] | None = None, captions: list[str] | None = None) -> SceneSpec:
    """The universal fallback. Sanitises everything and never raises."""
    try:
        head = _clean(title, 60) or "Key idea"
        items = [t for t in (_clean(x, 100) for x in (bullets or []) if x is not None) if t][:5]
        if not items:
            items = ["Let's look at this idea step by step."]
        b = _Builder()
        b.title(head)
        for i, text in enumerate(items):
            b.add(f"bl{i}", "label", 80, 130 + 85 * i, opacity=0.0, text=text, size=24, anchor="left")
            b.fade_in(f"bl{i}", 400 + 700 * i, 450)
            b.cue(400 + 700 * i, text)
        return b.finish("concept_card", "diagram", head, list(captions or []))
    except Exception:  # last resort: a fixed, valid card
        b = _Builder()
        b.title("Key idea")
        b.add("bl0", "label", 80, 130, text="Let's look at this idea step by step.", size=24, anchor="left")
        return b.finish("concept_card", "diagram", "Key idea", [])


def _concept_card_template(title: str, p: SceneParams, captions: list[str]) -> SceneSpec:
    return concept_card(title, list(p.bullets), captions)


TEMPLATES: dict[str, Callable[[str, SceneParams, list[str]], SceneSpec]] = {
    "fn_plot": fn_plot,
    "projectile": projectile,
    "array_walk": array_walk,
    "graph_traverse": graph_traverse,
    "number_line": number_line,
    "bar_compare": bar_compare,
    "timeline": timeline,
    "concept_card": _concept_card_template,
}
