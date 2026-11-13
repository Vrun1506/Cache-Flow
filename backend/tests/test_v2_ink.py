import json
import math
from pathlib import Path

import pytest

from tutor.board.strokes import attach_strokes
from tutor.schemas.board import BoardCmd
from tutor.v2.ink import cmd_to_inks, douglas_peucker, ink_cap, ink_is_valid, simplify
from tutor.v2.layout import BoardLayout, Placer, Rect

L = BoardLayout.for_screen()
BOARD = L.board_bounds


def make(op="write", id="b-1-1", rel="free", ref=None, **payload):
    anchor = {"rel": rel}
    if ref:
        anchor["ref"] = ref
    return BoardCmd.model_validate({"id": id, "op": op, "anchor": anchor, "payload": payload})


def placer(*student):
    return Placer(L, list(student))


def written(text="x^2+5x+6=0", id="b-1-1", p=None, strokes=True):
    p = p or placer(L.question_box)
    c = make("write", id, text=text)
    if strokes:
        attach_strokes([c])
        assert c.payload.strokes, "Hershey strokes are needed for this test"
    return c, p


# ---- write


def test_write_with_strokes_is_valid_and_inside_the_board():
    c, p = written()
    (ink,) = cmd_to_inks(c, p)
    assert ink["style"] == "write" and ink["strokes"] and "formula" not in ink
    assert ink_is_valid(ink, BOARD)


def test_write_lands_in_the_tutor_strip():
    c, p = written()
    (ink,) = cmd_to_inks(c, p)
    assert L.tutor_strip.contains(Rect.from_list(ink["region"]))


def test_write_region_is_the_tight_box_of_the_strokes():
    c, p = written()
    (ink,) = cmd_to_inks(c, p)
    xs = [pt[0] for s in ink["strokes"] for pt in s]
    ys = [pt[1] for s in ink["strokes"] for pt in s]
    r = Rect.from_list(ink["region"])
    assert r.minx <= min(xs) + 0.11 and r.maxx >= max(xs) - 0.11
    assert r.miny <= min(ys) + 0.11 and r.maxy >= max(ys) - 0.11


def test_write_records_its_real_rect_so_later_commands_anchor_to_it():
    c, p = written()
    (ink,) = cmd_to_inks(c, p)
    assert p.rect_of("b-1-1") == Rect.from_list(ink["region"])


def test_write_scales_down_to_a_small_size_hint_keeping_aspect():
    c, _ = written()
    big = cmd_to_inks(c, placer())[0]
    small_cmd = BoardCmd.model_validate({**c.model_dump(by_alias=True), "size_hint": {"w": 100, "h": 100}})
    small = cmd_to_inks(small_cmd, placer())[0]
    rb, rs = Rect.from_list(big["region"]), Rect.from_list(small["region"])
    assert rs.w < rb.w and rs.w / rs.h == pytest.approx(rb.w / rb.h, rel=0.05)


def test_write_without_strokes_uses_a_formula():
    c, p = written(strokes=False)
    (ink,) = cmd_to_inks(c, p)
    assert ink["strokes"] == [] and ink["formula"] == "x^2+5x+6=0" and ink_is_valid(ink, BOARD)


def test_multiline_text_becomes_one_formula_line():
    c, p = written("a=1\nb=2", strokes=False)
    (ink,) = cmd_to_inks(c, p)
    assert "\n" not in ink["formula"] and "a=1" in ink["formula"] and "b=2" in ink["formula"]


def test_formula_is_capped_at_200_characters():
    c = make("write", text="y" * 120)
    c.payload.text = "y" * 120  # Soft(120) already caps; build a longer joined one
    c.payload.text = "\n".join(["abcdefghij" * 3] * 4)
    (ink,) = cmd_to_inks(c, placer())
    assert len(ink["formula"]) <= 200 and ink_is_valid(ink, BOARD)


def test_write_with_no_text_and_no_strokes_is_dropped():
    assert cmd_to_inks(make("write", text="   "), placer()) == []
    assert cmd_to_inks(make("write"), placer()) == []


def test_very_long_stroke_is_simplified_to_512_points():
    c = make("write", text="wave")
    c.payload.strokes = [[[i * 0.5, 10 + 5 * math.sin(i / 7)] for i in range(5000)]]
    (ink,) = cmd_to_inks(c, placer())
    assert len(ink["strokes"][0]) <= 512 and ink_is_valid(ink, BOARD)


def test_more_than_256_strokes_are_capped():
    c = make("write", text="many")
    c.payload.strokes = [[[i * 2, 0], [i * 2 + 1, 5]] for i in range(400)]
    (ink,) = cmd_to_inks(c, placer())
    assert len(ink["strokes"]) <= 256 and ink_is_valid(ink, BOARD)


def test_ink_ids_are_prefixed_and_stable():
    c, p = written()
    (ink,) = cmd_to_inks(c, p, ink_id_prefix="t3")
    assert ink["ink_id"] == "t3-b-1-1"


def test_output_is_deterministic():
    a = cmd_to_inks(*written())
    b = cmd_to_inks(*written())
    assert a == b


def test_board_full_still_returns_valid_ink():
    p = Placer(L, [L.work_area, L.question_box])
    out = []
    for i in range(30):
        c = make("write", f"b-{i}", text="hello world")
        out += cmd_to_inks(c, p)
    assert len(out) == 30 and all(ink_is_valid(i, BOARD) for i in out)


# ---- highlight / underline


def test_highlight_surrounds_the_referenced_object():
    c, p = written()
    (w,) = cmd_to_inks(c, p)
    (h,) = cmd_to_inks(make("highlight", "b-1-2", ref="b-1-1", kind="box"), p)
    assert h["style"] == "highlight" and h["strokes"] == [] and "formula" not in h
    assert Rect.from_list(h["region"]).contains(Rect.from_list(w["region"])) and ink_is_valid(h, BOARD)


def test_highlight_uses_the_anchor_ref_when_payload_has_none():
    c, p = written()
    cmd_to_inks(c, p)
    h = make("highlight", "b-1-2", rel="inside", ref="b-1-1")
    assert len(cmd_to_inks(h, p)) == 1


def test_highlight_of_an_unknown_ref_is_dropped():
    assert cmd_to_inks(make("highlight", ref="ghost"), placer()) == []


def test_highlight_of_a_registered_student_line():
    p = placer()
    p.register("line-1", Rect(40, 200, 300, 50))
    (h,) = cmd_to_inks(make("highlight", ref="line-1", kind="glow"), p)
    assert ink_is_valid(h, BOARD) and Rect.from_list(h["region"]).contains(Rect(40, 200, 300, 50))


def test_highlight_at_the_board_edge_is_clamped_inside():
    p = placer()
    p.record("edge", Rect(0, 0, 100, 40))
    (h,) = cmd_to_inks(make("highlight", ref="edge"), p)
    assert ink_is_valid(h, BOARD) and Rect.from_list(h["region"]).minx >= 0


def test_underline_is_one_pen_stroke_under_the_object():
    c, p = written()
    (w,) = cmd_to_inks(c, p)
    (u,) = cmd_to_inks(make("underline", "b-1-2", ref="b-1-1"), p)
    wr = Rect.from_list(w["region"])
    assert u["style"] == "write" and len(u["strokes"]) == 1 and ink_is_valid(u, BOARD)
    ys = [pt[1] for pt in u["strokes"][0]]
    assert min(ys) >= wr.maxy and all(wr.minx - 0.1 <= pt[0] <= wr.maxx + 0.1 for pt in u["strokes"][0])


def test_underline_of_unknown_ref_is_dropped():
    assert cmd_to_inks(make("underline", ref="ghost"), placer()) == []


# ---- shapes


@pytest.mark.parametrize("kind", ["rect", "circle", "triangle", "brace", "line", "box"])
def test_every_shape_kind_is_valid(kind):
    (ink, *_) = cmd_to_inks(make("shape", kind=kind), placer())
    assert ink_is_valid(ink, BOARD) and ink["strokes"]


def test_circle_uses_the_circle_style_others_write():
    assert cmd_to_inks(make("shape", kind="circle"), placer())[0]["style"] == "circle"
    assert cmd_to_inks(make("shape", kind="rect"), placer())[0]["style"] == "write"


def test_rect_is_a_closed_stroke():
    s = cmd_to_inks(make("shape", kind="rect"), placer())[0]["strokes"][0]
    assert s[0] == s[-1] and len(s) == 5


def test_shape_label_is_a_separate_formula_ink_inside():
    inks = cmd_to_inks(make("shape", kind="rect", text="force"), placer())
    assert len(inks) == 2 and inks[1]["formula"] == "force"
    assert Rect.from_list(inks[0]["region"]).inset(-1).contains(Rect.from_list(inks[1]["region"]))
    assert all(ink_is_valid(i, BOARD) for i in inks)


def test_line_label_is_ignored():
    assert len(cmd_to_inks(make("shape", kind="line", text="ignored"), placer())) == 1


def test_wide_brace_opens_sideways():
    c = BoardCmd.model_validate({"id": "b", "op": "shape", "size_hint": {"w": 200, "h": 30}, "payload": {"kind": "brace"}})
    (ink,) = cmd_to_inks(c, placer())
    assert ink_is_valid(ink, BOARD)


def test_unknown_shape_kind_falls_back_to_a_rectangle():
    (ink,) = cmd_to_inks(make("shape"), placer())
    assert len(ink["strokes"][0]) == 5


# ---- arrows


def two_boxes():
    p = placer()
    p.record("a", Rect(730, 40, 120, 50))
    p.record("b", Rect(1000, 40, 120, 50))
    return p


def test_arrow_between_two_refs_has_shaft_and_head():
    p = two_boxes()
    (ink,) = cmd_to_inks(make("arrow", **{"from": "a", "to": "b"}), p)
    assert ink["style"] == "write" and len(ink["strokes"]) == 2 and len(ink["strokes"][1]) == 3
    assert ink["strokes"][0][0][0] > 850 and ink["strokes"][0][-1][0] < 1000
    assert ink_is_valid(ink, BOARD)


def test_arrow_pointing_down_between_stacked_refs():
    p = placer()
    p.record("a", Rect(800, 40, 120, 50))
    p.record("b", Rect(800, 200, 120, 50))
    (ink,) = cmd_to_inks(make("arrow", **{"from": "a", "to": "b"}), p)
    shaft = ink["strokes"][0]
    assert shaft[0][1] > 90 and shaft[-1][1] < 200 and ink_is_valid(ink, BOARD)


def test_arrow_with_point_ends():
    (ink,) = cmd_to_inks(make("arrow", **{"from": "800,100", "to": "(1000, 160)"}), placer())
    assert ink_is_valid(ink, BOARD)


def test_arrow_with_only_a_target_comes_from_the_left():
    p = two_boxes()
    (ink,) = cmd_to_inks(make("arrow", to="b"), p)
    assert ink_is_valid(ink, BOARD)


def test_arrow_with_nothing_known_is_dropped():
    assert cmd_to_inks(make("arrow", **{"from": "ghost", "to": "phantom"}), placer()) == []


def test_arrow_between_touching_refs_is_dropped_not_degenerate():
    p = placer()
    p.record("a", Rect(800, 40, 50, 50))
    p.record("b", Rect(852, 40, 50, 50))
    assert cmd_to_inks(make("arrow", **{"from": "a", "to": "b"}), p) == []


# ---- tables


def test_table_has_a_grid_ink_and_one_formula_per_row():
    inks = cmd_to_inks(make("table", rows=[["x", "y"], ["1", "2"], ["2", "4"]]), placer())
    assert len(inks) == 4 and inks[0]["strokes"] and not inks[0].get("formula")
    assert [i["formula"] for i in inks[1:]] == ["x | y", "1 | 2", "2 | 4"]
    assert all(ink_is_valid(i, BOARD) for i in inks)


def test_table_grid_line_count():
    inks = cmd_to_inks(make("table", rows=[["a", "b", "c"], ["1", "2", "3"]]), placer())
    assert len(inks[0]["strokes"]) == (2 + 1) + (3 + 1)


def test_narrow_table_degrades_to_one_formula():
    c = BoardCmd.model_validate({"id": "t", "op": "table", "size_hint": {"w": 100, "h": 60},
                                 "payload": {"rows": [["a", "b", "c", "d"], ["1", "2", "3", "4"]]}})
    inks = cmd_to_inks(c, placer())
    assert len(inks) == 1 and inks[0]["strokes"] == [] and "|" in inks[0]["formula"] and ink_is_valid(inks[0], BOARD)


def test_empty_table_is_dropped():
    assert cmd_to_inks(make("table"), placer()) == []
    assert cmd_to_inks(make("table", rows=[]), placer()) == []


# ---- ops that are not ink


@pytest.mark.parametrize("op", ["plot", "focus", "erase", "group"])
def test_non_ink_ops_return_nothing(op):
    assert cmd_to_inks(make(op, expr="x", ref="a"), placer()) == []


# ---- validity port


def good():
    return {"ink_id": "i", "style": "write", "region": [100, 100, 50, 20], "strokes": [[[100, 100], [150, 120]]]}


def test_valid_baseline():
    assert ink_is_valid(good(), BOARD)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda i: i.update(strokes=[[[100, 100], [150, 120]]] * 257),
        lambda i: i.update(strokes=[[[100, 100]] * 513]),
        lambda i: i.update(formula="x" * 201),
        lambda i: i.update(strokes=[], formula=""),
        lambda i: i.update(strokes=[]),
        lambda i: i.update(region=[100, 100, 0, 20]),
        lambda i: i.update(region=[1200, 100, 50, 20]),
        lambda i: i.update(region=[100, 630, 50, 20]),
        lambda i: i.update(region=[float("nan"), 100, 50, 20]),
        lambda i: i.update(strokes=[[[100, 100], [200, 120]]]),
        lambda i: i.update(strokes=[[[100, 100], [float("inf"), 120]]]),
        lambda i: i.update(strokes=[[[100, 100, 5]]]),
        lambda i: i.pop("region"),
        lambda i: i.update(region=[1, 2, 3]),
    ],
)
def test_invalid_variants_are_rejected(mutate):
    ink = good()
    mutate(ink)
    assert not ink_is_valid(ink, BOARD)


def test_points_may_sit_8_outside_the_region_but_not_9():
    ink = good()
    ink["strokes"] = [[[92, 100], [150, 120]]]
    assert ink_is_valid(ink, BOARD)
    ink["strokes"] = [[[90.5, 100], [150, 120]]]
    assert not ink_is_valid(ink, BOARD)


def test_highlight_needs_no_strokes():
    assert ink_is_valid({"ink_id": "i", "style": "highlight", "region": [10, 10, 40, 20], "strokes": []}, BOARD)


def test_no_board_means_no_bounds_check():
    ink = good()
    ink["region"] = [5000, 5000, 50, 20]
    ink["strokes"] = [[[5000, 5000], [5050, 5020]]]
    assert ink_is_valid(ink, None)


def test_ink_cap_keeps_the_first_valid_ones_in_order():
    bad = good()
    bad["region"] = [0, 0, 0, 0]
    inks = [dict(good(), ink_id=f"i{k}") for k in range(5)]
    out = ink_cap([inks[0], bad, *inks[1:]], 3, BOARD)
    assert [i["ink_id"] for i in out] == ["i0", "i1", "i2"]
    assert ink_cap(inks, 0) == []


# ---- geometry helpers


def test_douglas_peucker_keeps_corners_and_drops_collinear_points():
    pts = [(0, 0), (1, 0), (2, 0), (3, 0), (3, 3)]
    assert douglas_peucker(pts, 0.1) == [(0, 0), (3, 0), (3, 3)]


def test_douglas_peucker_short_input_unchanged():
    assert douglas_peucker([(0, 0), (1, 1)], 1) == [(0, 0), (1, 1)]


def test_simplify_respects_limit_and_keeps_ends():
    pts = [(i, (i * 7919) % 13) for i in range(2000)]
    out = simplify(pts, 100)
    assert len(out) <= 100 and out[0] == pts[0] and out[-1] == pts[-1]


# ---- the iOS fixture shape

IOS = Path(__file__).resolve().parents[2] / "ios/Packages/CacheFlowKit/Tests/CacheFlowKitTests/Fixtures/v2/events/tutor_ink_preview.json"
EMBEDDED = json.loads(
    '{"board_revision":2,"payload":{"ink_id":"ink-1","region":[26,160,328,90],"strokes":[[[30,205],[190,168],[350,205],[190,242],[30,205]]],"style":"circle"},"run_id":"run-t2","seq":5,"session_id":"s-fixture","turn_id":"t2","type":"tutor_ink_preview","v":2}'
)


def fixture_payloads():
    out = [EMBEDDED["payload"]]
    if IOS.exists():
        out.append(json.loads(IOS.read_text())["payload"])
    return out


@pytest.mark.parametrize("fx", fixture_payloads(), ids=lambda _: "fixture")
def test_ink_dicts_have_the_same_keys_and_types_as_the_ios_fixture(fx):
    c, p = written()
    mine = cmd_to_inks(c, p)[0]
    assert set(mine) <= {"ink_id", "style", "region", "strokes", "formula"}
    assert set(fx) <= set(mine) | {"formula"}
    assert type(mine["ink_id"]) is type(fx["ink_id"]) and type(mine["style"]) is type(fx["style"])
    assert len(mine["region"]) == len(fx["region"]) == 4 and all(isinstance(v, (int, float)) for v in mine["region"])
    assert isinstance(mine["strokes"][0][0], list) and len(mine["strokes"][0][0]) == 2
    assert ink_is_valid(fx, None)  # the fixture itself passes my port of the Swift check
    json.dumps(mine)  # plain JSON types only
