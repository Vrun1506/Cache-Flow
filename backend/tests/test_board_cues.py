import pytest

from tutor.board.cues import cue_times, estimate_ms, resolve_cues
from tutor.schemas.board import BoardCmd


def c(id="b-1-1", at="start", text="", op="write", **kw):
    return BoardCmd.model_validate({"id": id, "op": op, "payload": {"text": text, **kw}, "cue": {"at": at}})


def test_estimate_ms_uses_2_6_words_per_second():
    assert estimate_ms("one two three four five") == round(5 / 2.6 * 1000)
    assert estimate_ms("") == 0 and estimate_ms("   ") == 0
    assert estimate_ms("hi") == 400  # floor for very short text


def test_cue_times_follow_the_spec():
    assert cue_times(2000, 6000) == {"start": 0, "after_intent": 2000, "mid_explain": 5000, "end": 7700}


def test_end_cue_never_negative():
    assert cue_times(0, 100)["end"] == 0


def test_resolve_sets_at_ms_for_every_cue():
    cmds = [c("a", "start"), c("b", "after_intent"), c("d", "mid_explain"), c("e", "end")]
    out = resolve_cues(cmds, 2000, 6000)
    assert [x.cue.at_ms for x in out] == [0, 2000, 5000, 7700]


def test_resolve_does_not_mutate_inputs():
    cmds = [c("a", "after_intent")]
    resolve_cues(cmds, 1000, 1000)
    assert cmds[0].cue.at_ms is None and cmds[0].cue.pen_ms is None


def test_pen_time_is_70ms_per_char_with_600_floor():
    out = resolve_cues([c("a", "start", "x" * 20), c("b", "mid_explain", "hi")], 4000, 20000)
    assert out[0].cue.pen_ms == 1400  # 20 chars
    assert out[1].cue.pen_ms == 600  # floor


def test_overlong_drawing_is_cut_to_the_window():
    # 100 chars would take 7 s but the next cue is 1 s later.
    out = resolve_cues([c("a", "start", "y" * 100), c("b", "after_intent", "z")], 1000, 9000)
    assert out[0].cue.pen_ms == 1000


def test_last_command_may_use_the_rest_of_the_clips():
    out = resolve_cues([c("a", "after_intent", "x" * 40)], 1000, 9000)
    assert out[0].cue.pen_ms == 2800  # well under the 9 s left


def test_end_cue_has_only_300ms_left():
    out = resolve_cues([c("a", "end", "x" * 40)], 1000, 5000)
    assert out[0].cue.pen_ms == 300


def test_commands_sharing_a_cue_each_get_the_full_pen_time():
    out = resolve_cues([c("a", "after_intent", "x" * 10), c("b", "after_intent", "x" * 10)], 1000, 9000)
    assert out[0].cue.pen_ms == out[1].cue.pen_ms == 700


def test_non_text_ops_get_the_minimum_pen_time():
    out = resolve_cues([c("a", "after_intent", op="highlight", ref="x")], 5000, 5000)
    assert out[0].cue.pen_ms == 600


def test_empty_command_list():
    assert resolve_cues([], 1000, 1000) == []


def test_zero_length_clips_put_everything_at_zero():
    out = resolve_cues([c("a", "after_intent", "x"), c("b", "end", "y")], 0, 0)
    assert [x.cue.at_ms for x in out] == [0, 0]
    assert all(x.cue.pen_ms == 0 for x in out)


def test_unknown_cue_name_starts_immediately():
    cmd = c("a", "start")
    object.__setattr__(cmd.cue, "at", "whenever")  # bypass validation on purpose
    assert resolve_cues([cmd], 2000, 2000)[0].cue.at_ms == 0


def test_unknown_anchor_refs_pass_through_untouched():
    cmd = BoardCmd.model_validate(
        {"id": "a", "op": "write", "anchor": {"rel": "below", "ref": "ghost"}, "payload": {"text": "hi"}}
    )
    assert resolve_cues([cmd], 1000, 1000)[0].anchor.ref == "ghost"


def test_estimated_flag_does_not_change_the_maths():
    a = resolve_cues([c("a", "mid_explain")], 1000, 3000, estimated=True)
    b = resolve_cues([c("a", "mid_explain")], 1000, 3000, estimated=False)
    assert a[0].cue.at_ms == b[0].cue.at_ms == 2500
