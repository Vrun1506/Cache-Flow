import json

import pytest

from tutor.agents.animator import run_animator
from tutor.llm import FakeLLM, LLMTimeout
from tutor.prompts import load_prompt
from tutor.scenes import validate_scene
from tutor.schemas import Visual

pytestmark = pytest.mark.anyio


def v(concept="parabola crossing the x axis", kind="plot"):
    return Visual(concept=concept, kind_hint=kind)


def reply(template, params, title="Scene", captions=None):
    return json.dumps({"template": template, "title": title, "params": params, "captions": captions or []})


async def test_good_draft_becomes_a_validated_scene():
    llm = FakeLLM({"animator": [reply("fn_plot", {"expr": "x^2+5x+6", "x_min": -6, "x_max": 1, "points": [-3, -2]},
                                      captions=["The curve crosses at -3 and -2."])]})
    spec = await run_animator(llm, v(), ["roots are -3 and -2"], "secondary")
    assert spec.kind == "plot" and validate_scene(spec) == []
    curve = next(o for o in spec.objects if o.type == "curve")
    assert curve.props["expr"] == "x^2 + 5*x + 6"
    assert [c.caption for c in spec.cues] == ["The curve crosses at -3 and -2."]


async def test_request_shape():
    llm = FakeLLM({"animator": [reply("concept_card", {"bullets": ["a"]})]})
    await run_animator(llm, v(kind="diagram"), ["fact " + "x" * 300] * 9, "university")
    req = llm.calls[0]
    assert (req.agent, req.tier, req.think, req.temperature, req.num_predict) == ("animator", "router", False, 0.2, 1200)
    assert req.prompt_version == "1" and req.schema and "pattern" not in json.dumps(req.schema)
    assert "kind_hint: diagram" in req.user and "level: university" in req.user
    assert req.user.count("\n- ") == 6 and all(len(line) <= 102 for line in req.user.splitlines())


async def test_concise_mode_halves_the_budget():
    llm = FakeLLM({"animator": [reply("concept_card", {"bullets": ["a"]})]})
    await run_animator(llm, v(), [], concise=True)
    assert llm.calls[0].num_predict == 600 and "Keep it minimal" in llm.calls[0].user


async def test_invalid_json_twice_gives_a_concept_card_from_the_facts():
    llm = FakeLLM({"animator": ["not json", "{"]})
    spec = await run_animator(llm, v("how a parabola behaves"), ["Opens upward when a > 0", "Vertex at x = -b/2a"])
    assert len(llm.calls) == 2  # one retry, then the fallback
    assert spec.kind == "diagram" and validate_scene(spec) == []
    assert spec.title == "how a parabola behaves"
    assert [o.props["text"] for o in spec.objects if o.id.startswith("bl")] == [
        "Opens upward when a > 0", "Vertex at x = -b/2a",
    ]


async def test_retry_recovers_from_one_bad_reply():
    llm = FakeLLM({"animator": ["oops", reply("projectile", {"v0": 20, "angle": 30})]})
    spec = await run_animator(llm, v("a thrown ball", "motion"), [])
    assert spec.kind == "motion" and len(llm.calls) == 2


async def test_model_outage_falls_back_without_raising():
    llm = FakeLLM({"animator": [LLMTimeout("slow")]})
    spec = await run_animator(llm, v("a thrown ball", "motion"), [], scene_id="scene-3")
    assert spec.kind == "diagram" and spec.id == "scene-3"
    assert [o.props["text"] for o in spec.objects if o.id.startswith("bl")] == ["a thrown ball"]


async def test_out_of_range_numbers_are_clamped_not_failed():
    llm = FakeLLM({"animator": [reply("projectile", {"v0": 9999, "angle": 200, "g": -3})]})
    spec = await run_animator(llm, v("a rocket", "motion"), [])
    assert spec.kind == "motion" and validate_scene(spec) == []


async def test_unusable_params_use_the_hint_template_then_the_card():
    llm = FakeLLM({"animator": [reply("fn_plot", {"expr": "ln(x)", "x_min": -5, "x_max": -1}, "Log curve")]})
    spec = await run_animator(llm, v("log curve", "plot"), ["ln is undefined for x <= 0"])
    assert spec.kind == "diagram"
    assert [o.props["text"] for o in spec.objects if o.id.startswith("bl")] == ["ln is undefined for x <= 0"]


async def test_template_choice_can_differ_from_the_hint():
    llm = FakeLLM({"animator": [reply("bar_compare", {"labels": ["a", "b"], "values": [3, 5]})]})
    spec = await run_animator(llm, v("compare two groups", "plot"), [])
    assert spec.kind == "bar"


def test_prompt_loads_with_version_and_fits_the_budget():
    prompt = load_prompt("animator")
    assert prompt.version == "1" and len(prompt.text) > 200
    assert len(prompt.text) / 3 < 900
    for template in ("fn_plot", "projectile", "array_walk", "graph_traverse", "number_line", "bar_compare",
                     "timeline", "concept_card"):
        assert template in prompt.text
