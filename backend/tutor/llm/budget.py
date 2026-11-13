"""Token budgeting for the 8,192-token request cap (final-plan.md §15.1).

Ollama truncates an over-long prompt silently, so every request is checked
before it is sent, and every response is checked for truncation afterwards.
No tokenizer is assumed: a conservative 3 characters per token is used.
"""

from __future__ import annotations

import math

from .types import BudgetExceeded, LLMRequest, LLMResponse

IMAGE_TOKENS = 1000
DEFAULT_NUM_CTX = 8192
DEFAULT_MARGIN = 300


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 3)


def request_tokens(req: LLMRequest) -> int:
    """Estimated prompt tokens. The JSON schema is a grammar, not prompt text."""
    return estimate_tokens(req.system) + estimate_tokens(req.user) + IMAGE_TOKENS * len(req.images)


def check_budget(req: LLMRequest, num_ctx: int = DEFAULT_NUM_CTX, margin: int = DEFAULT_MARGIN) -> None:
    prompt = request_tokens(req)
    total = prompt + req.num_predict + margin
    if total > num_ctx:
        raise BudgetExceeded(
            f"{req.agent}: prompt ~{prompt} + num_predict {req.num_predict} + margin {margin} "
            f"= {total} exceeds num_ctx {num_ctx}"
        )


def is_truncated(resp: LLMResponse, num_ctx: int = DEFAULT_NUM_CTX) -> bool:
    return resp.done_reason == "length" or resp.prompt_tokens + resp.completion_tokens >= num_ctx
