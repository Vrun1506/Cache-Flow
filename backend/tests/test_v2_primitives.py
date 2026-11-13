import json
from pathlib import Path

import pytest

from tutor.schemas.board import BoardCmd
from tutor.schemas.scene import Init, SceneObject, SceneParams, SceneSpec, Tween
from tutor.scenes.templates import TEMPLATES
from tutor.v2.layout import BoardLayout, Rect
from tutor.v2.primitives import (
    APPROVED,
    MAX_POINTS,
    MAX_PRIMITIVES,
    _Prim,
    _enforce,
    plot_cmd_to_scene,
    scene_is_valid,
    scene_to_primitives,
)

L = BoardLayout.for_screen()
BOARD = L.board_bounds
BIG = Rect(720, 248, 470, 360)  # the mock's scene region
SLOT = L.slots[0]  # the smallest region a scene could be given

PARAMS = {
    "fn_plot": SceneParams(expr="x^2+5*x+6", x_min=-5, x_max=1, points=[-2, -3]),
    "projectile": SceneParams(v0=20, angle=45),
    "array_walk": SceneParams(values=[5, 2, 9, 1, 7], algo="bubble_sort"),
    "graph_traverse": SceneParams(nodes=["A", "B", "C", "D", "E"], edges=["A-B", "B-C", "C-D", "A-E", "D-E"], algo="bfs"),
    "number_line": SceneParams(points=[2], jumps=[3, -5, 4]),
    "bar_compare": SceneParams(values=[3, 5, 2, 8], labels=["a", "b", "c", "d"]),
    "timeline": SceneParams(events=["War begins", "Treaty", "Peace"]),
    "concept_card": SceneParams(bullets=["First idea", "Second idea", "Third idea"]),
}
KIND_OF = {"fn_plot": "plot", "projectile": "motion"}


def build(name, title="Test scene"):
    return TEMPLATES[name](title, PARAMS[name], [])


def prims_of(name, region=BIG):
    return scene_to_primitives(build(name), region)["primitives"]


def kinds(prims):
    return [p["kind"] for p in prims]


# ---- every template is valid in every region


@pytest.mark.parametrize("region", [BIG, SLOT, Rect(20, 160, 660, 440), Rect(0, 0, 1210, 638)], ids=["mock", "slot", "work", "board"])
@pytest.mark.parametrize("name", list(PARAMS))
def test_every_template_passes_the_swift_bounds_check(name, region):
    scene = scene_to_primitives(build(name), region)
    assert scene_is_valid(scene, BOARD), (name, region)


@pytest.mark.parametrize("name", list(PARAMS))
def test_only_approved_kinds_and_json_plain(name):
    scene = scene_to_primitives(build(name), BIG)
    assert set(kinds(scene["primitives"])) <= APPROVED
    json.dumps(scene)


@pytest.mark.parametrize("name", list(PARAMS))
def test_scene_dict_has_the_documented_keys(name):
    spec = build(name)
    scene = scene_to_primitives(spec, BIG)
    assert set(scene) == {"scene_id", "title", "region", "primitives"}
    assert scene["scene_id"] == spec.id and scene["region"] == BIG.to_list()


@pytest.mark.parametrize("name", list(PARAMS))
def test_mapping_is_deterministic(name):
    assert scene_to_primitives(build(name), BIG) == scene_to_primitives(build(name), BIG)


@pytest.mark.parametrize("name", list(PARAMS))
def test_every_point_is_inside_the_region(name):
    for p in prims_of(name):
        for x, y in p["points"]:
            assert BIG.minx <= x <= BIG.maxx and BIG.miny <= y <= BIG.maxy


@pytest.mark.parametrize("name", list(PARAMS))
def test_limits_hold(name):
    prims = prims_of(name)
    assert len(prims) <= MAX_PRIMITIVES and sum(len(p["points"]) for p in prims) <= MAX_POINTS
    assert all(len(p.get("text", "")) <= 80 for p in prims)


# ---- template specifics


def test_fn_plot_has_axes_a_plot_and_labelled_points():
    prims = prims_of("fn_plot")
    assert kinds(prims)[0] == "axes" and "plot" in kinds(prims)
    labelled = [p for p in prims if p["kind"] == "point" and p.get("text")]
    assert len(labelled) == 2 and any("-2" in p["text"] for p in labelled)


def test_axes_have_four_points():
    axes = next(p for p in prims_of("fn_plot") if p["kind"] == "axes")
    assert len(axes["points"]) == 4
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = axes["points"]
    assert y0 == y1 and x2 == x3 and x1 > x0 and y2 > y3  # x axis horizontal, y axis vertical, bottom to top


def test_fn_plot_curve_is_a_parabola_shape():
    plot = next(p for p in prims_of("fn_plot") if p["kind"] == "plot")["points"]
    ys = [y for _, y in plot]
    lowest = ys.index(max(ys))  # the vertex has the largest screen y (screen y grows downward)
    assert 0 < lowest < len(ys) - 1 and len(plot) <= 200


def test_plot_points_follow_the_screen_x_order():
    plot = next(p for p in prims_of("fn_plot") if p["kind"] == "plot")["points"]
    xs = [x for x, _ in plot]
    assert xs == sorted(xs)


def test_curve_with_gaps_is_split_into_pieces():
    spec = TEMPLATES["fn_plot"]("Hyperbola", SceneParams(expr="1/x", x_min=-4, x_max=4, y_min=-5, y_max=5), [])
    plots = [p for p in scene_to_primitives(spec, BIG)["primitives"] if p["kind"] == "plot"]
    assert len(plots) >= 2


def test_projectile_ball_ends_at_the_landing_point():
    prims = prims_of("projectile")
    plot = next(p for p in prims if p["kind"] == "plot")["points"]
    labelled = [p for p in prims if p["kind"] == "point"]
    ball = next(p for p in labelled if not p.get("text"))["points"][0]
    assert ball[0] == pytest.approx(plot[-1][0], abs=3) and ball[1] == pytest.approx(plot[-1][1], abs=3)
    assert any("peak" in p.get("text", "") for p in labelled)
    assert any("range" in p.get("text", "") for p in prims if p["kind"] == "label")


def test_array_walk_draws_a_box_and_label_per_cell_in_sorted_order():
    prims = prims_of("array_walk")
    cells = [p for p in prims if p["kind"] == "label" and p["text"].replace(".", "").isdigit()]
    by_screen_x = [p["text"] for p in sorted(cells, key=lambda p: p["points"][0][0])]
    assert by_screen_x == ["1", "2", "5", "7", "9"]  # cells move during a bubble sort: the final frame is sorted


def test_array_walk_cells_are_boxes():
    boxes = [p for p in prims_of("array_walk") if p["kind"] == "box"]
    assert len(boxes) >= 5 and all(len(b["points"]) == 2 for b in boxes)


def test_graph_traverse_has_points_and_segments():
    prims = prims_of("graph_traverse")
    assert sum(1 for p in prims if p["kind"] == "point") == 5
    assert sum(1 for p in prims if p["kind"] == "segment") == 5
    assert {p["text"] for p in prims if p["kind"] == "point"} == {"A", "B", "C", "D", "E"}


def test_edges_join_the_node_positions():
    prims = prims_of("graph_traverse")
    nodes = [tuple(p["points"][0]) for p in prims if p["kind"] == "point"]
    for seg in (p for p in prims if p["kind"] == "segment"):
        assert all(tuple(pt) in nodes for pt in seg["points"])


def test_number_line_primitive_ticks_and_hops():
    prims = prims_of("number_line")
    assert kinds(prims).count("number_line") == 1
    line = next(p for p in prims if p["kind"] == "number_line")["points"]
    assert line[0][1] == line[1][1] and line[1][0] > line[0][0]
    assert any(p["kind"] == "arrow" for p in prims)
    assert any(p["kind"] == "label" and p["text"].lstrip("-").isdigit() for p in prims)


def test_bar_compare_heights_are_proportional():
    prims = prims_of("bar_compare")
    boxes = [p["points"] for p in prims if p["kind"] == "box"]
    heights = [b[1][1] - b[0][1] for b in boxes]
    assert heights == pytest.approx([h * heights[3] / 8 for h in (3, 5, 2, 8)], rel=0.02)
    assert [p["text"] for p in prims if p["kind"] == "label" and p["text"] in "abcd"] == ["a", "b", "c", "d"]


def test_timeline_has_a_line_ticks_and_event_labels():
    prims = prims_of("timeline")
    texts = [p["text"] for p in prims if p["kind"] == "label"]
    assert {"War begins", "Treaty", "Peace"} <= set(texts) and "segment" in kinds(prims)


def test_concept_card_is_a_box_and_label_lines():
    prims = prims_of("concept_card")
    assert kinds(prims)[0] == "box" and kinds(prims).count("label") == 3
    box = prims[0]["points"]
    for p in prims[1:]:
        x, y = p["points"][0]
        assert box[0][0] <= x <= box[1][0] and box[0][1] <= y <= box[1][1]


def test_title_object_is_not_duplicated_as_a_label():
    prims = prims_of("concept_card")
    assert all(p.get("text") != "Test scene" for p in prims)
    assert scene_to_primitives(build("concept_card"), BIG)["title"] == "Test scene"


# ---- final frame handling


def spec_of(objects, timeline=(), kind="diagram"):
    return SceneSpec(id="s", title="t", kind=kind, duration_ms=1000, objects=objects, timeline=list(timeline))


def obj(id, type, x=0.0, y=0.0, opacity=1.0, **props):
    return SceneObject(id=id, type=type, props=props, init=Init(x=x, y=y, opacity=opacity))


def test_objects_hidden_at_the_end_are_skipped():
    s = spec_of([obj("a", "label", 100, 100, text="shown"), obj("b", "label", 200, 200, opacity=0.0, text="hidden")])
    texts = [p.get("text") for p in scene_to_primitives(s, BIG)["primitives"]]
    assert "shown" in texts and "hidden" not in texts


def test_fade_in_tween_makes_an_object_visible_in_the_final_frame():
    s = spec_of([obj("b", "label", 200, 200, opacity=0.0, text="fades in")],
                [Tween(t0_ms=0, t1_ms=100, target="b", prop="opacity", to=1.0)])
    assert "fades in" in [p.get("text") for p in scene_to_primitives(s, BIG)["primitives"]]


def test_position_tween_moves_the_object_to_its_end_position():
    s = spec_of([obj("a", "label", 100, 100, text="m")], [Tween(t0_ms=0, t1_ms=100, target="a", prop="x", to=900)])
    (p,) = [p for p in scene_to_primitives(s, BIG)["primitives"] if p["kind"] == "label"]
    assert p["points"][0][0] > BIG.midx


def test_vector_is_an_arrow_from_start_to_start_plus_delta():
    s = spec_of([obj("v", "vector", 100, 100, dx=300, dy=0, label="push")])
    (p,) = scene_to_primitives(s, BIG)["primitives"]
    assert p["kind"] == "arrow" and p["text"] == "push" and p["points"][1][0] > p["points"][0][0]


def test_circle_shape_becomes_a_box_and_line_a_segment():
    s = spec_of([obj("c", "shape", 100, 100, kind="circle", w=100, h=100), obj("l", "shape", 100, 300, kind="line", w=300, h=0)])
    assert kinds(scene_to_primitives(s, BIG)["primitives"]) == ["box", "segment"]


def test_long_text_is_truncated_to_80():
    s = spec_of([obj("a", "label", 100, 100, text="word " * 40)], kind="plot")
    (p,) = scene_to_primitives(s, BIG)["primitives"]
    assert len(p["text"]) <= 80


# ---- fallbacks


def test_unknown_object_type_falls_back_to_a_card_not_an_error():
    s = spec_of([obj("a", "label", 50, 50, text="keep me")])
    s.objects.append(SceneObject.model_construct(id="weird", type="hologram", props={"text": "ghost"}, init=Init()))
    scene = scene_to_primitives(s, BIG)
    assert kinds(scene["primitives"])[0] == "box" and scene_is_valid(scene, BOARD)


def test_empty_scene_falls_back_to_a_card():
    scene = scene_to_primitives(spec_of([]), BIG)
    assert scene["primitives"] and scene_is_valid(scene, BOARD)


def test_curve_without_axes_does_not_crash():
    s = spec_of([obj("c", "curve", expr="x", axes="nope", x_min=0, x_max=1), obj("l", "label", 10, 10, text="hi")])
    assert scene_is_valid(scene_to_primitives(s, BIG), BOARD)


def test_curve_with_a_bad_expression_is_dropped():
    s = spec_of([obj("a", "axes", 100, 80, w=800, h=420, x_min=0, x_max=1, y_min=0, y_max=1),
                 obj("c", "curve", expr="foo(", axes="a", x_min=0, x_max=1)])
    scene = scene_to_primitives(s, BIG)
    assert "plot" not in kinds(scene["primitives"]) and scene_is_valid(scene, BOARD)


def test_degenerate_region_still_returns_something_valid_shaped():
    tiny = Rect(100, 100, 12, 12)
    assert scene_is_valid(scene_to_primitives(build("fn_plot"), tiny), BOARD)


# ---- budgets


def test_primitive_budget_drops_ticks_and_labels_before_axes_and_plots():
    prims = [_Prim("axes", [(0, 0), (1, 1)]), _Prim("plot", [(0, 0), (1, 1)])]
    prims += [_Prim("segment", [(0, 0), (1, 1)], rank=2) for _ in range(70)]
    out = _enforce(prims)
    assert len(out) == MAX_PRIMITIVES and out[0].kind == "axes" and out[1].kind == "plot"


def test_point_budget_thins_the_plots():
    prims = [_Prim("plot", [(float(i), float(i)) for i in range(200)]) for _ in range(8)]
    out = _enforce(prims)
    assert sum(len(p.points) for p in out) <= MAX_POINTS
    assert all(len(p.points) >= 2 for p in out)


def test_point_budget_keeps_curve_ends_when_thinning():
    pts = [(float(i), 0.0) for i in range(200)]
    out = _enforce([_Prim("plot", list(pts)) for _ in range(8)])
    assert out[0].points[0] == pts[0] and out[0].points[-1] == pts[-1]


# ---- plot commands


def plot_cmd(**payload):
    return BoardCmd.model_validate({"id": "b-1-3", "op": "plot", "payload": payload})


def test_plot_command_becomes_a_scene():
    scene = plot_cmd_to_scene(plot_cmd(expr="x^2", x=[-3, 3], text="Parabola"), BIG)
    assert scene is not None and scene["scene_id"] == "scene-b-1-3" and scene_is_valid(scene, BOARD)
    assert "plot" in kinds(scene["primitives"])


def test_plot_command_with_y_range():
    scene = plot_cmd_to_scene(plot_cmd(expr="sin(x)", x=[-6, 6], y=[-2, 2]), BIG)
    assert scene is not None and scene_is_valid(scene, BOARD)


@pytest.mark.parametrize("payload", [{}, {"expr": "foo("}, {"expr": "1/0"}])
def test_bad_plot_commands_return_none(payload):
    assert plot_cmd_to_scene(plot_cmd(**payload), BIG) is None


# ---- validity port


def ok_scene():
    return {"scene_id": "s", "title": "t", "region": [100, 100, 200, 100],
            "primitives": [{"kind": "label", "points": [[150, 150]], "text": "hi"}]}


def test_scene_valid_baseline():
    assert scene_is_valid(ok_scene(), BOARD)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s["primitives"].__setitem__(0, {"kind": "circle", "points": [[150, 150]]}),
        lambda s: s["primitives"].__setitem__(0, {"kind": "label", "points": [[150, 150]], "text": "x" * 81}),
        lambda s: s.update(primitives=[{"kind": "point", "points": [[150, 150]]}] * 65),
        lambda s: s.update(primitives=[{"kind": "plot", "points": [[150, 150]] * 1025}]),
        lambda s: s.update(primitives=[{"kind": "point", "points": [[500, 150]]}]),
        lambda s: s.update(region=[100, 100, 0, 100]),
        lambda s: s.update(region=[1100, 100, 200, 100]),
        lambda s: s.update(region=[float("nan"), 1, 2, 3]),
        lambda s: s.update(primitives=[{"kind": "point", "points": [[float("inf"), 1]]}]),
        lambda s: s.pop("primitives"),
        lambda s: s.update(region=[1, 2]),
    ],
)
def test_invalid_scenes_are_rejected(mutate):
    s = ok_scene()
    mutate(s)
    assert not scene_is_valid(s, BOARD)


def test_scene_points_may_be_8_outside_not_9():
    s = ok_scene()
    s["primitives"] = [{"kind": "point", "points": [[92, 150]]}]
    assert scene_is_valid(s, BOARD)
    s["primitives"] = [{"kind": "point", "points": [[90.5, 150]]}]
    assert not scene_is_valid(s, BOARD)


def test_exactly_64_primitives_and_1024_points_are_allowed():
    s = ok_scene()
    s["primitives"] = [{"kind": "plot", "points": [[150, 150]] * 16}] * 64
    assert scene_is_valid(s, BOARD)


# ---- iOS fixture shape

IOS = Path(__file__).resolve().parents[2] / "ios/Packages/CacheFlowKit/Tests/CacheFlowKitTests/Fixtures/v2/events/scene_preview.json"
EMBEDDED = json.loads(
    '{"board_revision":2,"payload":{"primitives":[{"kind":"axes","points":[[744,560],[1166,560],[1100,590],[1100,276]]},'
    '{"kind":"plot","points":[[744,300],[900,560],[1000,300]]},{"kind":"point","points":[[870,560]],"text":"x = \\u22123"}],'
    '"region":[720,248,470,360],"scene_id":"scene-1","title":"y = x\\u00b2 + 5x + 6"},"seq":7,"type":"scene_preview","v":2}'
)


def fixture_payloads():
    out = [EMBEDDED["payload"]]
    if IOS.exists():
        out.append(json.loads(IOS.read_text())["payload"])
    return out


@pytest.mark.parametrize("fx", fixture_payloads(), ids=lambda _: "fixture")
def test_scene_dicts_have_the_same_keys_and_types_as_the_ios_fixture(fx):
    mine = scene_to_primitives(build("fn_plot"), BIG)
    assert set(mine) == set(fx)
    assert isinstance(mine["scene_id"], str) and isinstance(mine["title"], str)
    assert len(mine["region"]) == len(fx["region"]) == 4
    for a, b in zip(mine["primitives"], fx["primitives"]):
        assert set(a) <= {"kind", "points", "text"} and set(b) <= {"kind", "points", "text"}
    assert all(isinstance(pt, list) and len(pt) == 2 for p in mine["primitives"] for pt in p["points"])
    assert scene_is_valid(fx, None)  # the fixture itself passes my port of the Swift check
