import json

import pytest

from quiz_samples import quiz_json
from sample_data import EXPLAIN_PLAN, EXPLAIN_SOLUTION, ORCHESTRATOR_OK, SOLUTION_OK
from tutor.agents.quiz_designer import generic_quiz, run_quiz_designer
from tutor.llm import FakeLLM, LLMError
from tutor.schemas import Plan, Solution
from tutor.schemas.quiz import QuizSet

pytestmark = pytest.mark.anyio

PLAN = Plan.model_validate_json(ORCHESTRATOR_OK)
SOLUTION = Solution.model_validate_json(SOLUTION_OK)


async def test_happy_path_request_shape():
    llm = FakeLLM({"quiz_designer": [quiz_json()]})
    quiz = await run_quiz_designer(llm, PLAN, SOLUTION, "secondary")
    assert [i.id for i in quiz.items] == ["q1", "q2"]
    call = llm.calls[0]
    assert (call.agent, call.tier, call.temperature, call.think, call.num_predict) == ("quiz_designer", "reasoner", 0.4, False, 900)
    assert "<question>" in call.user and "level: secondary" in call.user and "zero product rule" in call.user
    assert call.schema is not None


async def test_concise_mode_asks_for_less():
    llm = FakeLLM({"quiz_designer": [quiz_json()]})
    await run_quiz_designer(llm, PLAN, SOLUTION, "primary", concise=True)
    assert llm.calls[0].num_predict == 600


async def test_hints_that_state_the_answer_are_replaced():
    data = json.loads(quiz_json())
    data["items"][0]["expected"]["value"] = "6174"
    data["items"][0]["hints"] = ["Is it 6174?", "Think of Kaprekar: 6174 is the constant.", "It is 6174."]
    llm = FakeLLM({"quiz_designer": [json.dumps(data)]})
    quiz = await run_quiz_designer(llm, PLAN, SOLUTION, "secondary")
    hints = quiz.items[0].hints
    assert "6174" not in hints[0] and "6174" not in hints[1]
    assert hints[2] == "It is 6174."  # hint 3 is the reveal


async def test_answer_already_in_the_question_is_not_a_leak():
    data = json.loads(quiz_json())
    data["items"][0]["expected"]["value"] = "x**2+5*x+6=0"
    data["items"][0]["hints"] = ["Look at x**2+5*x+6=0 again.", "Which rule applies?", "Factorise it."]
    quiz = await run_quiz_designer(FakeLLM({"quiz_designer": [json.dumps(data)]}), PLAN, SOLUTION, "secondary")
    assert quiz.items[0].hints[0] == "Look at x**2+5*x+6=0 again."


async def test_one_retry_on_invalid_output_then_success():
    llm = FakeLLM({"quiz_designer": ["not json", quiz_json()]})
    quiz = await run_quiz_designer(llm, PLAN, SOLUTION, "secondary")
    assert len(llm.calls) == 2 and len(quiz.items) == 2


@pytest.mark.parametrize("script", [["x", "x"], [LLMError("down")], [RuntimeError("odd")], [quiz_json(items=[])] * 2])
async def test_any_failure_returns_the_generic_quiz(script):
    quiz = await run_quiz_designer(FakeLLM({"quiz_designer": script}), PLAN, SOLUTION, "secondary")
    assert quiz == generic_quiz(PLAN, SOLUTION)


def test_generic_quiz_is_valid_and_built_from_the_solution():
    quiz = generic_quiz(PLAN, SOLUTION)
    QuizSet.model_validate(quiz.model_dump())
    assert [i.type for i in quiz.items] == ["explain", "short"]  # the value "x=-2 or x=-3" is not a bare number
    assert "factorising" in quiz.items[0].expected.key_points
    assert quiz.items[1].expected.value == "x=-2 or x=-3"
    for item in quiz.items:
        assert len(item.hints) == 3
    # the two early hints of the recall question do not give the answer away
    assert "-2" not in quiz.items[1].hints[0] + quiz.items[1].hints[1]


def test_generic_quiz_numeric_recall():
    solution = Solution.model_validate(
        {
            "steps": [{"id": "s1", "title": "Subtract", "statement": "Take 3 from both sides."}],
            "final_answer": {"text": "x is 4.", "value": "4"},
            "key_concepts": ["inverse operations"],
        }
    )
    quiz = generic_quiz(PLAN, solution)
    assert quiz.items[1].type == "numeric" and quiz.items[1].expected.value == "4"


def test_generic_quiz_without_a_final_value_or_concepts():
    plan = Plan.model_validate_json(EXPLAIN_PLAN)
    solution = Solution.model_validate_json(EXPLAIN_SOLUTION)
    quiz = generic_quiz(plan, solution)
    assert [i.type for i in quiz.items] == ["explain", "explain"]
    assert quiz.items[0].skill  # falls back to the domain
