"""Length handling for model output (final-plan.md §16.2).

Soft text fields over their limit are truncated at the last sentence boundary
without a retry. Structural problems (wrong enum, missing field) are not
handled here; they fail validation and trigger a retry.
"""

from __future__ import annotations

import re
from typing import Annotated, Any

from pydantic import BeforeValidator, StringConstraints

_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")
_MIN_KEEP = 0.4  # never cut back further than 40% of the limit


def trim_sentence(text: str, limit: int) -> str:
    """Trim ``text`` to at most ``limit`` characters, preferring a sentence end."""
    text = text.strip()
    if len(text) <= limit:
        return text
    head = text[:limit]
    ends = [m.end() for m in _SENTENCE_END.finditer(head)]
    if ends and ends[-1] >= limit * _MIN_KEEP:
        return head[: ends[-1]].strip()
    head = text[: limit - 1]
    space = head.rfind(" ")
    if space >= limit * _MIN_KEEP:
        head = head[:space]
    return head.rstrip(" ,;:") + "…"


def Soft(limit: int):
    """A string that is truncated (not rejected) when it is too long."""
    return Annotated[str, BeforeValidator(lambda v: trim_sentence(v, limit) if isinstance(v, str) else v)]


def Hard(limit: int):
    """A string that is rejected when it is too long (expressions, code)."""
    return Annotated[str, StringConstraints(max_length=limit)]


def cap_list(n: int):
    """A BeforeValidator that keeps only the first ``n`` list items."""

    def _cap(value: Any) -> Any:
        return value[:n] if isinstance(value, list) else value

    return BeforeValidator(_cap)
