import pytest

from tutor.board import strokes as S
from tutor.board.strokes import attach_strokes, text_to_strokes
from tutor.schemas.board import BoardCmd

pytestmark = pytest.mark.skipif(S._font() is None, reason="Hershey-Fonts not installed")


def box(r):
    pts = [p for s in r["strokes"] for p in s]
    return min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)


def test_bounding_box_starts_at_origin_and_matches_metrics():
    r = text_to_strokes("x + 1 = 3")
    x0, y0, x1, y1 = box(r)
    assert x0 == 0 and y0 == 0
    assert x1 == pytest.approx(r["w"], abs=0.2) and y1 == pytest.approx(r["h"], abs=0.2)


def test_is_deterministic():
    assert text_to_strokes("(x+2)(x+3) = 0") == text_to_strokes("(x+2)(x+3) = 0")


def test_height_scales_output():
    small, big = text_to_strokes("x^2", 20), text_to_strokes("x^2", 40)
    assert big["h"] == pytest.approx(small["h"] * 2, rel=0.05)


def test_fraction_is_stacked():
    frac = text_to_strokes("1/2")
    plain = text_to_strokes("1 2")
    assert frac["h"] > plain["h"] * 1.5 and frac["w"] < plain["w"]


def test_exponent_is_raised_and_smaller():
    r = text_to_strokes("x^2")
    plain = text_to_strokes("x2")
    assert r["h"] > plain["h"]  # the exponent sits above the cap line


def test_sqrt_draws_a_radical_over_its_content():
    r = text_to_strokes("sqrt(x+1)")
    inner = text_to_strokes("x+1")
    assert r["w"] > inner["w"] and r["h"] > inner["h"]


def test_nested_pieces_do_not_crash():
    for text in ["(x+1)^2/3", "sqrt(1/2)", "x^2^3", "a/(b+c)^2", "1/", "/2", "^", "(", ")", "sqrt(", "x^"]:
        assert text_to_strokes(text) is not None, text


def test_unknown_characters_are_skipped():
    assert text_to_strokes("x ሴ y") is not None
    assert text_to_strokes("ሴ") is None


def test_blank_text_gives_none():
    assert text_to_strokes("") is None and text_to_strokes("  \n ") is None


def test_multiple_lines_stack_downward_and_cap_at_three():
    one, three = text_to_strokes("x"), text_to_strokes("x\nx\nx\nx\nx")
    assert three["h"] > one["h"] * 2
    assert three["h"] == pytest.approx(text_to_strokes("x\nx\nx")["h"])


def test_unicode_aliases():
    assert text_to_strokes("−3 × 4") is not None


def test_attach_strokes_fills_write_commands_only():
    cmds = [
        BoardCmd.model_validate({"id": "a", "op": "write", "payload": {"text": "x = 2"}}),
        BoardCmd.model_validate({"id": "b", "op": "highlight", "payload": {"ref": "a"}}),
    ]
    attach_strokes(cmds)
    assert cmds[0].payload.strokes and cmds[0].payload.metrics.w > 0
    assert cmds[1].payload.strokes is None


def test_missing_font_package_degrades_to_none(monkeypatch):
    monkeypatch.setattr(S, "_font", lambda: None)
    assert text_to_strokes("x") is None
    cmds = [BoardCmd.model_validate({"id": "a", "op": "write", "payload": {"text": "x"}})]
    assert attach_strokes(cmds)[0].payload.strokes is None
