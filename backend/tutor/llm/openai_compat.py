"""OpenAI-compatible chat client (vLLM, NIM, llama.cpp server) behind ``LLMBackend``.

The team's plan on main targets a vLLM endpoint, while ``OllamaClient`` speaks Ollama's native
``/api/chat``. This class maps the same ``LLMRequest`` onto ``POST /v1/chat/completions``.

NOT verified against a real server (no server was available). Three things are Phase 0 checks on
the DGX, and each has a constructor switch so no code change is needed:

* **Structured output.** ``response_format`` (``json_schema``, strict) is always sent when a schema is
  set. Some vLLM versions instead honour the top-level ``guided_json`` extension: pass
  ``guided_json=True`` to send it as well. Which one a given server enforces must be measured.
* **Thinking.** ``chat_template_kwargs: {"enable_thinking": true}`` is sent ONLY when ``req.think`` is
  true (Nemotron / vLLM style); when thinking is off nothing is sent and the server default applies.
  Use ``thinking_param="none"`` for a server that rejects the field. Whether ``response_format`` is still
  honoured while thinking is on is the same open question as for Ollama (AGENT-SYSTEM.md §16.4).
* **Residency.** vLLM keeps one model resident, so ``loaded()`` reports the configured models that
  the server lists at ``/v1/models``.

Errors never contain request bodies or the API key. Retries are left to the scheduler.
"""

from __future__ import annotations

import base64
import re
import time
from dataclasses import replace
from typing import Any, Optional

import httpx

from .budget import check_budget, estimate_tokens
from .types import LLMError, LLMRequest, LLMResponse, LLMTimeout, Tier

WARM_TIMEOUT_S = 300.0
_THINK_BLOCK = re.compile(r"^\s*<think>.*?</think>\s*", re.DOTALL)
_NAME_OK = re.compile(r"[^A-Za-z0-9_-]+")
_GRAMMAR_HINTS = ("json_schema", "guided", "grammar", "response_format", "schema", "xgrammar", "outlines")


def _strip_reasoning(text: str) -> str:
    """Drop a leading ``<think>…</think>`` block. A server whose parser removed the opening tag leaves a bare ``</think>``."""
    stripped = _THINK_BLOCK.sub("", text, count=1)
    if stripped != text:
        return stripped
    if "<think>" not in text and "</think>" in text:
        return text.split("</think>", 1)[1].lstrip()
    return text


class OpenAICompatClient:
    def __init__(
        self,
        base_url: str,
        models: dict[Tier, str],
        *,
        num_ctx: int = 8192,
        timeout: float = 90.0,
        api_key: Optional[str] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        guided_json: bool = False,
        thinking_param: str = "chat_template_kwargs",
        supports_thinking: Optional[bool] = None,
    ) -> None:
        if thinking_param not in ("chat_template_kwargs", "none"):
            raise ValueError("thinking_param must be 'chat_template_kwargs' or 'none'")
        base = base_url.strip().rstrip("/")
        if base.endswith("/v1"):
            base = base[: -len("/v1")]
        self.base_url = base
        self.models = models
        self.num_ctx = num_ctx
        self.guided_json = guided_json
        self.thinking_param = thinking_param
        self.supports_thinking = supports_thinking
        self._api_key = api_key or None
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=timeout, transport=transport, headers=headers)

    # ------------------------------------------------------------------ payload

    def build_payload(self, req: LLMRequest) -> dict[str, Any]:
        content: Any = req.user
        if req.images:
            content = [{"type": "text", "text": req.user}] + [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(img).decode("ascii")}}
                for img in req.images
            ]
        payload: dict[str, Any] = {
            "model": self.models[req.tier],
            "messages": [{"role": "system", "content": req.system}, {"role": "user", "content": content}],
            "temperature": req.temperature,
            "max_tokens": req.num_predict,
            "seed": req.seed,
            "stream": False,
        }
        if req.schema is not None:
            name = _NAME_OK.sub("_", req.agent).strip("_")[:64] or "output"
            payload["response_format"] = {"type": "json_schema", "json_schema": {"name": name, "schema": req.schema, "strict": True}}
            if self.guided_json:
                payload["guided_json"] = req.schema
        if req.think and self.thinking_param == "chat_template_kwargs":
            payload["chat_template_kwargs"] = {"enable_thinking": True}
        return payload

    # ------------------------------------------------------------------- errors

    def _scrub(self, text: str) -> str:
        if self._api_key:
            text = text.replace(self._api_key, "[redacted]")
        return text

    def _server_message(self, resp: httpx.Response) -> str:
        msg = ""
        try:
            data = resp.json()
            err = data.get("error", data) if isinstance(data, dict) else data
            msg = (err.get("message") or err.get("detail") or "") if isinstance(err, dict) else str(err)
            if not isinstance(msg, str):
                msg = str(msg)
        except (ValueError, AttributeError):
            pass
        return self._scrub(" ".join((msg or resp.text).split()))[:300]

    def _raise_for_status(self, resp: httpx.Response, what: str) -> None:
        code = resp.status_code
        if code < 300:
            return
        msg = self._server_message(resp)
        if code == 400 and any(h in msg.lower() for h in _GRAMMAR_HINTS):
            raise LLMError(f"{what} -> 400: the server rejected the structured-output schema: {msg}")
        kind = "rate limited or overloaded" if code in (429, 503) else "server error" if code >= 500 else "request rejected"
        raise LLMError(f"{what} -> {code} ({kind}): {msg}")

    async def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self._http.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise LLMTimeout(f"{method} {path} timed out") from exc
        except httpx.TransportError as exc:
            raise LLMError(f"{method} {path} failed: {type(exc).__name__}") from exc

    # ------------------------------------------------------------------ generate

    async def capabilities(self, model: str) -> dict[str, bool]:
        """Best effort and never raising: thinking is assumed available unless ``supports_thinking=False``."""
        return {"thinking": self.supports_thinking is not False}

    async def generate(self, req: LLMRequest) -> LLMResponse:
        check_budget(req, self.num_ctx)
        if req.think and self.supports_thinking is False:
            req = replace(req, think=False)
        started = time.monotonic()
        resp = await self._send("POST", "/v1/chat/completions", json=self.build_payload(req))
        self._raise_for_status(resp, "POST /v1/chat/completions")
        try:
            data = resp.json()
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("bad /v1/chat/completions response: no choices[0].message.content") from exc
        if isinstance(content, list):  # content parts
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        if not isinstance(content, str):
            raise LLMError("bad /v1/chat/completions response: content is not text")
        text = _strip_reasoning(content).strip()
        if not text:
            raise LLMError(f"{req.agent}: the model returned empty content (finish_reason={choice.get('finish_reason')})")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        return LLMResponse(
            text=text,
            prompt_tokens=int(prompt_tokens) if prompt_tokens is not None else estimate_tokens(req.system + req.user),
            completion_tokens=int(completion_tokens) if completion_tokens is not None else estimate_tokens(content),
            done_reason="length" if choice.get("finish_reason") == "length" else (choice.get("finish_reason") or "stop"),
            model=str(data.get("model") or self.models[req.tier]),
            total_ms=(time.monotonic() - started) * 1000,
        )

    # --------------------------------------------------------- server inspection

    async def tags(self) -> list[str]:
        resp = await self._send("GET", "/v1/models")
        self._raise_for_status(resp, "GET /v1/models")
        try:
            return [str(m["id"]) for m in resp.json()["data"]]
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMError("bad /v1/models response") from exc

    async def loaded(self) -> list[str]:
        """vLLM serves its resident model(s) at /v1/models: report the configured ones that are listed."""
        listed = set(await self.tags())
        return [m for m in dict.fromkeys(self.models.values()) if m in listed]

    async def warm(self, tier: Tier) -> float:
        """A 1-token request, so the first real call is not a cold start. Returns seconds taken."""
        started = time.monotonic()
        resp = await self._send(
            "POST", "/v1/chat/completions", timeout=WARM_TIMEOUT_S,
            json={"model": self.models[tier], "messages": [{"role": "user", "content": "ok"}], "max_tokens": 1, "temperature": 0, "stream": False},
        )
        self._raise_for_status(resp, f"warm {self.models[tier]}")
        return time.monotonic() - started

    async def aclose(self) -> None:
        await self._http.aclose()
