import json

import pytest
from pydantic import ValidationError

from tutor.schemas import inline_schema
from tutor.schemas.scene import AnimatorDraft, SceneParams, SceneSpec


def obj(i, **kw):
    return {"id": f"o{i}", "type": "label", "props": {"text": "hi"}, "init": {"x": 10, "y": 10}, **kw}


def spec(**kw):
    base = {"id": "s", "title": "t", "kind": "plot", "duration_ms": 1000, "objects": [obj(0)]}
    base.update(kw)
    return base


def test_valid_scene_round_trips_with_from_alias():
    s = SceneSpec.model_validate(
        spec(timeline=[{"t0_ms": 0, "t1_ms": 100, "target": "o0", "prop": "opacity", "from": 0, "to": 1}])
    )
    assert s.timeline[0].from_ == 0
    wire = s.to_wire()
    assert wire["timeline"][0]["from"] == 0 and "from_" not in wire["timeline"][0]
    assert SceneSpec.model_validate(wire).timeline[0].from_ == 0
    assert wire["canvas"] == {"w": 1000, "h": 600} and wire["v"] == 1


def test_bounds_are_enforced():
    with pytest.raises(ValidationError):
        SceneSpec.model_validate(spec(objects=[obj(i) for i in range(25)]))
    with pytest.raises(ValidationError):
        SceneSpec.model_validate(spec(duration_ms=20001))
    tw = {"t0_ms": 0, "t1_ms": 10, "target": "o0", "prop": "x", "to": 1}
    with pytest.raises(ValidationError):
        SceneSpec.model_validate(spec(timeline=[tw] * 41))
    cue = {"at_ms": 0, "caption": "c"}
    with pytest.raises(ValidationError):
        SceneSpec.model_validate(spec(cues=[cue] * 7))


def test_unknown_enum_and_extra_keys():
    with pytest.raises(ValidationError):
        SceneSpec.model_validate(spec(kind="hologram"))
    assert not hasattr(SceneSpec.model_validate(spec(surprise=1)), "surprise")


def test_long_text_is_truncated_not_rejected():
    s = SceneSpec.model_validate(spec(title="A sentence. " * 30, cues=[{"at_ms": 0, "caption": "word " * 100}]))
    assert len(s.title) <= 60 and len(s.cues[0].caption) <= 120


def test_draft_lists_are_capped_and_numbers_coerced():
    d = AnimatorDraft.model_validate(
        {
            "template": "array_walk",
            "title": "t",
            "params": {"values": list(range(30)), "nodes": list("abcdefghijkl"), "points": ["1", 2, 3, 4, 5]},
            "captions": ["a", "b", "c", "d", "e", "f"],
        }
    )
    assert len(d.params.values) == 12 and len(d.params.nodes) == 8
    assert d.params.points[0] == 1.0 and len(d.params.points) == 4
    assert len(d.captions) == 4


def test_draft_rejects_unknown_template_and_defaults_params_to_none():
    with pytest.raises(ValidationError):
        AnimatorDraft.model_validate({"template": "hologram", "title": "t", "params": {}})
    p = SceneParams()
    assert p.expr is None and p.values == [] and p.v0 is None


def test_draft_wire_schema_is_simple_and_ollama_safe():
    schema = inline_schema(AnimatorDraft, require_all=False)
    text = json.dumps(schema)
    assert "pattern" not in text and "$ref" not in text and "$defs" not in text
    assert schema["required"] == ["template", "title", "params"]
    assert {"expr", "values", "events", "bullets"} <= set(schema["properties"]["params"]["properties"])
