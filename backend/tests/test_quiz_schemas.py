import json

import pytest
from pydantic import ValidationError

from quiz_samples import quiz_json
from tutor.prompts import load_prompt
from tutor.schemas import inline_schema
from tutor.schemas.quiz import Expected, Hint, QuizItem, QuizSet, Verdict


def raw_item(**kw):
    base = {
        "type": "numeric", "prompt": "q", "expected": {"value": "6"},
        "hints": ["a", "b", "c"],
    }
    base.update(kw)
    return base


def test_valid_quiz_parses_and_renumbers_ids():
    data = json.loads(quiz_json())
    data["items"][0]["id"] = "Q9"
    quiz = QuizSet.model_validate(data)
    assert [i.id for i in quiz.items] == ["q1", "q2"]


def test_hints_must_be_exactly_three():
    with pytest.raises(ValidationError):
        QuizItem.model_validate(raw_item(hints=["a", "b"]))
    assert len(QuizItem.model_validate(raw_item(hints=["a", "b", "c", "d"])).hints) == 3


def test_quiz_has_two_or_three_items_extra_are_capped():
    items = json.loads(quiz_json())["items"]
    with pytest.raises(ValidationError):
        QuizSet.model_validate({"items": items[:1]})
    assert len(QuizSet.model_validate({"items": items + items}).items) == 3


def test_mcq_needs_options_and_an_answer():
    with pytest.raises(ValidationError):
        QuizItem.model_validate(raw_item(type="mcq", expected={"option_index": 0}))
    with pytest.raises(ValidationError):
        QuizItem.model_validate(raw_item(type="mcq", options=["a", "b"], expected={}))
    with pytest.raises(ValidationError):
        QuizItem.model_validate(raw_item(type="mcq", options=["a", "b"], expected={"option_index": 3}))


def test_mcq_index_is_derived_from_the_value():
    q = QuizItem.model_validate(raw_item(type="mcq", options=["red", "Green"], expected={"value": "green"}))
    assert q.expected.option_index == 1


def test_numeric_and_explain_need_their_expectation():
    with pytest.raises(ValidationError):
        QuizItem.model_validate(raw_item(expected={}))
    with pytest.raises(ValidationError):
        QuizItem.model_validate(raw_item(type="explain", expected={"value": "x"}))


def test_blank_lists_count_as_missing():
    q = QuizItem.model_validate(raw_item(options=[], expected={"value": "6", "key_points": []}))
    assert q.options is None and q.expected.key_points is None


def test_long_text_is_truncated_not_rejected():
    q = QuizItem.model_validate(raw_item(prompt="A sentence. " * 60, hints=["x" * 400, "b", "c"]))
    assert len(q.prompt) <= 200 and len(q.hints[0]) <= 160


def test_unknown_keys_ignored_and_bad_type_rejected():
    assert not hasattr(QuizItem.model_validate(raw_item(extra=1)), "extra")
    with pytest.raises(ValidationError):
        QuizItem.model_validate(raw_item(type="essay"))


def test_verdict_and_hint_shapes():
    v = Verdict.model_validate({"correct": False, "matched_points": ["a"] * 9})
    assert len(v.matched_points) == 4 and v.partial is False
    with pytest.raises(ValidationError):
        Hint.model_validate({"level": 4, "say": "x"})
    assert len(Hint.model_validate({"level": 1, "say": "x", "board": [{}, {}, {}]}).board) == 2


def test_wire_schemas_have_no_patterns_and_require_every_field():
    for model in (QuizSet, Verdict, Hint):
        schema = inline_schema(model)
        assert "pattern" not in json.dumps(schema) and "$ref" not in json.dumps(schema)
        assert set(schema["required"]) == set(schema["properties"])


@pytest.mark.parametrize("name", ["quiz_designer", "hint", "judge"])
def test_prompts_load_with_versions_and_fit_the_budget(name):
    prompt = load_prompt(name)
    assert prompt.version == "1" and len(prompt.text) > 200
    assert len(prompt.text) / 3 < 900
