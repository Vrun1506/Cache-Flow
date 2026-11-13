"""Loads agent prompts from backend/prompts/<name>.md (final-plan.md §16.11).

The first line of every prompt file is ``<!-- prompt_version: N -->``. The
version is logged with each call so a result can be traced to its prompt.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"
_VERSION = re.compile(r"<!--\s*prompt_version:\s*(\S+)\s*-->")


class Prompt(NamedTuple):
    text: str
    version: str


@lru_cache(maxsize=None)
def load_prompt(name: str) -> Prompt:
    raw = (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")
    match = _VERSION.match(raw)
    if not match:
        raise ValueError(f"prompt {name!r} is missing its prompt_version header")
    return Prompt(text=raw[match.end():].strip(), version=match.group(1))
