import json

import httpx
import pytest

from tutor.speech.asr import ASRError, FakeASR, HttpASR, Transcript, transcript_candidate

pytestmark = pytest.mark.anyio


def http(handler, **kw) -> HttpASR:
    return HttpASR("http://asr.local", transport=httpx.MockTransport(handler), **kw)


# ------------------------------------------------------------------- HttpASR


async def test_posts_multipart_wav_to_the_transcription_endpoint():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["ctype"] = request.headers["content-type"]
        seen["body"] = request.content
        return httpx.Response(200, json={"text": "  what is   the root ", "confidence": 0.93})

    out = await http(handler).transcribe(b"RIFFfakewav")
    assert out == Transcript("what is the root", 0.93)
    assert seen["path"] == "/v1/audio/transcriptions"
    assert seen["ctype"].startswith("multipart/form-data")
    assert b"RIFFfakewav" in seen["body"] and b'name="model"' in seen["body"]


async def test_confidence_is_optional():
    out = await http(lambda r: httpx.Response(200, json={"text": "hello"})).transcribe(b"x")
    assert out.confidence is None


async def test_timeout_becomes_asr_error():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ASRError, match="timed out"):
        await http(handler).transcribe(b"x")


async def test_server_error_becomes_asr_error():
    with pytest.raises(ASRError, match="500"):
        await http(lambda r: httpx.Response(500, text="boom")).transcribe(b"x")


async def test_unreachable_service_becomes_asr_error():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ASRError, match="unreachable"):
        await http(handler).transcribe(b"x")


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"nope": 1}),
        httpx.Response(200, json=["a"]),
        httpx.Response(200, json={"text": 5}),
        httpx.Response(200, json={"text": None}),
    ],
)
async def test_malformed_responses_become_asr_error(response):
    with pytest.raises(ASRError):
        await http(lambda r: response).transcribe(b"x")


async def test_non_numeric_confidence_is_ignored():
    out = await http(lambda r: httpx.Response(200, json={"text": "hi", "confidence": "high"})).transcribe(b"x")
    assert out.confidence is None


# ------------------------------------------------------------------ FakeASR


async def test_fake_asr_replays_and_records():
    fake = FakeASR(["one", Transcript("two", 0.5), ASRError("down")])
    assert (await fake.transcribe(b"a")).text == "one"
    assert (await fake.transcribe(b"b")).confidence == 0.5
    with pytest.raises(ASRError):
        await fake.transcribe(b"c")
    assert fake.calls == [b"a", b"b", b"c"]


# -------------------------------------------------------- transcript_candidate


async def test_clear_transcript_is_not_ambiguous():
    out = await transcript_candidate(FakeASR([Transcript("explain the second step again", 0.95)]), b"x")
    assert out == {"text": "explain the second step again", "ambiguous": False, "alternatives": [],
                   "confidence": 0.95, "note": None}


async def test_empty_transcript_is_ambiguous():
    out = await transcript_candidate(FakeASR(["   "]), b"x")
    assert out["ambiguous"] and out["text"] == "" and out["alternatives"] == []


async def test_low_confidence_is_ambiguous():
    out = await transcript_candidate(FakeASR([Transcript("solve it", 0.4)]), b"x")
    assert out["ambiguous"] and "not sure" in out["note"]


async def test_confidence_at_the_threshold_is_trusted():
    assert not (await transcript_candidate(FakeASR([Transcript("solve it", 0.6)]), b"x"))["ambiguous"]


@pytest.mark.parametrize(
    "heard,alt",
    [
        ("what is the route of this equation", "what is the root of this equation"),
        ("what is the root", "what is the route"),
        ("the sine of x", "the sign of x"),
        ("add one to both sides", "add won to both sides"),
    ],
)
async def test_homophones_that_change_the_maths_are_ambiguous_with_alternatives(heard, alt):
    out = await transcript_candidate(FakeASR([Transcript(heard, 0.99)]), b"x")
    assert out["ambiguous"] and alt in out["alternatives"] and out["text"] == heard


async def test_ordinary_to_and_for_in_a_sentence_do_not_force_a_readback():
    out = await transcript_candidate(FakeASR([Transcript("why do we subtract three from both sides to isolate x", 0.97)]), b"x")
    assert out["ambiguous"] is False


async def test_a_very_short_utterance_with_a_weak_homophone_is_ambiguous():
    assert (await transcript_candidate(FakeASR(["to"]), b"x"))["ambiguous"]


async def test_alternatives_are_capped_and_unique():
    out = await transcript_candidate(FakeASR([Transcript("root two root four sine sign ten", 0.99)]), b"x")
    assert len(out["alternatives"]) <= 3 and len(set(out["alternatives"])) == len(out["alternatives"])


async def test_asr_failure_propagates_as_asr_error():
    with pytest.raises(ASRError):
        await transcript_candidate(FakeASR([ASRError("down")]), b"x")


async def test_candidate_is_json_serialisable():
    out = await transcript_candidate(FakeASR([Transcript("the route", 0.9)]), b"x")
    json.dumps(out)
