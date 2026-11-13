"""Scene validation and the build-with-fallback entry point (final-plan.md §16.7).

``validate_scene`` returns a list of problems (empty means valid).
``build_scene`` turns an Animator draft into a scene and never raises: a draft
that cannot be built falls back to the template for the visual's kind, then to
the concept card. A scene never blocks a lesson.
"""

from __future__ import annotations

from ..schemas.scene import AnimatorDraft, SceneSpec
from .expr import dry_run
from .templates import TEMPLATES, concept_card

KIND_TO_TEMPLATE = {
    "plot": "fn_plot",
    "motion": "projectile",
    "array": "array_walk",
    "graph": "graph_traverse",
    "number_line": "number_line",
    "bar": "bar_compare",
    "timeline": "timeline",
    "diagram": "concept_card",
}
MIN_FINITE = 0.5
_TOL = 1.0


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def validate_scene(spec: SceneSpec) -> list[str]:
    problems: list[str] = []
    W, H = spec.canvas.w, spec.canvas.h
    by_id = {}
    for o in spec.objects:
        if o.id in by_id:
            problems.append(f"duplicate object id {o.id!r}")
        by_id[o.id] = o

    for o in spec.objects:
        props = o.props
        follows = o.type == "point" and props.get("follow")
        x, y = o.init.x, o.init.y
        if not follows and not (-_TOL <= x <= W + _TOL and -_TOL <= y <= H + _TOL):
            problems.append(f"{o.id}: start ({x:g}, {y:g}) is outside the canvas")
        w, h = _num(props.get("w")), _num(props.get("h"))
        if w is not None and o.type != "bar" and x + w > W + _TOL:
            problems.append(f"{o.id}: extends past the right edge")
        if h is not None and o.type not in ("bar", "shape") and y + h > H + _TOL:
            problems.append(f"{o.id}: extends past the bottom edge")
        if o.type == "bar":
            max_h = _num(props.get("max_h"))
            if max_h is None or y - max_h < -_TOL or x + (w or 0) > W + _TOL:
                problems.append(f"{o.id}: bar does not fit the canvas")
        if o.type == "vector":
            ex, ey = x + (_num(props.get("dx")) or 0), y + (_num(props.get("dy")) or 0)
            if not (-_TOL <= ex <= W + _TOL and -_TOL <= ey <= H + _TOL):
                problems.append(f"{o.id}: ends outside the canvas")
        if o.type == "edge":
            for end in ("from", "to"):
                if by_id.get(props.get(end)) is None or by_id[props[end]].type != "node":
                    problems.append(f"{o.id}: {end} does not name a node")
        if follows:
            target = by_id.get(props["follow"])
            if target is None or target.type != "curve":
                problems.append(f"{o.id}: follow does not name a curve")
        if o.type == "curve":
            axes = by_id.get(props.get("axes"))
            if axes is None or axes.type != "axes":
                problems.append(f"{o.id}: axes does not name an axes object")
            x0, x1 = _num(props.get("x_min")), _num(props.get("x_max"))
            expr = props.get("expr")
            if not isinstance(expr, str) or x0 is None or x1 is None:
                problems.append(f"{o.id}: curve needs expr, x_min and x_max")
            elif dry_run(expr, (x0, x1)) < MIN_FINITE:
                problems.append(f"{o.id}: expression is undefined over most of its range")

    for i, t in enumerate(spec.timeline):
        if t.target not in by_id:
            problems.append(f"tween {i}: unknown target {t.target!r}")
        if not (0 <= t.t0_ms < t.t1_ms <= spec.duration_ms):
            problems.append(f"tween {i}: times {t.t0_ms}..{t.t1_ms} outside 0..{spec.duration_ms}")
        if t.prop == "x" and not (-_TOL <= t.to <= W + _TOL):
            problems.append(f"tween {i}: x target outside the canvas")
        if t.prop == "y" and not (-_TOL <= t.to <= H + _TOL):
            problems.append(f"tween {i}: y target outside the canvas")
    for c in spec.cues:
        if not (0 <= c.at_ms <= spec.duration_ms):
            problems.append(f"cue at {c.at_ms} ms is outside the scene")
    return problems


def build_scene(draft: AnimatorDraft, kind_hint: str | None = None, *, scene_id: str | None = None,
                fallback_bullets: list[str] | None = None) -> SceneSpec:
    """Build the scene for a draft. Never raises (CancelledError aside)."""
    tried: set[str] = set()
    spec: SceneSpec | None = None
    for name in (draft.template, KIND_TO_TEMPLATE.get(kind_hint or "")):
        if not name or name == "concept_card" or name in tried:
            continue
        tried.add(name)
        try:
            candidate = TEMPLATES[name](draft.title, draft.params, list(draft.captions))
        except Exception:
            continue
        if not validate_scene(candidate):
            spec = candidate
            break
    if spec is None:
        bullets = list(draft.params.bullets) or list(fallback_bullets or []) or list(draft.captions)
        spec = concept_card(draft.title, bullets, list(draft.captions))
    if scene_id:
        spec.id = scene_id[:60]
    return spec
