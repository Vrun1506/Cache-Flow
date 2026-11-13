"""Scripted model for tests (final-plan.md §16.14). Replays queued outputs per agent."""

from __future__ import annotations

import asyncio
from typing import Union

from .budget import estimate_tokens
from .types import LLMRequest, LLMResponse

Item = Union[str, LLMResponse, Exception]


class FakeLLM:
    def __init__(self, script: dict[str, list[Item]], delay: float = 0.0) -> None:
        self.script = {agent: list(items) for agent, items in script.items()}
        self.delay = delay
        self.calls: list[LLMRequest] = []

    async def generate(self, req: LLMRequest) -> LLMResponse:
        self.calls.append(req)
        if self.delay:
            await asyncio.sleep(self.delay)
        queue = self.script.get(req.agent)
        if not queue:
            raise AssertionError(f"FakeLLM has no scripted response left for agent {req.agent!r}")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, LLMResponse):
            return item
        return LLMResponse(
            text=item,
            prompt_tokens=estimate_tokens(req.system + req.user),
            completion_tokens=estimate_tokens(item),
            done_reason="stop",
            model="fake",
        )
