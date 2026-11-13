"""Shared LLM request/response types and exceptions (final-plan.md §16.4, §16.12)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional, Protocol

Tier = Literal["router", "reasoner", "vision"]


@dataclass(frozen=True)
class LLMRequest:
    agent: str
    tier: Tier
    system: str
    user: str
    schema: Optional[dict[str, Any]] = None
    images: tuple[bytes, ...] = ()
    temperature: float = 0.0
    think: bool = False
    num_predict: int = 600
    seed: int = 42
    prompt_version: str = "1"


@dataclass
class LLMResponse:
    text: str
    prompt_tokens: int
    completion_tokens: int
    done_reason: str
    model: str
    total_ms: float = 0.0


class LLMBackend(Protocol):
    async def generate(self, req: LLMRequest) -> LLMResponse: ...


class LLMError(Exception):
    """Any failure talking to a model."""


class LLMTimeout(LLMError):
    """The model call timed out."""


class BudgetExceeded(LLMError):
    """The request would not fit in the context window (final-plan.md §15.1)."""


class StructuredOutputError(LLMError):
    """The model never produced output that validates against the schema."""

    def __init__(self, agent: str, last_error: str, raw: str = "") -> None:
        super().__init__(f"{agent}: structured output failed: {last_error}")
        self.agent = agent
        self.last_error = last_error
        self.raw = raw
