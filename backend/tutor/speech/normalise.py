"""Maths-to-speech (final-plan.md §3, the Narrator).

Turns board-style text into something a TTS voice reads aloud correctly:
``x^2 = 4`` becomes ``x squared equals 4``. Pure functions, no model.
"""

from __future__ import annotations

import hashlib
import re

_HYPHEN_WORD = "\u0001"  # protects hyphens inside ordinary words (well-known)

_UNITS = [  # longest first, applied before powers and fractions
    (r"m/s\^2|m/s²", "metres per second squared"),
    (r"km/h", "kilometres per hour"),
    (r"m/s", "metres per second"),
    (r"kg/m\^3|kg/m³", "kilograms per cubic metre"),
    (r"g/mol", "grams per mole"),
    (r"mol/L", "moles per litre"),
    (r"J/s", "joules per second"),
    (r"N/m", "newtons per metre"),
]

_SUPERSCRIPT = {"²": " squared", "³": " cubed"}

_POW = re.compile(r"(?P<base>\([^()]*\)|[A-Za-z0-9.]+)\s*(?:\^|\*\*)\s*\(?(?P<exp>-?\d+|[A-Za-z])\)?")


def _power_words(exp: str) -> str:
    if exp == "2":
        return "squared"
    if exp == "3":
        return "cubed"
    if exp.startswith("-") and exp[1:].isdigit():
        return f"to the power minus {exp[1:]}"
    return f"to the power {exp}"


def _power(m: re.Match) -> str:
    base = m.group("base")
    if base.startswith("("):
        base = f"the quantity {base[1:-1]}"
    return f"{base} {_power_words(m.group('exp'))}"


def to_speech(text: str) -> str:
    """Rewrite maths notation in ``text`` as spoken English."""
    s = text.replace("−", "-").replace("–", "-").replace("÷", " divided by ")
    for ch, word in _SUPERSCRIPT.items():
        s = s.replace(ch, word)

    for pattern, words in _UNITS:
        s = re.sub(rf"(?<![A-Za-z]){pattern}(?![A-Za-z])", words, s)

    for _ in range(3):  # sqrt(...) may nest
        s = re.sub(r"sqrt\(([^()]*)\)", r"the square root of \1", s)
    s = re.sub(r"√\s*\(([^()]*)\)", r"the square root of \1", s)
    s = re.sub(r"√\s*([A-Za-z0-9.]+)", r"the square root of \1", s)

    s = _POW.sub(_power, s)

    s = s.replace("<=", " is less than or equal to ").replace("≤", " is less than or equal to ")
    s = s.replace(">=", " is greater than or equal to ").replace("≥", " is greater than or equal to ")
    s = s.replace("!=", " is not equal to ").replace("≠", " is not equal to ")
    s = s.replace("->", " gives ").replace("→", " gives ").replace("≈", " is approximately ")
    s = re.sub(r"(?<![<>])<(?![<>=])", " is less than ", s)
    s = re.sub(r"(?<![<>=-])>(?![<>=])", " is greater than ", s)
    s = s.replace("=", " equals ")

    s = re.sub(r"(?<=\w)\s*/\s*(?=\w)", " over ", s)
    s = re.sub(r"\s*[*×·]\s*", " times ", s)
    s = re.sub(r"\s*\+\s*", " plus ", s)
    s = s.replace("%", " percent").replace("°", " degrees").replace("π", "pi")

    s = re.sub(r"(?<=[A-Za-z]{2})-(?=[A-Za-z]{2})", _HYPHEN_WORD, s)  # keep hyphenated words
    s = re.sub(r"\s*-\s*(?=[\w.(])", " minus ", s)
    s = s.replace(_HYPHEN_WORD, "-")
    return re.sub(r"\s+", " ", s).strip()


def split_sentences(text: str, max_chars: int = 200) -> list[str]:
    """Split into TTS-sized chunks: whole sentences where possible, never over ``max_chars``."""
    text = " ".join(text.split())
    if not text:
        return []
    pieces: list[str] = []
    for sentence in re.split(r"(?<=[.!?;])\s+", text):
        while len(sentence) > max_chars:
            cut = max(sentence.rfind(", ", 0, max_chars), sentence.rfind(" ", 0, max_chars))
            if cut < max_chars * 0.3:
                cut = max_chars
            pieces.append(sentence[:cut].strip(" ,"))
            sentence = sentence[cut:].strip()
        if sentence:
            pieces.append(sentence)
    chunks: list[str] = []
    for piece in pieces:  # merge short neighbours so we make fewer TTS calls
        if chunks and len(chunks[-1]) + 1 + len(piece) <= max_chars:
            chunks[-1] += " " + piece
        else:
            chunks.append(piece)
    return chunks


def phrase_key(text: str, voice: str = "default") -> str:
    """Stable cache key for a spoken phrase."""
    norm = " ".join(text.split()).lower()
    return hashlib.sha256(f"{voice}\x00{norm}".encode()).hexdigest()[:24]
