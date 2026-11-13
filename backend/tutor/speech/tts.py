"""Text-to-speech clients and the Narrator (final-plan.md §3, §16.6).

The Narrator turns a step's ``intent`` and ``explain`` into two clips with real
durations (read from the WAV header) so cues can be resolved on the server. If
TTS is down it still returns clips, with estimated durations and no audio, and
the iPad speaks them with its on-device voice.
"""

from __future__ import annotations

import asyncio
import io
import struct
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx

from ..board.cues import WORDS_PER_SECOND, estimate_ms
from .normalise import phrase_key, split_sentences, to_speech


class TTSError(Exception):
    """The TTS service failed or returned something that is not a WAV."""


class TTSBackend(Protocol):
    async def synth(self, text: str) -> bytes:
        """Return a WAV file speaking ``text``."""
        ...


def make_wav(duration_ms: int, sample_rate: int = 8000) -> bytes:
    """A silent mono 16-bit WAV of the given length."""
    frames = max(0, round(sample_rate * duration_ms / 1000))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(struct.pack("<h", 0) * frames)
    return buf.getvalue()


def wav_duration_ms(data: bytes) -> int:
    """Duration of a WAV in milliseconds. Raises ValueError if it is not a readable WAV."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            rate = w.getframerate()
            if rate <= 0:
                raise ValueError("WAV has no sample rate")
            return round(w.getnframes() / rate * 1000)
    except (wave.Error, EOFError) as exc:
        raise ValueError(f"not a valid WAV: {exc}") from exc


def concat_wavs(parts: list[bytes]) -> bytes:
    """Join WAVs that share the same format into one."""
    if len(parts) == 1:
        return parts[0]
    frames, params = [], None
    for part in parts:
        with wave.open(io.BytesIO(part), "rb") as w:
            p = (w.getnchannels(), w.getsampwidth(), w.getframerate())
            if params is None:
                params = p
            elif p != params:
                raise ValueError("cannot join WAVs with different formats")
            frames.append(w.readframes(w.getnframes()))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(params[0])
        out.setsampwidth(params[1])
        out.setframerate(params[2])
        out.writeframes(b"".join(frames))
    return buf.getvalue()


class FakeTTS:
    """Test double: silent audio whose length follows 2.6 words per second."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[str] = []

    async def synth(self, text: str) -> bytes:
        self.calls.append(text)
        if self.error is not None:
            raise self.error
        return make_wav(round(len(text.split()) / WORDS_PER_SECOND * 1000))


class PhraseCache:
    """Remembers synthesised phrases, in memory and optionally on disk."""

    def __init__(self, directory: str | Path | None = None) -> None:
        self._mem: dict[str, bytes] = {}
        self._dir = Path(directory) if directory else None
        if self._dir:
            self._dir.mkdir(parents=True, exist_ok=True)

    def get(self, key: str) -> bytes | None:
        if key in self._mem:
            return self._mem[key]
        if self._dir:
            path = self._dir / f"{key}.wav"
            if path.is_file():
                self._mem[key] = path.read_bytes()
                return self._mem[key]
        return None

    def put(self, key: str, wav: bytes) -> None:
        self._mem[key] = wav
        if self._dir:
            (self._dir / f"{key}.wav").write_bytes(wav)

    def __len__(self) -> int:
        return len(self._mem)


class HttpTTS:
    """Client for a local OpenAI-style speech endpoint (Magpie or Kokoro behind ``/v1/audio/speech``).

    NOT verified against a real server: the request shape is a best guess from
    the common API and has only been unit-tested with a mock transport. Confirm
    the real endpoint on the Spark (final-plan.md §16.15 item 8).
    """

    def __init__(
        self,
        base_url: str,
        voice: str = "default",
        *,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout, transport=transport)
        self.voice = voice

    async def synth(self, text: str) -> bytes:
        try:
            resp = await self._client.post(
                "/v1/audio/speech", json={"input": text, "voice": self.voice, "response_format": "wav"}
            )
        except httpx.HTTPError as exc:
            raise TTSError(f"TTS request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise TTSError(f"TTS returned HTTP {resp.status_code}")
        try:
            wav_duration_ms(resp.content)
        except ValueError as exc:
            raise TTSError(str(exc)) from exc
        return resp.content

    async def aclose(self) -> None:
        await self._client.aclose()


@dataclass
class Clip:
    id: str
    text: str  # what is spoken (maths already rewritten as words)
    caption: str  # what is shown
    duration_ms: int
    estimated: bool
    wav: bytes | None = field(default=None, repr=False)

    def public(self, url: str | None = None) -> dict:
        """The shape of an entry in the ``step`` event's ``clips`` list."""
        return {
            "id": self.id,
            "url": url,
            "caption": self.caption,
            "duration_ms": self.duration_ms,
            "estimated": self.estimated,
        }


class Narrator:
    def __init__(
        self,
        tts: TTSBackend | None,
        cache: PhraseCache | None = None,
        *,
        voice: str = "default",
        timeout_s: float = 20.0,
        max_chunk_chars: int = 200,
    ) -> None:
        self._tts = tts
        self._cache = cache
        self._voice = voice
        self._timeout = timeout_s
        self._max = max_chunk_chars

    async def narrate(self, intent: str, explain: str, *, prefix: str = "c") -> list[Clip]:
        """Two clips (intent, explain) in order. Empty texts are skipped. Never raises on TTS failure."""
        jobs = [(f"{prefix}-intent", intent), (f"{prefix}-explain", explain)]
        jobs = [(cid, text) for cid, text in jobs if text and text.strip()]
        return list(await asyncio.gather(*(self._clip(cid, text) for cid, text in jobs)))

    async def _clip(self, clip_id: str, caption: str) -> Clip:
        spoken = to_speech(caption)
        try:
            if self._tts is None:
                raise TTSError("no TTS configured")
            parts = [await self._chunk(chunk) for chunk in split_sentences(spoken, self._max)]
            wav = concat_wavs(parts)
            return Clip(clip_id, spoken, caption, wav_duration_ms(wav), False, wav)
        except Exception:  # any TTS failure degrades to on-device speech; cancellation still propagates
            return Clip(clip_id, spoken, caption, estimate_ms(spoken), True, None)

    async def _chunk(self, chunk: str) -> bytes:
        key = phrase_key(chunk, self._voice)
        if self._cache is not None and (hit := self._cache.get(key)) is not None:
            return hit
        async with asyncio.timeout(self._timeout):
            wav = await self._tts.synth(chunk)
        if self._cache is not None:
            self._cache.put(key, wav)
        return wav
