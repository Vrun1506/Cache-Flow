import json

import pytest

from quiz_samples import item
from tutor.agents.hint import Action, advance, next_hint, run_feedback, templated_feedback, trim_words
from tutor.llm import FakeLLM, LLMError
from tutor.schemas.quiz import Verdict

pytestmark = pytest.mark.anyio

WRONG = Verdict(correct=False)
RIGHT = Verdict(correct=True)


@pytest.mark.parametrize(
    "misses,correct,expected",
    [
        (0, True, Action("next_step")),
        (1, True, Action("next_step")),
        (2, True, Action("next_step")),
        (3, True, Action("next_step")),
        (1, False, Action("hint", 1)),
        (2, False, Action("hint", 2)),
        (3, False, Action("reveal_and_advance", 3)),
        (4, False, Action("reveal_and_advance", 3)),  # never loops past the reveal
        (0, False, Action("hint", 1)),  # a wrong answer is at least one miss
    ],
)
def test_advance_table(misses, correct, expected):
    assert advance(misses, correct) == expected


def test_ladder_always_ends():
    misses, steps = 0, 0
    while True:
        misses += 1
        steps += 1
        if advance(misses, False).kind != "hint":
            break
        assert steps < 10
    assert steps == 3


def test_next_hint_uses_the_written_hints():
    it = item()
    assert [next_hint(it, n).say for n in (1, 2, 3)] == it.hints
    assert next_hint(it, 1).level == 1 and next_hint(it, 3).level == 3
    assert next_hint(it, 0).say == it.hints[0] and next_hint(it, 9).say == it.hints[2]


async def test_feedback_from_the_model_is_trimmed_to_25_words():
    long = " ".join(["word"] * 40)
    llm = FakeLLM({"feedback": [json.dumps({"feedback": long})]})
    out = await run_feedback(llm, item(), WRONG)
    assert len(out.split()) <= 25 and out.endswith(".")
    call = llm.calls[0]
    assert (call.agent, call.tier, call.temperature, call.think, call.num_predict) == ("feedback", "reasoner", 0.4, False, 400)


async def test_wrong_answer_feedback_context_hides_the_answer():
    llm = FakeLLM({"feedback": [json.dumps({"feedback": "Have another go."})]})
    await run_feedback(llm, item(value="6174"), WRONG)
    assert "6174" not in llm.calls[0].user
    llm2 = FakeLLM({"feedback": [json.dumps({"feedback": "Right, it is 6174."})]})
    out = await run_feedback(llm2, item(value="6174"), WRONG, reveal=True)
    assert "6174" in llm2.calls[0].user and "6174" in out


async def test_feedback_that_leaks_the_answer_is_replaced():
    llm = FakeLLM({"feedback": [json.dumps({"feedback": "The answer is 6174, try again."})]})
    out = await run_feedback(llm, item(value="6174"), WRONG)
    assert "6174" not in out


@pytest.mark.parametrize("script", [["junk", "junk"], [LLMError("down")], [RuntimeError("x")]])
async def test_feedback_falls_back_to_a_template(script):
    out = await run_feedback(FakeLLM({"feedback": script}), item(), WRONG)
    assert out and len(out.split()) <= 25


def test_templates_cover_every_verdict_and_are_stable():
    it = item()
    texts = {templated_feedback(it, v) for v in (RIGHT, Verdict(correct=False, partial=True), WRONG)}
    assert len(texts) == 3
    assert templated_feedback(it, WRONG) == templated_feedback(it, WRONG)


def test_trim_words():
    assert trim_words("one two three", 25) == "one two three"
    assert trim_words("a b c d e", 3) == "a b c."
