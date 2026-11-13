"""Ollama native ``/api/chat`` client (final-plan.md §2, §16.4).

The native endpoint is used (not the OpenAI-compatible ``/v1``) because the
plan depends on ``options.num_ctx``, ``think``, ``keep_alive`` and JSON-schema
``format``. Budget checks live in ``structured_call``, not here.
"""

from __future__ import annotations

import asyncio
import base64
import time
from dataclasses import replace
from typing import Any, Awaitable, Callable, Optional, Union

import httpx

from .types import LLMError, LLMRequest, LLMResponse, LLMTimeout, Tier

RETRY_DELAYS = (0.5, 1.5)
WARM_TIMEOUT_S = 300.0


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        models: dict[Tier, str],
        num_ctx: int = 8192,
        keep_alive: Union[str, int] = "5m",
        timeout: float = 90.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.models = models
        self.num_ctx = num_ctx
        self.keep_alive = keep_alive
        self._sleep = sleep
        self._caps: dict[str, set[str]] = {}
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=timeout, transport=transport)

    def build_payload(self, req: LLMRequest) -> dict[str, Any]:
        user: dict[str, Any] = {"role": "user", "content": req.user}
        if req.images:
            user["images"] = [base64.b64encode(img).decode("ascii") for img in req.images]
        payload: dict[str, Any] = {
            "model": self.models[req.tier],
            "messages": [{"role": "system", "content": req.system}, user],
            "stream": False,
            "think": req.think,
            "keep_alive": self.keep_alive,
            "options": {
                "num_ctx": self.num_ctx,
                "num_predict": req.num_predict,
                "temperature": req.temperature,
                "seed": req.seed,
            },
        }
        if req.schema is not None:
            payload["format"] = req.schema
        return payload

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Send with up to two retries on network errors and 5xx. 4xx is final."""
        last: Optional[Exception] = None
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                resp = await self._http.request(method, path, **kwargs)
            except httpx.TimeoutException as exc:
                raise LLMTimeout(f"{method} {path} timed out") from exc
            except httpx.TransportError as exc:
                last = exc
            else:
                if resp.status_code < 500:
                    return resp
                last = LLMError(f"{method} {path} -> {resp.status_code}: {resp.text[:200]}")
            if attempt < len(RETRY_DELAYS):
                await self._sleep(RETRY_DELAYS[attempt])
        if isinstance(last, LLMError):
            raise last
        raise LLMError(f"{method} {path} failed: {last}") from last

    async def capabilities(self, model: str) -> set[str]:
        """Capabilities Ollama reports for ``model`` (e.g. completion, thinking, vision).

        Cached. Returns an empty set when unknown (old server, error), which callers
        treat as "do not second-guess the request".
        """
        if model not in self._caps:
            try:
                resp = await self._request("POST", "/api/show", json={"model": model})
                caps = resp.json().get("capabilities", []) if resp.status_code < 300 else []
            except (LLMError, ValueError, AttributeError):
                caps = []
            self._caps[model] = {str(c) for c in caps}
        return self._caps[model]

    async def warm(self, tier: Tier) -> float:
        """Load the tier's model into memory without generating. Returns seconds taken.

        A cold 30B load can take longer than a node timeout, so call this before a
        lesson (and again after any model switch). The long timeout is deliberate.
        """
        start = time.monotonic()
        resp = await self._request(
            "POST",
            "/api/chat",
            json={"model": self.models[tier], "messages": [], "stream": False, "keep_alive": self.keep_alive},
            timeout=WARM_TIMEOUT_S,
        )
        if resp.status_code >= 300:
            raise LLMError(f"warm {self.models[tier]} -> {resp.status_code}: {resp.text[:200]}")
        return time.monotonic() - start

    async def generate(self, req: LLMRequest) -> LLMResponse:
        if req.think:
            # Asking a non-thinking model for think=true is a 400 on a real Ollama.
            caps = await self.capabilities(self.models[req.tier])
            if caps and "thinking" not in caps:
                req = replace(req, think=False)
        resp = await self._request("POST", "/api/chat", json=self.build_payload(req))
        if resp.status_code >= 300:
            raise LLMError(f"POST /api/chat -> {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
            text = data["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMError(f"bad /api/chat response: {resp.text[:200]}") from exc
        return LLMResponse(
            text=text,
            prompt_tokens=int(data.get("prompt_eval_count", 0) or 0),
            completion_tokens=int(data.get("eval_count", 0) or 0),
            done_reason=data.get("done_reason") or "stop",
            model=data.get("model", self.models[req.tier]),
            total_ms=float(data.get("total_duration", 0) or 0) / 1e6,
        )

    async def _names(self, path: str) -> list[str]:
        resp = await self._request("GET", path)
        if resp.status_code >= 300:
            raise LLMError(f"GET {path} -> {resp.status_code}: {resp.text[:200]}")
        try:
            return [m["name"] for m in resp.json().get("models", [])]
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise LLMError(f"bad {path} response: {resp.text[:200]}") from exc

    async def tags(self) -> list[str]:
        return await self._names("/api/tags")

    async def loaded(self) -> list[str]:
        return await self._names("/api/ps")

    async def aclose(self) -> None:
        await self._http.aclose()
