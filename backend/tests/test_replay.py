import json
import random

import pytest

from tutor.llm import FakeLLM, LLMRequest, LLMResponse, LLMTimeout
from tutor.llm.replay import RecordingBackend, ReplayBackend, ReplayedResponse, ReplayMiss, request_key

pytestmark = pytest.mark.anyio


def req(**kw):
    base = dict(agent="solver", tier="reasoner", system="sys", user="Solve it", prompt_version="2",
                schema={"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "integer"}}})
    base.update(kw)
    return LLMRequest(**base)


def resp(text='{"a":"é — ü","b":1}', **kw):
    base = dict(text=text, prompt_tokens=11, completion_tokens=7, done_reason="stop", model="m", total_ms=12.5)
    base.update(kw)
    return LLMResponse(**base)


# -------------------------------------------------------------------- key


def test_key_is_stable_across_dict_ordering():
    a = {"type": "object", "properties": {"a": 1, "b": 2}}
    b = {"properties": {"b": 2, "a": 1}, "type": "object"}
    assert request_key(req(schema=a)) == request_key(req(schema=b))


@pytest.mark.parametrize(
    "change",
    [dict(agent="x"), dict(prompt_version="3"), dict(system="s2"), dict(user="u2"), dict(temperature=0.5),
     dict(think=True), dict(seed=7), dict(schema=None), dict(images=(b"png",))],
)
def test_key_changes_with_every_input_that_changes_the_answer(change):
    assert request_key(req()) != request_key(req(**change))


def test_key_ignores_tier_and_num_predict_and_hashes_images():
    assert request_key(req()) == request_key(req(tier="router", num_predict=99))
    assert request_key(req(images=(b"a",))) != request_key(req(images=(b"b",)))


def test_key_is_a_sha256_hex():
    assert len(request_key(req())) == 64


# ------------------------------------------------------------- roundtrip


async def test_record_then_replay_is_byte_identical(tmp_path):
    store = tmp_path / "rec" / "demo.jsonl"
    original = resp()
    live = RecordingBackend(FakeLLM({"solver": [original]}), store)
    got = await live.generate(req())
    assert got is original and live.recorded == 1

    replay = ReplayBackend(store)
    out = await replay.generate(req())
    assert isinstance(out, ReplayedResponse) and out.replayed is True
    assert (out.text, out.prompt_tokens, out.completion_tokens, out.done_reason, out.model, out.total_ms) == (
        original.text, original.prompt_tokens, original.completion_tokens, original.done_reason, original.model,
        original.total_ms)
    assert out.text.encode() == original.text.encode()
    assert replay.stats()["hits"] == 1 and replay.is_replay and replay.label == "replayed"


async def test_recording_the_same_request_twice_writes_once(tmp_path):
    store = tmp_path / "s.jsonl"
    rec = RecordingBackend(FakeLLM({"solver": [resp(), resp()]}), store)
    await rec.generate(req())
    await rec.generate(req())
    assert len(store.read_text().splitlines()) == 1 and rec.recorded == 1


async def test_recording_resumes_an_existing_store(tmp_path):
    store = tmp_path / "s.jsonl"
    await RecordingBackend(FakeLLM({"solver": [resp()]}), store).generate(req())
    second = RecordingBackend(FakeLLM({"solver": [resp()]}), store)
    await second.generate(req())
    assert second.recorded == 0 and len(store.read_text().splitlines()) == 1


async def test_errors_pass_through_and_are_not_recorded(tmp_path):
    store = tmp_path / "s.jsonl"
    err = LLMTimeout("slow")
    rec = RecordingBackend(FakeLLM({"solver": [err]}), store)
    with pytest.raises(LLMTimeout) as info:
        await rec.generate(req())
    assert info.value is err and (not store.exists() or store.read_text() == "")


async def test_images_are_hashed_never_stored(tmp_path):
    store = tmp_path / "s.jsonl"
    secret = b"\x89PNG-student-handwriting"
    rec = RecordingBackend(FakeLLM({"vision": [resp()]}), store)
    await rec.generate(req(agent="vision", images=(secret,)))
    raw = store.read_text()
    assert "student-handwriting" not in raw and "images" not in json.loads(raw)
    out = await ReplayBackend(store).generate(req(agent="vision", images=(secret,)))
    assert out.replayed


async def test_recorder_forwards_other_attributes(tmp_path):
    class Inner:
        async def generate(self, r):
            return resp()

        def stats(self):
            return {"swaps": 1}

    assert RecordingBackend(Inner(), tmp_path / "s.jsonl").stats() == {"swaps": 1}


# ------------------------------------------------------------------- misses


async def test_miss_raises_by_default(tmp_path):
    replay = ReplayBackend(tmp_path / "none.jsonl")
    with pytest.raises(ReplayMiss, match="solver"):
        await replay.generate(req())
    assert replay.stats()["misses"] == 1 and replay.stats()["entries"] == 0


async def test_replay_miss_is_an_llm_error_so_agents_degrade_cleanly(tmp_path):
    from tutor.llm import LLMError

    assert issubclass(ReplayMiss, LLMError)


async def test_miss_can_fall_through_and_is_labelled_honestly(tmp_path):
    store = tmp_path / "s.jsonl"
    await RecordingBackend(FakeLLM({"solver": [resp()]}), store).generate(req())
    live = FakeLLM({"solver": [resp('{"live":true}')]})
    replay = ReplayBackend(store, miss="fallback", fallback=live)
    hit = await replay.generate(req())
    fresh = await replay.generate(req(user="a different question"))
    assert hit.replayed is True and not getattr(fresh, "replayed", False) and fresh.text == '{"live":true}'
    assert replay.stats() == {**replay.stats(), "hits": 1, "misses": 1, "label": "partially replayed"}


def test_bad_construction_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        ReplayBackend(tmp_path / "s.jsonl", miss="fallback")
    with pytest.raises(ValueError):
        ReplayBackend(tmp_path / "s.jsonl", miss="sometimes")


# ------------------------------------------------------------ corrupt stores


def test_corrupt_and_malformed_lines_are_skipped(tmp_path):
    store = tmp_path / "s.jsonl"
    good = {"key": "k1", "agent": "solver", "prompt_version": "1",
            "response": {"text": "ok", "prompt_tokens": 1, "completion_tokens": 2, "done_reason": "stop", "model": "m", "total_ms": 1.0}}
    store.write_text("\n".join([
        "not json at all",
        json.dumps(good),
        '{"key": "k2"}',                                   # no response
        '{"key": 5, "response": {}}',                      # wrong key type
        json.dumps({**good, "key": "k3", "response": {**good["response"], "prompt_tokens": "many"}}),
        "",
        '{"truncated": ',
    ]) + "\n")
    replay = ReplayBackend(store)
    assert replay.stats()["entries"] == 1 and replay.stats()["skipped_lines"] == 5


async def test_last_entry_for_a_key_wins(tmp_path):
    store = tmp_path / "s.jsonl"
    key = request_key(req())
    base = {"key": key, "agent": "solver", "prompt_version": "2"}
    rows = [{**base, "response": {"text": t, "prompt_tokens": 1, "completion_tokens": 1, "done_reason": "stop", "model": "m", "total_ms": 0}}
            for t in ("old", "new")]
    store.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert (await ReplayBackend(store).generate(req())).text == "new"


async def test_replay_is_deterministic_regardless_of_call_order(tmp_path):
    store = tmp_path / "s.jsonl"
    users = [f"question {i}" for i in range(20)]
    rec = RecordingBackend(FakeLLM({"solver": [resp(f'{{"i":{i}}}') for i in range(20)]}), store)
    for u in users:
        await rec.generate(req(user=u))
    replay = ReplayBackend(store)
    shuffled = users[:]
    random.Random(1).shuffle(shuffled)
    for u in shuffled:
        assert (await replay.generate(req(user=u))).text == f'{{"i":{users.index(u)}}}'


async def test_replayed_response_works_with_structured_call(tmp_path):
    """The agents must not care that an answer came from the store."""
    from pydantic import BaseModel

    from tutor.llm import structured_call

    class Out(BaseModel):
        a: str
        b: int

    store = tmp_path / "s.jsonl"
    await RecordingBackend(FakeLLM({"solver": [resp()]}), store).generate(req())
    parsed = await structured_call(ReplayBackend(store), req(), Out)
    assert parsed.value.b == 1 and parsed.response.replayed is True
