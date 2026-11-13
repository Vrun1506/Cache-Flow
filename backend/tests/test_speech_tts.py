import asyncio

import httpx
import pytest

from tutor.speech.tts import (
    Clip,
    FakeTTS,
    HttpTTS,
    Narrator,
    PhraseCache,
    TTSError,
    concat_wavs,
    make_wav,
    wav_duration_ms,
)

pytestmark = pytest.mark.anyio


def test_make_wav_and_duration_round_trip():
    assert wav_duration_ms(make_wav(1500)) == 1500
    assert wav_duration_ms(make_wav(0)) == 0


def test_wav_duration_rejects_garbage():
    with pytest.raises(ValueError):
        wav_duration_ms(b"not a wav")


def test_concat_wavs_adds_durations():
    assert wav_duration_ms(concat_wavs([make_wav(1000), make_wav(500)])) == 1500
    one = make_wav(700)
    assert concat_wavs([one]) is one


def test_concat_rejects_mixed_formats():
    with pytest.raises(ValueError):
        concat_wavs([make_wav(100, 8000), make_wav(100, 16000)])


async def test_fake_tts_duration_follows_words_per_second():
    wav = await FakeTTS().synth("one two three four five")
    assert wav_duration_ms(wav) == round(5 / 2.6 * 1000)


def test_phrase_cache_memory_and_disk(tmp_path):
    cache = PhraseCache(tmp_path / "wavs")
    assert cache.get("abc") is None
    cache.put("abc", b"data")
    assert cache.get("abc") == b"data" and len(cache) == 1
    again = PhraseCache(tmp_path / "wavs")  # a new process finds the file
    assert again.get("abc") == b"data"


async def test_narrator_makes_two_clips_with_real_durations():
    tts = FakeTTS()
    clips = await Narrator(tts).narrate("First we find the pair.", "Two and three add to five.", prefix="s1")
    assert [c.id for c in clips] == ["s1-intent", "s1-explain"]
    assert all(isinstance(c, Clip) and not c.estimated and c.wav for c in clips)
    assert clips[0].duration_ms == wav_duration_ms(clips[0].wav)
    assert clips[0].caption == "First we find the pair."


async def test_narrator_speaks_maths_as_words_but_shows_the_original():
    tts = FakeTTS()
    (clip,) = await Narrator(tts).narrate("", "So x^2 = 4.")
    assert clip.caption == "So x^2 = 4." and clip.text == "So x squared equals 4."
    assert tts.calls == ["So x squared equals 4."]


async def test_narrator_skips_empty_text():
    clips = await Narrator(FakeTTS()).narrate("  ", "Only this.")
    assert [c.id for c in clips] == ["c-explain"]


async def test_narrator_chunks_long_text_into_one_clip():
    tts = FakeTTS()
    text = " ".join(f"Sentence number {i} is here." for i in range(30))
    (clip,) = await Narrator(tts, max_chunk_chars=100).narrate("", text)
    assert len(tts.calls) > 1 and not clip.estimated
    assert clip.duration_ms == wav_duration_ms(clip.wav)


async def test_narrator_uses_the_cache():
    tts, cache = FakeTTS(), PhraseCache()
    n = Narrator(tts, cache)
    await n.narrate("Hello there.", "")
    await n.narrate("Hello there.", "")
    assert len(tts.calls) == 1


async def test_tts_failure_gives_estimated_clips_without_audio():
    clips = await Narrator(FakeTTS(error=RuntimeError("down"))).narrate("Intent words here.", "And an explanation.")
    assert len(clips) == 2
    assert all(c.estimated and c.wav is None and c.duration_ms > 0 for c in clips)


async def test_no_tts_configured_is_the_same_as_failure():
    (clip,) = await Narrator(None).narrate("Some words to say aloud.", "")
    assert clip.estimated and clip.wav is None


async def test_slow_tts_times_out_into_an_estimate():
    class Slow:
        async def synth(self, text):
            await asyncio.sleep(5)

    (clip,) = await Narrator(Slow(), timeout_s=0.05).narrate("Hello there.", "")
    assert clip.estimated


async def test_partial_failure_only_degrades_that_clip():
    class Flaky:
        async def synth(self, text):
            if "boom" in text:
                raise TTSError("nope")
            return make_wav(500)

    a, b = await Narrator(Flaky()).narrate("fine", "boom")
    assert not a.estimated and b.estimated


async def test_cancellation_propagates():
    class Hang:
        async def synth(self, text):
            await asyncio.sleep(10)

    task = asyncio.create_task(Narrator(Hang()).narrate("hello", ""))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_clip_public_shape():
    clip = Clip("s1-intent", "hi", "Hi", 800, False)
    assert clip.public("/audio/x.wav") == {
        "id": "s1-intent",
        "url": "/audio/x.wav",
        "caption": "Hi",
        "duration_ms": 800,
        "estimated": False,
    }


async def test_http_tts_posts_an_openai_style_request():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["json"] = str(request.url), request.read()
        return httpx.Response(200, content=make_wav(1000))

    tts = HttpTTS("http://127.0.0.1:8002", "magpie", transport=httpx.MockTransport(handler))
    wav = await tts.synth("hello")
    assert seen["url"] == "http://127.0.0.1:8002/v1/audio/speech"
    assert b'"input":"hello"' in seen["json"].replace(b" ", b"") and b"wav" in seen["json"]
    assert wav_duration_ms(wav) == 1000
    await tts.aclose()


async def test_http_tts_errors_become_tts_error():
    tts = HttpTTS("http://x", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(TTSError):
        await tts.synth("hi")
    bad = HttpTTS("http://x", transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"junk")))
    with pytest.raises(TTSError):
        await bad.synth("hi")

    def boom(request):
        raise httpx.ConnectError("refused")

    down = HttpTTS("http://x", transport=httpx.MockTransport(boom))
    with pytest.raises(TTSError):
        await down.synth("hi")
