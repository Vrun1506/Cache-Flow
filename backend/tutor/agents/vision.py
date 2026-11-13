"""Vision reader: transcribe handwriting from images (final-plan.md §3 agent 7, §16.11).

Qwen is the only installed model that reads images, so every call uses tier ``"vision"``.
The reader transcribes only. Its output is a candidate that the student confirms (read-back),
so a misread can never silently become the problem. These functions never raise except on
cancellation: a failed read is an ambiguous, empty ``Reading`` the caller can show as "couldn't read".
"""

from __future__ import annotations

import re
from typing import Sequence

from ..llm import LLMBackend, LLMError, LLMRequest, structured_call
from ..llm.budget import DEFAULT_NUM_CTX, DEFAULT_MARGIN, IMAGE_TOKENS, estimate_tokens
from ..media.png import MediaError, downscale
from ..prompts import load_prompt
from ..schemas import inline_schema
from ..schemas.text import trim_sentence
from ..schemas.vision import LinesReading, Reading

UNREADABLE_NOTE = "I couldn't read that"
UNCLEAR_NOTE = "Part of this was unclear."
MAX_ALTERNATIVES = 3
QUESTION_NUM_PREDICT = 500
PER_LINE_PREDICT = 100


def max_images_per_call(system_chars: int, num_predict_per_line: int = PER_LINE_PREDICT, base_predict: int = 100) -> int:
    """How many line images fit one request: each image costs IMAGE_TOKENS of the 8,192 window."""
    n = 1
    while n < 8:
        nxt = n + 1
        total = estimate_tokens("x" * system_chars) + 40 + IMAGE_TOKENS * nxt + base_predict + nxt * num_predict_per_line + DEFAULT_MARGIN
        if total > DEFAULT_NUM_CTX:
            break
        n = nxt
    return n


# ---------------------------------------------------------------- post-processing

_UNICODE = str.maketrans(
    {"−": "-", "–": "-", "—": "-", "‐": "-", "×": "*", "·": "*", "⋅": "*", "÷": "/", "²": "^2", "³": "^3",
     "“": '"', "”": '"', "‘": "'", "’": "'", " ": " "}
)
_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*\n?|\n?\s*```\s*$")
_UNREADABLE = re.compile(r"\[\s*\?\s*\]")
# (pattern, replacement) pairs that only ever produce ALTERNATIVES. The text itself is never changed.
_CONFUSABLES = [
    # A confusable letter counts only when it sits against digits and is not part of a word (2ln(x), 2or).
    (re.compile(r"(?<=\d)S(?![A-Za-z])|(?<![A-Za-z])S(?=\d)"), "5"),
    (re.compile(r"(?<=\d)[lI](?![A-Za-z])|(?<![A-Za-z])[lI](?=\d)"), "1"),
    (re.compile(r"(?<=\d)[Oo](?![A-Za-z])|(?<![A-Za-z])[Oo](?=\d)"), "0"),
    (re.compile(r"(?<=\d)\s*x\s*(?=\d)"), " * "),
]


def _tidy(text: str) -> str:
    text = _FENCE.sub("", text.translate(_UNICODE)).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'`":
        text = text[1:-1]
    return re.sub(r"\s+", " ", text).strip()


def clean_reading(text: str) -> Reading:
    """Normalise a transcription and flag what is uncertain, without changing what was read."""
    cleaned = trim_sentence(_tidy(text or ""), 300)
    alternatives: list[str] = []
    ambiguous = False
    note = None
    if _UNREADABLE.search(cleaned):
        cleaned = _UNREADABLE.sub("[?]", cleaned)
        ambiguous, note = True, UNCLEAR_NOTE
    for pattern, replacement in _CONFUSABLES:
        if pattern.search(cleaned):
            alt = re.sub(r"\s+", " ", pattern.sub(replacement, cleaned)).strip()
            if alt != cleaned and alt not in alternatives:
                alternatives.append(alt)
    if alternatives:
        ambiguous, note = True, note or UNCLEAR_NOTE
    if not cleaned:
        ambiguous, note = True, UNREADABLE_NOTE
    return Reading(text=cleaned, alternatives=alternatives[:MAX_ALTERNATIVES], ambiguous=ambiguous, note=note)


def finalise(reading: Reading) -> Reading:
    """Merge the model's own flags with the deterministic ones. Ambiguity only ever goes up."""
    mine = clean_reading(reading.text)
    alts: list[str] = []
    for candidate in [*reading.alternatives, *mine.alternatives]:
        c = _tidy(candidate)
        if c and c != mine.text and c not in alts:
            alts.append(c)
    ambiguous = reading.ambiguous or mine.ambiguous or bool(alts)
    note = reading.note or mine.note or (UNCLEAR_NOTE if ambiguous else None)
    return Reading(text=mine.text, alternatives=alts[:MAX_ALTERNATIVES], ambiguous=ambiguous, note=note)


def unreadable() -> Reading:
    return Reading(text="", ambiguous=True, note=UNREADABLE_NOTE)


# ------------------------------------------------------------------------ agent


async def read_question(backend: LLMBackend, png: bytes) -> Reading:
    """Read one image of a handwritten question."""
    prompt = load_prompt("vision_reader")
    try:
        image = downscale(png)
    except MediaError:
        return unreadable()
    req = LLMRequest(
        agent="vision",
        tier="vision",
        system=prompt.text,
        user="<task>Transcribe the handwritten question in the image.</task>",
        schema=inline_schema(Reading),
        images=(image,),
        temperature=0.0,
        think=False,
        num_predict=QUESTION_NUM_PREDICT,
        prompt_version=prompt.version,
    )
    try:
        return finalise((await structured_call(backend, req, Reading)).value)
    except LLMError:
        return unreadable()


async def read_lines(backend: LLMBackend, pngs: Sequence[bytes]) -> list[Reading]:
    """Read many line images. Each batch is ONE multi-image request; results keep the input order."""
    prompt = load_prompt("vision_reader")
    per_call = max_images_per_call(len(prompt.text))
    out: list[Reading] = []
    for start in range(0, len(pngs), per_call):
        out.extend(await _read_batch(backend, prompt, list(pngs[start : start + per_call])))
    return out


async def _read_batch(backend: LLMBackend, prompt, pngs: list[bytes]) -> list[Reading]:
    images: list[bytes] = []
    slots: list[int | None] = []  # image position -> index into `images`, or None if it was unusable
    for png in pngs:
        try:
            images.append(downscale(png))
            slots.append(len(images) - 1)
        except MediaError:
            slots.append(None)
    if not images:
        return [unreadable() for _ in pngs]
    req = LLMRequest(
        agent="vision_lines",
        tier="vision",
        system=prompt.text,
        user=f"<task>Transcribe these {len(images)} handwritten lines. Return {len(images)} readings, one per image, in order.</task>",
        schema=inline_schema(LinesReading),
        images=tuple(images),
        temperature=0.0,
        think=False,
        num_predict=100 + PER_LINE_PREDICT * len(images),
        prompt_version=prompt.version,
    )
    try:
        batch = (await structured_call(backend, req, LinesReading)).value.readings
    except LLMError:
        return [unreadable() for _ in pngs]
    # The count must match the images sent: pad or truncate rather than trust it.
    readings = [finalise(r) for r in batch[: len(images)]]
    readings += [unreadable() for _ in range(len(images) - len(readings))]
    return [readings[i] if i is not None else unreadable() for i in slots]
