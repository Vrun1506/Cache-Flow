"""Schema-validated model calls with one retry (final-plan.md §16.2, §16.4)."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Generic, Optional, TypeVar

from pydantic import BaseModel, ValidationError

from .budget import DEFAULT_NUM_CTX, check_budget, is_truncated
from .types import LLMBackend, LLMRequest, LLMResponse, StructuredOutputError

T = TypeVar("T", bound=BaseModel)


@dataclass
class Parsed(Generic[T]):
    value: T
    response: LLMResponse
    attempts: int


def _summarise(exc: Exception) -> str:
    return " ".join(str(exc).split())[:300]


async def structured_call(
    backend: LLMBackend,
    req: LLMRequest,
    model_cls: type[T],
    *,
    num_ctx: int = DEFAULT_NUM_CTX,
    retries: int = 1,
) -> Parsed[T]:
    """Call the backend and validate the output. Max total calls = 1 + retries.

    Retries once on invalid output (with the error appended) or on truncation
    (with thinking off and a "be concise" note). A valid result is accepted on
    the final attempt even if it was flagged as truncated. Raises
    BudgetExceeded before any call that cannot fit, and StructuredOutputError
    if the output never validates.
    """
    check_budget(req, num_ctx)
    last_error = "no attempt made"
    raw = ""
    for attempt in range(1, retries + 2):
        resp = await backend.generate(req)
        raw = resp.text
        truncated = is_truncated(resp, num_ctx)
        value: Optional[T] = None
        invalid: Optional[str] = None
        try:
            value = model_cls.model_validate_json(resp.text)
        except ValidationError as exc:
            invalid = _summarise(exc)

        final = attempt > retries
        if value is not None and (not truncated or final):
            return Parsed(value=value, response=resp, attempts=attempt)

        if truncated:
            last_error = (
                f"output truncated (done_reason={resp.done_reason}, "
                f"tokens={resp.prompt_tokens + resp.completion_tokens})"
            )
        else:
            last_error = invalid or last_error
        if final:
            break

        if truncated:
            req = dataclasses.replace(req, think=False, user=req.user + "\n\nBe concise.")
        else:
            req = dataclasses.replace(
                req,
                user=req.user
                + f"\n\nYour previous output was invalid: {invalid}. Return only valid JSON matching the schema.",
            )
        check_budget(req, num_ctx)
    raise StructuredOutputError(req.agent, last_error, raw)
