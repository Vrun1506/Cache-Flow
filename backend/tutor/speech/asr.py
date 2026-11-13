"""Speech-to-text for push-to-talk questions (Interface.md ``POST /audio``).

``HttpASR`` posts a WAV to an OpenAI-style ``/v1/audio/transcriptions`` endpoint, which is the shape
the Parakeet speech NIM is documented to expose. **NOT verified against the real server**: it is
unit-tested only with a mock transport. Confirm the real path, the form fields and whether a
confidence value is returned on the Spark (final-plan.md §16.15 item 8).

Nothing here is silently trusted: ``transcript_candidate`` marks a transcript ambiguous when it is
empty, low confidence, or contains a homophone that changes the maths ("root" vs "route"), so the iPad
shows a read-back the student confirms before the text is used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional, Protocol, Sequence, Union

import httpx

LOW_CONFIDENCE = 0.6
MAX_ALTERNATIVES = 3

# Words an ASR can confuse in spoken maths. Each maps to what it might really have been.
HOMOPHONES: dict[str, tuple[str, ...]] = {
    "root": ("route",),
    "route": ("root",),
    "two": ("to", "too"),
    "to": ("two",),
    "too": ("two",),
    "for": ("four",),
    "four": ("for",),
    "sine": ("sign",),
    "sign": ("sine",),
    "one": ("won",),
    "won": ("one",),
    "eight": ("ate",),
    "ate": ("eight",),
    "tan": ("ten",),
    "ten": ("tan",),
}
# The ones that make an utterance suspect on their own. "to" and "for" are ordinary English, so on
# their own they do not trigger a read-back (a number word or maths word next to them would).
_WEAK = {"to", "too", "for"}


class ASRError(Exception):
    """The speech service failed or returned something unusable."""


@dataclass
class Transcript:
    text: str
    confidence: Optional[float] = None


class ASRBackend(Protocol):
    async def transcribe(self, wav: bytes) -> Transcript: ...


class FakeASR:
    """Scripted ASR for tests: each item is a str, a Transcript or an Exception."""

    def __init__(self, script: Sequence[Union[str, Transcript, Exception]]) -> None:
        self.script = list(script)
        self.calls: list[bytes] = []

    async def transcribe(self, wav: bytes) -> Transcript:
        self.calls.append(wav)
        if not self.script:
            raise AssertionError("FakeASR has no scripted transcript left")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, Transcript) else Transcript(item)


class HttpASR:
    def __init__(
        self,
        base_url: str,
        *,
        model: str = "parakeet",
        language: str = "en",
        timeout: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._model, self._language = model, language
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout, transport=transport)

    async def transcribe(self, wav: bytes) -> Transcript:
        try:
            resp = await self._client.post(
                "/v1/audio/transcriptions",
                files={"file": ("utterance.wav", wav, "audio/wav")},
                data={"model": self._model, "language": self._language, "response_format": "json"},
            )
            resp.raise_for_status()
        except httpx.TimeoutException as exc:
            raise ASRError("speech recognition timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise ASRError(f"speech service returned {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise ASRError(f"speech service unreachable: {type(exc).__name__}") from exc
        try:
            body: Any = resp.json()
            text = body["text"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ASRError("speech service returned an unreadable response") from exc
        if not isinstance(text, str):
            raise ASRError("speech service returned a non-text transcript")
        conf = body.get("confidence") if isinstance(body, dict) else None
        confidence = float(conf) if isinstance(conf, (int, float)) and not isinstance(conf, bool) else None
        return Transcript(_tidy(text), confidence)

    async def aclose(self) -> None:
        await self._client.aclose()


def _tidy(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _alternatives(text: str) -> list[str]:
    words = text.split(" ")
    found: list[str] = []
    for i, word in enumerate(words):
        core = re.sub(r"[^\w]", "", word).lower()
        for alt in HOMOPHONES.get(core, ()):
            swapped = [*words[:i], re.sub(re.escape(core), alt, word, flags=re.I), *words[i + 1 :]]
            candidate = " ".join(swapped)
            if candidate != text and candidate not in found:
                found.append(candidate)
    return found[:MAX_ALTERNATIVES]


def _has_suspect_word(text: str) -> bool:
    words = {re.sub(r"[^\w]", "", w).lower() for w in text.split()}
    hits = words & HOMOPHONES.keys()
    strong = hits - _WEAK
    return bool(strong) or (bool(hits & _WEAK) and len(words) <= 3)


async def transcript_candidate(asr: ASRBackend, wav: bytes) -> dict[str, Any]:
    """Transcribe and say how far to trust it. Raises ASRError if the service fails."""
    t = await asr.transcribe(wav)
    text = _tidy(t.text or "")
    reasons: list[str] = []
    if not text:
        reasons.append("I didn't catch anything.")
    if t.confidence is not None and t.confidence < LOW_CONFIDENCE:
        reasons.append("I'm not sure I heard that right.")
    if text and _has_suspect_word(text):
        reasons.append("Some words sound alike.")
    return {
        "text": text,
        "ambiguous": bool(reasons),
        "alternatives": _alternatives(text) if text else [],
        "confidence": t.confidence,
        "note": reasons[0] if reasons else None,
    }
