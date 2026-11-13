import json

import pytest
from pydantic import ValidationError

from sample_data import ORCHESTRATOR_OK, SOLUTION_OK
from tutor.agents.lesson_designer import (
    FAILED_NOTE,
    UNVERIFIED_NOTE,
    batches,
    fallback_step,
    run_lesson_designer,
)
from tutor.llm import FakeLLM, LLMTimeout
from tutor.prompts import load_prompt
from tutor.schemas import Plan, Solution, inline_schema
from tutor.schemas.lesson import LessonBatch
from tutor.state.memory import Memory

pytestmark = pytest.mark.anyio


def entry(sid="s1", **kw):
    data = {
        "step_id": sid,
        "intent": "We find a pair of numbers.",
        "explain": "Two and three multiply to six and add to five.",
        "check": {
            "type": "numeric",
            "prompt": "What is two times three?",
            "options": None,
            "expected": {"value": "6", "key_points": None, "option_index": None},
            "hints": ["What is 2 groups of 3?", "Multiplication.", "Two times three is six."],
        },
    }
    data.update(kw)
    return data


def batch(*sids, **kw):
    return json.dumps({"steps": [entry(s, **kw) for s in sids]})


def memory(verified=None) -> Memory:
    m = Memory()
    m.set_solution(
        Plan.model_validate_json(ORCHESTRATOR_OK),
        Solution.model_validate_json(SOLUTION_OK),
        verified or {"s1": "verified", "s2": "verified", "s3": "verified"},
    )
    return m


async def test_good_batch_returns_one_entry_per_step_in_order():
    llm = FakeLLM({"lesson_designer": [batch("s1", "s2", "s3")]})
    out = await run_lesson_designer(llm, memory(), ["s1", "s2", "s3"])
    assert [s.step_id for s in out] == ["s1", "s2", "s3"]
    req = llm.calls[0]
    assert req.tier == "reasoner" and req.think is False and "<steps>s1, s2, s3</steps>" in req.user


async def test_invalid_output_falls_back_to_a_plain_lesson_for_every_step():
    llm = FakeLLM({"lesson_designer": ["nope", "nope"]})
    out = await run_lesson_designer(llm, memory(), ["s1", "s2"])
    assert [s.step_id for s in out] == ["s1", "s2"]
    assert all(len(s.check.hints) == 3 and s.check.type == "explain" for s in out)


async def test_model_outage_also_falls_back():
    llm = FakeLLM({"lesson_designer": [LLMTimeout("t"), LLMTimeout("t")]})
    out = await run_lesson_designer(llm, memory(), ["s1"])
    assert out[0].step_id == "s1"


async def test_missing_step_is_filled_from_the_fallback():
    llm = FakeLLM({"lesson_designer": [batch("s1")]})
    out = await run_lesson_designer(llm, memory(), ["s1", "s2"])
    assert [s.step_id for s in out] == ["s1", "s2"]
    assert out[1].check.type == "explain"  # fallback


async def test_mislabelled_ids_are_repaired_by_position():
    llm = FakeLLM({"lesson_designer": [batch("s7", "s8")]})
    out = await run_lesson_designer(llm, memory(), ["s1", "s2"])
    assert [s.step_id for s in out] == ["s1", "s2"]
    assert out[0].check.type == "numeric"  # the model's content was kept


async def test_unverified_and_failed_steps_always_carry_a_warning():
    m = memory({"s1": "unverified", "s2": "failed", "s3": "verified"})
    llm = FakeLLM({"lesson_designer": [batch("s1", "s2", "s3")]})
    out = await run_lesson_designer(llm, m, ["s1", "s2", "s3"])
    assert UNVERIFIED_NOTE in out[0].explain
    assert FAILED_NOTE in out[1].explain
    assert UNVERIFIED_NOTE not in out[2].explain and FAILED_NOTE not in out[2].explain


async def test_prompt_slice_carries_verification_state_and_stays_inside_budget():
    llm = FakeLLM({"lesson_designer": [batch("s1")]})
    await run_lesson_designer(llm, memory({"s1": "unverified"}), ["s1"])
    assert "s1 [unverified]" in llm.calls[0].user


async def test_batches_are_capped_at_three_steps():
    llm = FakeLLM({"lesson_designer": [batch("s1", "s2", "s3")]})
    await run_lesson_designer(llm, memory(), ["s1", "s2", "s3", "s4", "s5"])
    assert "<steps>s1, s2, s3</steps>" in llm.calls[0].user


async def test_concise_mode_halves_the_output_budget():
    llm = FakeLLM({"lesson_designer": [batch("s1")]})
    await run_lesson_designer(llm, memory(), ["s1"], concise=True)
    assert llm.calls[0].num_predict < 1500


def test_batches_helper_groups_by_three():
    sol = Solution.model_validate_json(SOLUTION_OK)
    assert batches(sol) == [["s1", "s2", "s3"]]
    assert batches(sol, 2) == [["s1", "s2"], ["s3"]]


def test_fallback_step_is_valid_and_has_three_hints():
    sol = Solution.model_validate_json(SOLUTION_OK)
    lesson = fallback_step(sol.steps[1])
    assert lesson.check.hints and len(lesson.check.hints) == 3


def test_schema_requires_three_hints_but_trims_four():
    four = entry()["check"] | {"hints": ["a", "b", "c", "d"]}
    ok = LessonBatch.model_validate({"steps": [entry(check=four)]})
    assert len(ok.steps[0].check.hints) == 3
    two = entry()["check"] | {"hints": ["a", "b"]}
    with pytest.raises(ValidationError):
        LessonBatch.model_validate({"steps": [entry(check=two)]})


def test_overlong_speech_is_truncated_not_rejected():
    b = LessonBatch.model_validate({"steps": [entry(explain="A long sentence. " * 60)]})
    assert len(b.steps[0].explain) <= 420


def test_wire_schema_is_clean():
    text = json.dumps(inline_schema(LessonBatch))
    assert "pattern" not in text and "$ref" not in text


def test_prompt_loads_and_fits():
    p = load_prompt("lesson_designer")
    assert p.version == "1" and len(p.text) / 3 < 900


def test_check_converts_to_a_quiz_item():
    from tutor.agents.lesson_designer import check_to_item

    sol = Solution.model_validate_json(SOLUTION_OK)
    lesson = LessonBatch.model_validate({"steps": [entry("s2")]}).steps[0]
    item = check_to_item(lesson, sol.steps[1])
    assert item.id == "c2" and item.type == "numeric" and item.expected.value == "6"


def test_inconsistent_mcq_degrades_to_explain_instead_of_failing():
    from tutor.agents.lesson_designer import check_to_item

    sol = Solution.model_validate_json(SOLUTION_OK)
    bad = entry("s1")["check"] | {"type": "mcq", "options": ["a", "b"], "expected": {"option_index": 3}}
    lesson = LessonBatch.model_validate({"steps": [entry("s1", check=bad)]}).steps[0]
    item = check_to_item(lesson, sol.steps[0])
    assert item.type == "explain" and item.expected.key_points
