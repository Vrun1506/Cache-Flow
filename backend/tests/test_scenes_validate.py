import copy

from tutor.scenes.templates import TEMPLATES
from tutor.scenes.validate import KIND_TO_TEMPLATE, build_scene, validate_scene
from tutor.schemas.scene import AnimatorDraft, SceneParams


def draft(template, title="T", captions=None, **params):
    return AnimatorDraft(template=template, title=title, params=SceneParams(**params), captions=captions or [])


def good():
    return TEMPLATES["fn_plot"]("T", SceneParams(expr="x^2", x_min=-3, x_max=3, points=[1]), [])


def mutated(fn):
    spec = good()
    fn(spec)
    return validate_scene(spec)


def test_valid_scene_has_no_problems():
    assert validate_scene(good()) == []


def test_validator_flags_each_class_of_problem():
    assert any("duplicate" in p for p in mutated(lambda s: s.objects.append(copy.deepcopy(s.objects[0]))))
    assert any("outside the canvas" in p for p in mutated(lambda s: setattr(s.objects[0].init, "x", 5000)))
    assert any("unknown target" in p for p in mutated(lambda s: setattr(s.timeline[0], "target", "ghost")))
    assert any("times" in p for p in mutated(lambda s: setattr(s.timeline[0], "t1_ms", s.duration_ms + 1)))
    assert any("times" in p for p in mutated(lambda s: setattr(s.timeline[0], "t0_ms", s.timeline[0].t1_ms)))
    assert any("cue" in p for p in mutated(lambda s: setattr(s.cues[0], "at_ms", s.duration_ms + 5)))
    assert any("axes" in p for p in mutated(lambda s: s.objects[2].props.update(axes="nope")))
    assert any("undefined over most" in p for p in mutated(lambda s: s.objects[2].props.update(expr="ln(x)", x_min=-5, x_max=-1)))
    assert any("curve needs" in p for p in mutated(lambda s: s.objects[2].props.pop("expr")))
    assert any("right edge" in p for p in mutated(lambda s: s.objects[1].props.update(w=2000)))


def test_validator_checks_follow_edges_and_bars():
    ball = TEMPLATES["projectile"]("T", SceneParams(v0=10), [])
    next(o for o in ball.objects if o.id == "ball").props["follow"] = "title"
    assert any("follow" in p for p in validate_scene(ball))
    graph = TEMPLATES["graph_traverse"]("T", SceneParams(nodes=["A", "B"], edges=["A-B"]), [])
    next(o for o in graph.objects if o.type == "edge").props["to"] = "n9"
    assert any("edge" in p or "does not name a node" in p for p in validate_scene(graph))
    bars = TEMPLATES["bar_compare"]("T", SceneParams(values=[1, 2]), [])
    next(o for o in bars.objects if o.type == "bar").props["max_h"] = 9999
    assert any("bar does not fit" in p for p in validate_scene(bars))


def test_build_scene_uses_the_requested_template():
    spec = build_scene(draft("projectile", v0=15, angle=60), "motion")
    assert spec.kind == "motion" and spec.id.startswith("scene-projectile-")


def test_build_scene_falls_back_to_the_kind_hint_template():
    # The draft asks for a plot but has no expression; the hint says motion, which has defaults.
    spec = build_scene(draft("fn_plot", title="Throw"), "motion")
    assert spec.kind == "motion"


def test_build_scene_falls_back_to_concept_card():
    spec = build_scene(draft("fn_plot", expr="y=!!", bullets=["Parabolas curve upward."]), "plot")
    assert spec.kind == "diagram"
    assert [o.props["text"] for o in spec.objects if o.id.startswith("bl")] == ["Parabolas curve upward."]


def test_build_scene_card_uses_fallback_bullets_then_captions():
    spec = build_scene(draft("array_walk", captions=["Say this."]), "array", fallback_bullets=["fact one"])
    assert [o.props["text"] for o in spec.objects if o.id.startswith("bl")] == ["fact one"]
    spec = build_scene(draft("array_walk", captions=["Say this."]), "array")
    assert [o.props["text"] for o in spec.objects if o.id.startswith("bl")] == ["Say this."]


def test_build_scene_survives_garbage_for_every_template():
    junk = dict(expr="((((", values=[float("inf")], nodes=["", ""], edges=["x"], events=[""], labels=[""],
                v0=float("nan"), points=[1e300], jumps=[1e300], algo="??", x_min=1e300, x_max=-1e300)
    for name in TEMPLATES:
        spec = build_scene(draft(name, **junk), "diagram")
        assert validate_scene(spec) == []


def test_build_scene_can_set_the_scene_id():
    assert build_scene(draft("concept_card", bullets=["a"]), scene_id="scene-7").id == "scene-7"


def test_kind_hints_cover_every_visual_kind():
    from typing import get_args

    from tutor.schemas.core import VisualKind

    assert set(KIND_TO_TEMPLATE) == set(get_args(VisualKind))
    assert set(KIND_TO_TEMPLATE.values()) <= set(TEMPLATES)
