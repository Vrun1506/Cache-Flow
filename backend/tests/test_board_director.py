import json

import pytest

from tutor.agents.board_director import fallback_board, run_board_director, validate_plan
from tutor.llm import FakeLLM, LLMTimeout
from tutor.prompts import load_prompt
from tutor.schemas.board import BoardPlan

pytestmark = pytest.mark.anyio

RECENT = [{"id": "b-1-1", "kind": "write", "label": "x^2 + 5x + 6 = 0"}]


def plan(*cmds):
    return json.dumps({"commands": list(cmds)})


def w(id, text="(x+2)(x+3) = 0", at="after_intent", **kw):
    return {"id": id, "op": "write", "anchor": {"rel": "below"}, "payload": {"text": text}, "cue": {"at": at}, **kw}


def hl(id, ref, at="end"):
    return {"id": id, "op": "highlight", "anchor": {"rel": "inside", "ref": ref}, "payload": {"ref": ref}, "cue": {"at": at}}


async def run(script, **kw):
    llm = FakeLLM({"board_director": script})
    kw.setdefault("recent_index", RECENT)
    result = await run_board_director(llm, 2, "Factorise", ["(x+2)(x+3) = 0"], "Use the pair.", "Two brackets.", **kw)
    return llm, result


def test_prompt_loads_and_fits_the_budget():
    p = load_prompt("board_director")
    assert p.version == "1" and len(p.text) / 3 < 900
    for rule in ("relative anchors", "never give coordinates", "at most 3 lines"):
        assert rule.lower() in p.text.lower()


async def test_good_plan_is_returned_and_renumbered():
    llm, result = await run([plan(w("a"), hl("z", "a"))])
    assert [c.id for c in result.commands] == ["b-2-1", "b-2-2"]
    assert result.commands[1].payload.ref == "b-2-1" and result.commands[1].anchor.ref == "b-2-1"
    req = llm.calls[0]
    assert req.agent == "board_director" and req.tier == "router"
    assert req.temperature == 0.2 and req.think is False and req.num_predict == 1200
    assert req.schema["properties"]["commands"]["items"]["required"]
    assert "<narration>" in req.user and "b-1-1 (write)" in req.user and "Factorise" in req.user


async def test_concise_mode_halves_the_budget():
    llm, _ = await run([plan(w("a"))], concise=True)
    assert llm.calls[0].num_predict == 600 and "at most 3 commands" in llm.calls[0].user


async def test_refs_to_recent_objects_are_kept():
    _, result = await run([plan(w("a"), hl("b", "b-1-1"))])
    assert [c.id for c in result.commands] == ["b-2-1", "b-2-2"]
    assert result.commands[1].payload.ref == "b-1-1"


async def test_commands_with_unknown_refs_are_dropped():
    _, result = await run([plan(w("a"), hl("b", "ghost"), hl("c", "a"))])
    assert [c.op for c in result.commands] == ["write", "highlight"]
    assert result.commands[1].payload.ref == "b-2-1"
    assert [c.id for c in result.commands] == ["b-2-1", "b-2-2"]


async def test_a_reference_to_a_dropped_command_is_dropped_too():
    _, result = await run([plan(w("a"), hl("b", "ghost"), hl("c", "b"))])
    assert [c.op for c in result.commands] == ["write"]


async def test_forward_references_are_dropped():
    _, result = await run([plan(hl("a", "later"), w("later"))])
    assert [c.op for c in result.commands] == ["write"]


async def test_arrow_and_group_refs_are_validated_and_rewritten():
    arrow = {"id": "ar", "op": "arrow", "payload": {"from": "a", "to": "b-1-1"}, "cue": {"at": "mid_explain"}}
    bad_arrow = {"id": "ar2", "op": "arrow", "payload": {"from": "a", "to": "nope"}}
    group = {"id": "g", "op": "group", "payload": {"refs": ["a", "b-1-1"]}}
    _, result = await run([plan(w("a"), arrow, bad_arrow, group)])
    assert [c.op for c in result.commands] == ["write", "arrow", "group"]
    assert result.commands[1].payload.from_ == "b-2-1" and result.commands[1].payload.to == "b-1-1"
    assert result.commands[2].payload.refs == ["b-2-1", "b-1-1"]


async def test_relative_anchor_without_ref_is_allowed():
    _, result = await run([plan(w("a"))])
    assert result.commands[0].anchor.ref is None and result.commands[0].anchor.rel == "below"


async def test_everything_dropped_falls_back():
    _, result = await run([plan(hl("a", "ghost"))])
    assert [c.op for c in result.commands] == ["write", "highlight"]
    assert result.commands[0].payload.text == "(x+2)(x+3) = 0"


async def test_invalid_json_retries_then_falls_back():
    llm, result = await run(["not json", "still not json"])
    assert len(llm.calls) == 2
    assert result.commands[0].id == "b-2-1" and result.commands[0].op == "write"


async def test_invalid_then_valid_uses_the_retry():
    llm, result = await run(["{}{", plan(w("a"))])
    assert len(llm.calls) == 2 and result.commands[0].payload.text == "(x+2)(x+3) = 0"


async def test_model_outage_falls_back_without_raising():
    _, result = await run([LLMTimeout("slow")])
    assert [c.op for c in result.commands] == ["write", "highlight"]


async def test_cancellation_is_not_swallowed():
    import asyncio

    llm = FakeLLM({"board_director": [plan(w("a"))]}, delay=5)
    task = asyncio.create_task(run_board_director(llm, 1, "t", [], "i", "e"))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_prompt_only_sees_the_last_six_recent_objects():
    recent = [{"id": f"b-0-{i}", "kind": "write", "label": f"obj{i}"} for i in range(1, 10)]
    llm, _ = await run([plan(w("a"))], recent_index=recent)
    user = llm.calls[0].user
    assert "obj9" in user and "obj4" in user and "obj3" not in user


async def test_empty_board_is_described():
    llm, _ = await run([plan(w("a"))], recent_index=[])
    assert "(empty)" in llm.calls[0].user


def test_fallback_is_deterministic_and_well_formed():
    a = fallback_board(3, ["line one", "line two", "l3", "l4"], "Title")
    assert a == fallback_board(3, ["line one", "line two", "l3", "l4"], "Title")
    assert [c.id for c in a.commands] == ["b-3-1", "b-3-2"]
    assert a.commands[0].payload.text == "line one\nline two\nl3"
    assert a.commands[0].anchor.rel == "below" and a.commands[0].anchor.ref is None
    assert a.commands[1].cue.at == "end" and a.commands[1].payload.ref == "b-3-1"


def test_fallback_uses_the_title_when_there_is_no_board_text():
    assert fallback_board(1, [], "Zero product rule").commands[0].payload.text == "Zero product rule"
    assert fallback_board(1, ["", "  "], "T").commands[0].payload.text == "T"


def test_fallback_trims_long_lines():
    text = fallback_board(1, ["y" * 100], "T").commands[0].payload.text
    assert len(text) == 48


def test_validate_plan_leaves_clean_plans_alone_apart_from_ids():
    p = BoardPlan.model_validate({"commands": [json.loads(json.dumps(w("q")))]})
    assert validate_plan(p, 5, set()).commands[0].id == "b-5-1"
