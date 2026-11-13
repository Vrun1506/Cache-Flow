import itertools
import math

import pytest

from tutor.scenes.expr import evaluate
from tutor.scenes.templates import (
    TEMPLATES,
    TemplateError,
    binary_trace,
    bubble_trace,
    concept_card,
    projectile_stats,
    traverse,
    two_pointer_trace,
)
from tutor.scenes.validate import validate_scene
from tutor.schemas.scene import SceneParams

GOOD = {
    "fn_plot": {"expr": "x^2 - 4", "x_min": -4, "x_max": 4, "points": [-2, 2]},
    "projectile": {"v0": 20, "angle": 45},
    "array_walk": {"values": [5, 3, 8, 1, 9], "algo": "bubble_sort"},
    "graph_traverse": {"nodes": list("ABCDEF"), "edges": ["A-B", "A-C", "B-D", "C-E", "E-F"], "algo": "bfs"},
    "number_line": {"points": [0], "jumps": [3, -5, 2]},
    "bar_compare": {"labels": ["a", "b", "c"], "values": [3, 9, 5]},
    "timeline": {"events": ["1914 war starts", "1918 war ends", "1919 treaty"]},
    "concept_card": {"bullets": ["one", "two", "three"]},
}


def build(name, **params):
    return TEMPLATES[name]("Title", SceneParams(**params), [])


@pytest.mark.parametrize("name", sorted(GOOD))
def test_every_template_is_valid(name):
    spec = build(name, **GOOD[name])
    assert validate_scene(spec) == []
    assert len(spec.objects) <= 24 and len(spec.timeline) <= 40 and spec.duration_ms <= 20000
    assert spec.cues and all(c.caption for c in spec.cues)
    assert spec.v == 1 and spec.canvas.w == 1000


def test_scene_ids_are_deterministic_and_distinct():
    a, b = build("fn_plot", **GOOD["fn_plot"]), build("projectile", **GOOD["projectile"])
    assert a.id == build("fn_plot", **GOOD["fn_plot"]).id and a.id != b.id


def test_given_captions_replace_generated_cues():
    spec = TEMPLATES["fn_plot"]("T", SceneParams(**GOOD["fn_plot"]), ["First.", "Second."])
    assert [c.caption for c in spec.cues] == ["First.", "Second."]
    assert spec.cues[0].at_ms == 0 and spec.cues[1].at_ms > 0


# ---- fn_plot


def test_fn_plot_curve_uses_the_wire_grammar_and_marks_points():
    spec = build("fn_plot", expr="2x^2 - 3", x_min=-3, x_max=3, points=[1, 50])
    curve = next(o for o in spec.objects if o.type == "curve")
    assert curve.props["expr"] == "2*x^2 - 3"
    assert [o.id for o in spec.objects if o.type == "point"] == ["pt0"]  # x=50 is outside the range


def test_fn_plot_needs_a_usable_expression():
    for bad in (None, "", "y+1", "ln(x)"):
        with pytest.raises(TemplateError if bad == "ln(x)" else ValueError):
            build("fn_plot", expr=bad, x_min=-5, x_max=-1)


def test_fn_plot_swaps_reversed_range_and_survives_asymptotes():
    spec = build("fn_plot", expr="1/x", x_min=5, x_max=-5)
    curve = next(o for o in spec.objects if o.type == "curve")
    assert curve.props["x_min"] == -5 and validate_scene(spec) == []


# ---- projectile


@pytest.mark.parametrize("v0,angle,g", [(20, 45, 9.81), (50, 30, 9.81), (10, 80, 3.7), (200, 5, 0.5)])
def test_projectile_matches_the_physics_formulas(v0, angle, g):
    spec = build("projectile", v0=v0, angle=angle, g=g)
    a = math.radians(angle)
    stats = projectile_stats(v0, angle, g)
    assert stats["range"] == pytest.approx(v0**2 * math.sin(2 * a) / g)
    assert stats["peak"] == pytest.approx((v0 * math.sin(a)) ** 2 / (2 * g))
    curve = next(o for o in spec.objects if o.id == "curve")
    expr, rng = curve.props["expr"], curve.props["x_max"]
    assert rng == pytest.approx(stats["range"])
    assert evaluate(expr, x=0) == pytest.approx(0, abs=1e-9)
    assert evaluate(expr, x=rng) == pytest.approx(0, abs=1e-3 * stats["peak"])
    assert evaluate(expr, x=rng / 2) == pytest.approx(stats["peak"], rel=1e-5)
    assert validate_scene(spec) == []


def test_projectile_ball_reaches_the_apex_at_half_time():
    spec = build("projectile", v0=20, angle=45)
    ball = [t for t in spec.timeline if t.target == "ball"][0]
    peak = [t for t in spec.timeline if t.target == "peak"][0]
    assert ball.ease == "linear"  # constant horizontal speed: x is linear in time
    mid = (ball.t0_ms + ball.t1_ms) / 2
    assert abs(peak.t0_ms - mid) <= 150


def test_projectile_clamps_silly_inputs_and_has_defaults():
    for params in ({}, {"v0": -5, "angle": 400, "g": 0}, {"v0": 1e9, "angle": float("nan")}):
        spec = build("projectile", **params)
        assert validate_scene(spec) == []


# ---- arrays


@pytest.mark.parametrize("perm", list(itertools.permutations([4, 1, 3, 5, 2])))
def test_bubble_trace_sorts_every_permutation_within_the_tween_budget(perm):
    final, ops = bubble_trace(list(perm))
    assert final == sorted(perm)
    swaps = sum(1 for op, _ in ops if op == "swap")
    cmps = sum(1 for op, _ in ops if op == "cmp")
    assert cmps + 2 * swaps + len(perm) <= 40


def test_bubble_scene_ends_sorted_and_valid():
    spec = build("array_walk", values=[5, 4, 3, 2, 1, 9, 9], algo="bubble sort")
    assert sum(1 for o in spec.objects if o.type == "cell") == 5  # clamped
    assert validate_scene(spec) == []
    assert spec.cues[-1].caption == "Sorted: 1, 2, 3, 4, 5."
    # Replaying the swaps leaves the cells in sorted order.
    pos = {o.id: o.init.x for o in spec.objects if o.type == "cell"}
    for t in sorted((t for t in spec.timeline if t.prop == "x" and t.target.startswith("c")), key=lambda t: t.t0_ms):
        pos[t.target] = t.to
    by_x = [o.props["text"] for o in sorted((o for o in spec.objects if o.type == "cell"), key=lambda o: pos[o.id])]
    assert by_x == ["1", "2", "3", "4", "5"]


@pytest.mark.parametrize("target", [2, 5, 8, 12, 16, 23, 38, 1, 100, 9])
def test_binary_trace_finds_the_index_or_none(target):
    vals = [2, 5, 8, 12, 16, 23, 38]
    probes, found = binary_trace(vals, target)
    assert (vals.index(target) if target in vals else None) == found
    assert len(probes) <= 3


def test_binary_scene_probes_the_middle_and_highlights_the_hit():
    spec = build("array_walk", values=[38, 2, 23, 5, 8, 16, 12], algo="binary_search", target=23)
    cells = [o for o in spec.objects if o.type == "cell"]
    assert [c.props["text"] for c in cells] == ["2", "5", "8", "12", "16", "23", "38"]  # sorted first
    hit = [t for t in spec.timeline if t.prop == "highlight"]
    assert [t.target for t in hit] == ["c5"]
    assert spec.cues[-1].caption == "Found 23 at position 5."
    assert validate_scene(spec) == []


def test_binary_scene_reports_missing_target():
    spec = build("array_walk", values=[1, 3, 5, 7], algo="binary_search", target=4)
    assert "not in the list" in spec.cues[-1].caption


def test_linear_and_two_pointer_scenes():
    spec = build("array_walk", values=[4, 9, 2, 7, 5], algo="linear_search", target=7)
    assert spec.cues[-1].caption == "Found 7 at position 3."
    spec = build("array_walk", values=[1, 3, 4, 6, 9, 12], algo="two_pointers", target=10)
    assert "Found the pair" in spec.cues[-1].caption
    assert two_pointer_trace([1, 3, 4, 6, 9, 12], 10)[1] == (0, 4)
    assert two_pointer_trace([1, 2, 3], 100)[1] is None


@pytest.mark.parametrize("algo", ["linear_search", "binary_search", "bubble_sort", "two_pointers"])
def test_array_walk_clamps_to_twelve_values(algo):
    spec = build("array_walk", values=list(range(40, 0, -1)), algo=algo)
    assert sum(1 for o in spec.objects if o.type == "cell") <= 12
    assert validate_scene(spec) == []


def test_array_walk_rejects_unusable_input():
    with pytest.raises(TemplateError):
        build("array_walk", values=[1])
    with pytest.raises(TemplateError):
        build("array_walk", values=[1, 2, 3], algo="quantum_sort")


# ---- graphs


def test_traverse_orders():
    nodes, edges = list("ABCDEF"), [(0, 1), (0, 2), (1, 3), (2, 4), (4, 5)]
    assert [nodes[i] for i in traverse(nodes, edges, "bfs")[0]] == list("ABCDEF")
    assert [nodes[i] for i in traverse(nodes, edges, "dfs")[0]] == list("ABDCEF")
    order, tree = traverse(nodes, edges, "bfs", start=3)
    assert order[0] == 3 and len(tree) == len(order) - 1
    assert traverse(list("ABC"), [(0, 1)], "bfs")[0] == [0, 1]  # only the reachable part


def test_graph_scene_visits_every_node_once_and_ignores_bad_edges():
    spec = build("graph_traverse", nodes=["A", "B", "C", "A"], edges=["A-B", "B->C", "C-Z", "A-A", "junk", "B-A"], algo="dfs")
    assert len([o for o in spec.objects if o.type == "node"]) == 3
    assert len([o for o in spec.objects if o.type == "edge"]) == 2
    visits = [t for t in spec.timeline if t.target.startswith("n") and t.prop == "highlight"]
    assert len(visits) == 3 and validate_scene(spec) == []


def test_graph_needs_nodes_and_edges():
    with pytest.raises(TemplateError):
        build("graph_traverse", nodes=["A"], edges=[])
    with pytest.raises(TemplateError):
        build("graph_traverse", nodes=["A", "B"], edges=["A-C"])


def test_graph_clamps_to_eight_nodes():
    spec = build("graph_traverse", nodes=[f"n{i}" for i in range(20)], edges=[f"n{i}-n{i+1}" for i in range(19)])
    assert len([o for o in spec.objects if o.type == "node"]) == 8 and validate_scene(spec) == []


# ---- number line, bars, timeline


def test_number_line_lands_where_the_jumps_say():
    spec = build("number_line", points=[1], jumps=[3, -5, 2])
    hop = [t for t in spec.timeline if t.target == "hopper"]
    ticks = {o.props["label"]: o.init.x for o in spec.objects if o.type == "tick"}
    assert len(hop) == 3
    assert hop[-1].to == pytest.approx(ticks["1"], abs=0.05)
    assert len(ticks) <= 11 and validate_scene(spec) == []


def test_number_line_handles_wide_and_negative_ranges():
    for params in ({"points": [-1000, 5000]}, {"jumps": [0.25, 0.5]}, {"points": [0], "jumps": [1e6, -1e6]}):
        assert validate_scene(build("number_line", **params)) == []
    with pytest.raises(TemplateError):
        build("number_line")


def test_bars_scale_to_the_tallest_and_clamp_negatives():
    spec = build("bar_compare", labels=["a", "b"], values=[3, -4, 9, 1, 1, 1, 1, 1])
    bars = [o for o in spec.objects if o.type == "bar"]
    assert len(bars) == 6 and bars[0].props["max_value"] == 9
    assert [t.to for t in spec.timeline][:2] == [3, 0] and validate_scene(spec) == []
    assert [o.props["text"] for o in spec.objects if o.type == "label" and o.id.startswith("lb")][:3] == ["a", "b", "C"]
    with pytest.raises(TemplateError):
        build("bar_compare")
    assert validate_scene(build("bar_compare", values=[0, 0])) == []


def test_timeline_clamps_to_eight_events_and_alternates():
    spec = build("timeline", events=[f"event {i}" for i in range(12)])
    evs = [o for o in spec.objects if o.id.startswith("ev")]
    assert len(evs) == 8 and evs[0].init.y != evs[1].init.y and validate_scene(spec) == []
    assert validate_scene(build("timeline", events=["only one"])) == []
    with pytest.raises(TemplateError):
        build("timeline", events=["", "  "])


# ---- concept card never raises


@pytest.mark.parametrize(
    "title,bullets",
    [("", []), ("T" * 500, ["b" * 500] * 9), (None, [None, "", 5, ["x"]]), ("ok", ["\x00\x01 ctrl", "line\nbreak"])],
)
def test_concept_card_never_raises_and_is_valid(title, bullets):
    spec = concept_card(title, bullets, ["c" * 400] * 9)
    assert validate_scene(spec) == []
    assert 1 <= len([o for o in spec.objects if o.id.startswith("bl")]) <= 5 and len(spec.cues) <= 6
    assert all("\x00" not in o.props.get("text", "") for o in spec.objects)
