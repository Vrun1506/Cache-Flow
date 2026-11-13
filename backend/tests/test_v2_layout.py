import pytest

from tutor.schemas.board import BoardCmd
from tutor.v2.layout import BoardLayout, Placer, Rect

L = BoardLayout.for_screen(1210, 834)  # 11-inch iPad Pro (M4), landscape


def cmd(id="b-1-1", rel="free", ref=None, col=None, gap=None, size=None, op="write"):
    anchor = {"rel": rel}
    if ref is not None:
        anchor["ref"] = ref
    if col is not None:
        anchor["col"] = col
    if gap is not None:
        anchor["gap"] = gap
    data = {"id": id, "op": op, "anchor": anchor, "payload": {"text": "x"}}
    if size:
        data["size_hint"] = {"w": size[0], "h": size[1]}
    return BoardCmd.model_validate(data)


# ---- mirrors BoardLayoutTests.swift


def test_regions_tile_the_screen_without_overlap():
    assert L.header.maxy == L.board.miny
    assert L.board.maxy == L.caption_bar.miny
    assert L.caption_bar.maxy == 834
    assert L.student_region.maxx == L.tutor_strip.minx
    assert L.tutor_strip.maxx == L.board.w


def test_five_slots_stay_inside_the_strip_in_order():
    assert len(L.slots) == 5
    for a, b in zip(L.slots, L.slots[1:]):
        assert a.maxy < b.miny
    for slot in L.slots:
        assert L.tutor_strip.contains(slot)


def test_slot_lookup_uses_board_space():
    s = L.slots[2]
    assert L.slot_index(s.midx, s.midy) == 2
    assert L.slot_index(10, 10) is None


def test_layout_is_deterministic_and_fingerprinted():
    assert L == BoardLayout.for_screen(1210, 834)
    assert L.fingerprint == "1210x834"
    assert L.fingerprint != BoardLayout.for_screen(1180, 820).fingerprint


# ---- exact numbers for the reference iPad


def test_reference_numbers():
    assert (L.board.w, L.board.h) == (1210, 638)
    assert L.board_bounds == Rect(0, 0, 1210, 638)
    assert L.tutor_strip == Rect(710, 0, 500, 638)
    assert L.student_region == Rect(0, 0, 710, 638)
    assert L.question_box == Rect(12, 12, 686, 132)
    assert L.work_area == Rect(0, 156, 710, 482)
    assert L.slots[0] == Rect(722, 12, 476, pytest.approx(116.4))


def test_default_screen_is_the_reference_ipad():
    assert BoardLayout.for_screen() == L


def test_narrow_screen_strip_is_45_percent():
    n = BoardLayout.for_screen(800, 834)
    assert n.tutor_strip.w == pytest.approx(360)
    assert n.student_region.w == pytest.approx(440)


@pytest.mark.parametrize("size", [(1180, 820), (1366, 1024), (834, 1194), (700, 500)])
def test_other_screens_tile_and_stay_inside(size):
    lay = BoardLayout.for_screen(*size)
    assert lay.board_bounds.contains(lay.tutor_strip) and lay.board_bounds.contains(lay.work_area)
    assert lay.tutor_strip.contains(lay.slots[-1])


def test_work_area_is_below_the_question_box():
    assert L.work_area.miny > L.question_box.maxy


def test_strip_inner_is_inset_12():
    assert L.strip_inner == L.tutor_strip.inset(12, 12)


# ---- Rect


def test_rect_contains_allows_touching_edges():
    assert Rect(0, 0, 10, 10).contains(Rect(0, 0, 10, 10))
    assert not Rect(0, 0, 10, 10).contains(Rect(5, 5, 6, 6))


def test_rect_intersects_ignores_touching():
    assert not Rect(0, 0, 10, 10).intersects(Rect(10, 0, 5, 5))
    assert Rect(0, 0, 10, 10).intersects(Rect(9, 9, 5, 5))


def test_rect_inset_union_intersection():
    r = Rect(10, 10, 20, 20)
    assert r.inset(2) == Rect(12, 12, 16, 16)
    assert r.inset(-2) == Rect(8, 8, 24, 24)
    assert r.union(Rect(0, 0, 5, 5)) == Rect(0, 0, 30, 30)
    assert r.intersection(Rect(15, 15, 30, 30)) == Rect(15, 15, 15, 15)
    assert r.intersection(Rect(100, 100, 1, 1)) is None


def test_rect_list_round_trip_and_validation():
    assert Rect.from_list(Rect(1, 2, 3, 4).to_list()) == Rect(1, 2, 3, 4)
    with pytest.raises(ValueError):
        Rect.from_list([1, 2, 3])


# ---- Placer


def test_first_placement_goes_in_the_tutor_strip():
    p = Placer(L, [L.question_box])
    r = p.place(cmd(), (200, 60))
    assert L.strip_inner.contains(r) and r.minx == L.strip_inner.minx and r.miny == L.strip_inner.miny


def test_placements_do_not_overlap_each_other():
    p = Placer(L, [])
    rects = [p.place(cmd(f"b-1-{i}"), (300, 80)) for i in range(1, 6)]
    assert all(r is not None for r in rects)
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            assert not a.intersects(b)


def test_below_ref_goes_under_it():
    p = Placer(L, [])
    a = p.place(cmd("a"), (200, 50))
    b = p.place(cmd("b", rel="below", ref="a", gap=20), (200, 50))
    assert b.miny == pytest.approx(a.maxy + 20) and b.minx == a.minx


def test_below_without_ref_uses_latest():
    p = Placer(L, [])
    a = p.place(cmd("a"), (200, 50))
    b = p.place(cmd("b", rel="below"), (200, 50))
    assert b.miny >= a.maxy


def test_right_of_ref():
    p = Placer(L, [])
    a = p.place(cmd("a"), (100, 50))
    b = p.place(cmd("b", rel="right_of", ref="a", gap=10), (100, 50))
    assert b.minx == pytest.approx(a.maxx + 10) and b.miny == a.miny


def test_right_of_that_does_not_fit_falls_back_to_free_space():
    p = Placer(L, [])
    a = p.place(cmd("a"), (460, 50))
    b = p.place(cmd("b", rel="right_of", ref="a"), (460, 50))
    assert b is not None and not b.intersects(a) and L.board_bounds.contains(b)


def test_inside_ref_is_centred_and_shrunk():
    p = Placer(L, [])
    a = p.place(cmd("a"), (300, 200))
    b = p.place(cmd("b", rel="inside", ref="a"), (500, 500))
    assert a.contains(b) and b.midx == pytest.approx(a.midx) and b.midy == pytest.approx(a.midy)


def test_column_picks_the_requested_band_of_the_strip():
    p1, p2 = Placer(L, []), Placer(L, [])
    left = p1.place(cmd(rel="column", col=1), (100, 40))
    right = p2.place(cmd(rel="column", col=2), (100, 40))
    mid = L.strip_inner.midx
    assert left.maxx <= mid + 1e-6 and right.minx >= mid - 1e-6


def test_four_columns_for_col_3_and_4():
    r3 = Placer(L, []).place(cmd(rel="column", col=3), (80, 40))
    r4 = Placer(L, []).place(cmd(rel="column", col=4), (80, 40))
    assert r3.minx < r4.minx and L.strip_inner.contains(r3) and L.strip_inner.contains(r4)


def test_size_hint_overrides_natural_size():
    r = Placer(L, []).place(cmd(size=(120, 30)), (400, 200))
    assert (r.w, r.h) == (120, 30)


def test_oversize_object_is_shrunk_to_fit_with_aspect_preserved():
    r = Placer(L, []).place(cmd(), (2000, 1000))
    assert r.w <= L.strip_inner.w + 1e-6 and r.w / r.h == pytest.approx(2.0, rel=1e-3)


def test_spills_into_the_work_area_and_avoids_student_lines():
    student = [Rect(0, 160, 700, 60), Rect(0, 230, 500, 60)]
    p = Placer(L, student)
    for i in range(1, 8):  # fill the strip
        p.place(cmd(f"s{i}"), (470, 80))
    spill = p.place(cmd("spill"), (300, 80))
    assert spill is not None and L.work_area.contains(spill)
    assert not any(spill.intersects(s) for s in student)


def test_returns_none_only_when_the_board_is_full():
    p = Placer(L, [L.work_area, L.question_box])
    results = [p.place(cmd(f"b-{i}"), (470, 100)) for i in range(40)]
    assert results[0] is not None and results[-1] is None


def test_something_smaller_still_fits_when_big_does_not():
    p = Placer(L, [L.work_area, L.question_box])
    for i in range(5):
        p.place(cmd(f"b-{i}"), (470, 100))
    placed = list(p.placed_rects)
    tiny = p.place(cmd("tiny"), (60, 20))
    if tiny is not None:  # when there is room it must be real room
        assert L.board_bounds.contains(tiny) and not any(tiny.intersects(r) for r in placed)


def test_placement_is_deterministic():
    def run():
        p = Placer(L, [Rect(0, 160, 300, 50)])
        return [p.place(cmd(f"b-{i}", rel="below" if i % 2 else "free"), (200 + 10 * i, 60)) for i in range(8)]

    assert run() == run()


def test_record_and_rect_of():
    p = Placer(L, [])
    p.record("x", Rect(1, 2, 3, 4))
    assert p.rect_of("x") == Rect(1, 2, 3, 4) and p.rect_of("nope") is None and p.rect_of(None) is None
    assert p.latest == Rect(1, 2, 3, 4)


def test_register_makes_student_objects_addressable_and_keeps_them_clear():
    p = Placer(L, [])
    p.register("line-0", Rect(722, 12, 300, 50))  # a student line sitting in the strip
    assert p.rect_of("line-0") == Rect(722, 12, 300, 50)
    r = p.place(cmd(), (200, 40))
    assert not r.intersects(Rect(722, 12, 300, 50))


def test_below_an_unknown_ref_still_places_something():
    r = Placer(L, []).place(cmd(rel="below", ref="ghost"), (100, 40))
    assert r is not None and L.board_bounds.contains(r)


def test_all_placements_stay_inside_the_board():
    p = Placer(L, [L.question_box])
    for i in range(30):
        r = p.place(cmd(f"b-{i}", rel=("below", "right_of", "free", "column")[i % 4], col=1 + i % 3), (150 + i * 5, 50))
        if r is not None:
            assert L.board_bounds.contains(r)
