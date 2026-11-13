"""Scene specs and the Animator's draft (final-plan.md §6, §16.2, §16.7).

The LLM never writes a scene. It returns an ``AnimatorDraft`` (a template name
plus flat parameters) and code builds the ``SceneSpec`` from it.

Scene coordinate space is a 1000 x 600 canvas, origin top-left, y down.
Object ``props`` by ``type`` (the contract the iPad ``SceneRenderer`` follows):

* ``axes``   init=(x,y) top-left of the plot area; props w,h, x_min,x_max,y_min,y_max, x_label?, y_label?
* ``curve``  props expr (§16.7 grammar, variable x), axes (id of an axes object),
             x_min, x_max (data units), color?. ``progress`` 0..1 reveals the curve.
* ``point``  props radius, color?, label?, follow? (id of a curve). A point with
             ``follow`` sits at the curve position for ``progress`` 0..1, mapped
             linearly across the curve's x range (so constant horizontal speed).
* ``vector`` init=(x,y) start; props dx, dy (canvas units), arc? (draw as an arc), label?, color?
* ``shape``  props kind (rect|circle|line), w, h, color?
* ``label``  props text, size, anchor (left|center|right), color?
* ``cell``   props text, w, h, index?
* ``node``   props label, radius
* ``edge``   props from, to (node ids), color?
* ``bar``    init=(x,y) bottom-left baseline; props w, max_h, max_value, text?, color?.
             ``value`` tween (0..max_value) sets the height: value/max_value*max_h upwards.
* ``tick``   init=(x,y) on a line; props label?, h

Tween props: x, y (canvas units), opacity, scale, progress (0..1), value, highlight (0..1).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .text import Soft, cap_list

SceneKind = Literal["plot", "motion", "array", "graph", "number_line", "bar", "timeline", "diagram"]
TemplateName = Literal[
    "fn_plot", "projectile", "array_walk", "graph_traverse", "number_line", "bar_compare", "timeline", "concept_card"
]
ObjectType = Literal["axes", "curve", "point", "vector", "shape", "label", "cell", "node", "edge", "bar", "tick"]
TweenProp = Literal["x", "y", "opacity", "scale", "progress", "value", "highlight"]
Ease = Literal["linear", "inOut"]

CANVAS_W = 1000
CANVAS_H = 600
MAX_DURATION_MS = 20000
MAX_OBJECTS = 24
MAX_TWEENS = 40
MAX_CUES = 6


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class Canvas(_Model):
    w: int = CANVAS_W
    h: int = CANVAS_H


class Init(_Model):
    x: float = 0.0
    y: float = 0.0
    opacity: float = 1.0
    scale: float = 1.0


class SceneObject(_Model):
    id: Soft(40)
    type: ObjectType
    props: dict[str, Any] = Field(default_factory=dict)
    init: Init = Field(default_factory=Init)


class Tween(_Model):
    t0_ms: int
    t1_ms: int
    target: Soft(40)
    prop: TweenProp
    from_: Optional[float] = Field(default=None, alias="from")
    to: float
    ease: Ease = "inOut"


class Cue(_Model):
    at_ms: int
    caption: Soft(120)


class SceneSpec(_Model):
    v: Literal[1] = 1
    id: Soft(60)
    title: Soft(60)
    kind: SceneKind
    duration_ms: int = Field(ge=1, le=MAX_DURATION_MS)
    canvas: Canvas = Field(default_factory=Canvas)
    objects: Annotated[list[SceneObject], Field(max_length=MAX_OBJECTS)]
    timeline: Annotated[list[Tween], Field(max_length=MAX_TWEENS)] = Field(default_factory=list)
    cues: Annotated[list[Cue], Field(max_length=MAX_CUES)] = Field(default_factory=list)

    def to_wire(self) -> dict[str, Any]:
        """JSON-ready dict for the ``scene`` event (tween ``from`` keeps its wire name)."""
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


class SceneParams(_Model):
    """Flat, all-optional template parameters. Each template reads only its own fields."""

    # fn_plot
    expr: Optional[str] = None
    x_min: Optional[float] = None
    x_max: Optional[float] = None
    y_min: Optional[float] = None
    y_max: Optional[float] = None
    points: Annotated[list[float], cap_list(4)] = Field(default_factory=list)
    x_label: Optional[Soft(30)] = None
    y_label: Optional[Soft(30)] = None
    # projectile
    v0: Optional[float] = None
    angle: Optional[float] = None
    g: Optional[float] = None
    # array_walk
    values: Annotated[list[float], cap_list(12)] = Field(default_factory=list)
    algo: Optional[Soft(30)] = None
    target: Optional[float] = None
    # graph_traverse
    nodes: Annotated[list[Soft(12)], cap_list(8)] = Field(default_factory=list)
    edges: Annotated[list[Soft(30)], cap_list(12)] = Field(default_factory=list)
    start: Optional[Soft(12)] = None
    # number_line
    jumps: Annotated[list[float], cap_list(5)] = Field(default_factory=list)
    # bar_compare
    labels: Annotated[list[Soft(16)], cap_list(6)] = Field(default_factory=list)
    # timeline
    events: Annotated[list[Soft(60)], cap_list(8)] = Field(default_factory=list)
    # concept_card
    bullets: Annotated[list[Soft(100)], cap_list(5)] = Field(default_factory=list)


class AnimatorDraft(_Model):
    """What the Animator LLM returns. It never returns frames or coordinates."""

    template: TemplateName
    title: Soft(60)
    params: SceneParams
    captions: Annotated[list[Soft(120)], cap_list(4)] = Field(default_factory=list)
