import json

import pytest
from pydantic import ValidationError

from tutor.schemas import inline_schema
from tutor.schemas.board import (
    BoardCmd,
    BoardPlan,
    ClipTiming,
    board_id,
    board_llm_schema,
)


def cmd(op="write", id="b-1-1", **kw):
    return {"id": id, "op": op, **kw}


def write(text, id="b-1-1", **kw):
    return cmd("write", id, payload={"text": text}, **kw)


def test_minimal_command_gets_defaults():
    c = BoardCmd.model_validate(cmd("write"))
    assert c.anchor.rel == "free" and c.style.color == "agent" and c.cue.at == "start"


def test_unknown_op_is_rejected():
    with pytest.raises(ValidationError):
        BoardCmd.model_validate(cmd("teleport"))


def test_unknown_keys_are_ignored():
    c = BoardCmd.model_validate(cmd("write", surprise=1, payload={"text": "hi", "zzz": 2}))
    assert c.payload.text == "hi"


def test_gap_and_col_are_clamped():
    c = BoardCmd.model_validate(cmd(anchor={"rel": "column", "col": 9, "gap": 500}))
    assert c.anchor.col == 4 and c.anchor.gap == 48


def test_soft_text_truncates():
    c = BoardCmd.model_validate(write("A sentence here. " * 30))
    assert len(c.payload.text) <= 120


def test_plot_expression_is_a_hard_limit():
    with pytest.raises(ValidationError):
        BoardCmd.model_validate(cmd("plot", payload={"expr": "x+" * 80}))


def test_arrow_uses_from_alias_and_round_trips():
    c = BoardCmd.model_validate(cmd("arrow", payload={"from": "a", "to": "b"}))
    assert c.payload.from_ == "a"
    dumped = c.model_dump(by_alias=True, exclude_none=True)
    assert dumped["payload"] == {"from": "a", "to": "b"}


def test_more_than_twelve_commands_are_capped():
    plan = BoardPlan.model_validate({"commands": [cmd("highlight", f"b-1-{i}") for i in range(30)]})
    assert len(plan.commands) == 12


def test_text_cap_drops_least_important_text_commands_first():
    cmds = [write("x" * 100, f"b-1-{i}") for i in range(1, 6)]  # 500 chars
    cmds.append(cmd("table", "b-1-6", payload={"rows": [["a" * 24] * 4]}))  # 96 chars
    cmds.append(cmd("highlight", "b-1-7", payload={"ref": "b-1-1"}))
    plan = BoardPlan.model_validate({"commands": cmds})
    assert sum(c.text_chars for c in plan.commands) <= 400
    ops = [c.op for c in plan.commands]
    assert "highlight" in ops  # no text, so never a reason to drop it
    assert "table" not in ops  # dropped before any write


def test_a_single_oversized_write_is_trimmed_not_dropped():
    big = [write("word " * 24, f"b-1-{i}") for i in range(1, 6)]
    plan = BoardPlan.model_validate({"commands": big})
    assert sum(c.text_chars for c in plan.commands) <= 400
    assert len(plan.commands) >= 1 and all(c.op == "write" for c in plan.commands)


def test_table_rows_are_capped():
    c = BoardCmd.model_validate(cmd("table", payload={"rows": [["a"] * 9] * 9}))
    assert len(c.payload.rows) == 6 and len(c.payload.rows[0]) == 4


def test_board_id_format():
    assert board_id(3, 2) == "b-3-2"


def test_clip_timing_total():
    t = ClipTiming(1000, 2500, estimated=True)
    assert t.total_ms == 3500 and t.estimated


def test_llm_schema_is_small_and_has_no_server_fields_or_patterns():
    schema = board_llm_schema()
    text = json.dumps(schema)
    for banned in ("strokes", "metrics", "at_ms", "pen_ms", "pattern", "$ref", "$defs"):
        assert banned not in text
    item = schema["properties"]["commands"]["items"]
    assert set(item["required"]) == {"id", "op", "anchor", "payload", "cue"}
    assert "from" in item["properties"]["payload"]["properties"]
    assert len(text) < 4500


def test_full_schema_still_has_server_fields_for_the_ipad_contract():
    assert "strokes" in json.dumps(inline_schema(BoardPlan))
