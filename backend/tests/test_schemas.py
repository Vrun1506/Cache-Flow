import json

import pytest
from pydantic import ValidationError

from tutor.prompts import load_prompt
from tutor.schemas import Plan, PlanDraft, Solution, VerifyReport, inline_schema, trim_sentence


def step(i, **kw):
    base = {"id": f"s{i}", "title": "t", "statement": "do a thing"}
    base.update(kw)
    return base


def solution(n=2, **kw):
    data = {"steps": [step(i) for i in range(1, n + 1)], "final_answer": {"text": "42"}}
    data.update(kw)
    return data


def test_trim_prefers_sentence_boundary():
    text = "First sentence is here. Second sentence goes on and on and on forever."
    assert trim_sentence(text, 40) == "First sentence is here."


def test_trim_falls_back_to_word_boundary_with_ellipsis():
    out = trim_sentence("word " * 50, 30)
    assert len(out) <= 30 and out.endswith("…")


def test_trim_leaves_short_text_alone():
    assert trim_sentence("  short  ", 40) == "short"


def test_soft_fields_truncate_instead_of_failing():
    s = Solution.model_validate(solution(1, steps=[step(1, statement="A sentence. " * 40)]))
    assert len(s.steps[0].statement) <= 200


def test_lists_are_capped_not_rejected():
    plan = PlanDraft.model_validate(
        {
            "domain": "physics",
            "question_clean": "q",
            "solution_kind": "explain",
            "visuals": [{"concept": f"c{i}", "kind_hint": "plot"} for i in range(6)],
        }
    )
    assert len(plan.visuals) == 3


def test_hard_fields_are_rejected_when_too_long():
    with pytest.raises(ValidationError):
        Solution.model_validate(solution(1, steps=[step(1, expr="x" * 300)]))


def test_step_ids_must_be_sequential():
    bad = solution(2, steps=[step(1), step(3)])
    with pytest.raises(ValidationError):
        Solution.model_validate(bad)


def test_more_than_eight_steps_is_structural_error():
    with pytest.raises(ValidationError):
        Solution.model_validate(solution(9))


def test_unknown_keys_are_ignored():
    p = PlanDraft.model_validate(
        {"domain": "math", "question_clean": "q", "solution_kind": "compute", "surprise": 1}
    )
    assert not hasattr(p, "surprise")


def test_bad_enum_is_rejected():
    with pytest.raises(ValidationError):
        PlanDraft.model_validate({"domain": "astrology", "question_clean": "q", "solution_kind": "compute"})


def test_plan_adds_template_and_draft_schema_excludes_it():
    assert "template" not in inline_schema(PlanDraft)["properties"]
    assert Plan.model_validate({"domain": "math", "question_clean": "q", "solution_kind": "compute"}).template == "solve_teach"


def test_inline_schema_has_no_refs_and_requires_all_fields():
    schema = inline_schema(Solution)
    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in text
    assert set(schema["required"]) == set(schema["properties"])
    step_schema = schema["properties"]["steps"]["items"]
    assert set(step_schema["required"]) == set(step_schema["properties"])


def test_verify_report_failed_ids():
    report = VerifyReport.model_validate(
        {
            "steps": [
                {"id": "s1", "status": "verified", "method": "sympy"},
                {"id": "s2", "status": "failed", "method": "sympy", "note": "bad"},
            ],
            "final_answer": {"id": "final", "status": "failed", "method": "sympy"},
            "overall": "partial",
        }
    )
    assert report.failed_ids == ["s2", "final"]


@pytest.mark.parametrize("name", ["orchestrator", "solver", "verifier_critic"])
def test_prompts_load_with_versions(name):
    prompt = load_prompt(name)
    assert prompt.version == "1" and len(prompt.text) > 200
    # Each prompt has to stay small enough for the 8,192 token budget.
    assert len(prompt.text) / 3 < 900


def test_property_names_survive_inlining():
    """A field called "title" must not be mistaken for a JSON-schema annotation."""
    step = inline_schema(Solution)["properties"]["steps"]["items"]
    assert "title" in step["properties"] and "title" in step["required"]
    assert {"id", "statement", "rationale", "expr"} <= set(step["properties"])


def test_wire_schema_has_no_regex_patterns():
    """Ollama's grammar builder rejects `pattern` ("failed to parse grammar")."""
    for model in (PlanDraft, Solution, VerifyReport):
        assert "pattern" not in json.dumps(inline_schema(model))


def test_pydantic_still_enforces_the_pattern_that_the_wire_schema_drops():
    with pytest.raises(ValidationError):
        Solution.model_validate(solution(1, steps=[step(1, id="one")]))
