"""Structured session logging with mandatory redaction (final-plan.md §15.5).

Logs hold timings, token counts and event types, never a student's ink, audio
or words. Rules applied to every record before it is written:

* ``bytes`` (images, audio) become ``{"sha256", "bytes"}``.
* Student-text fields (``text``, ``question``, ``answer``, ``transcript`` ...)
  become ``{"sha256", "len"}`` unless ``TUTOR_LOG_TEXT=1``.
* Any other string is capped at 500 characters.
* Keys that look like secrets (password, token, secret, authorization ...) are dropped.

The log lives on the Spark's disk, is git-ignored, and ``ops/purge-logs.sh`` deletes it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from ..llm.budget import is_truncated
from ..llm.types import LLMBackend, LLMRequest, LLMResponse

MAX_STR = 500
MAX_ITEMS = 50
MAX_DEPTH = 6

# Keys whose values are (or may contain) what a student wrote or said.
TEXT_KEYS = frozenset({
    "text", "question", "answer", "transcript", "utterance", "message", "student",
    "input", "user", "system", "prompt", "content", "caption", "reply", "feedback",
    "reading", "lines", "body", "note", "hint", "statement", "expr", "value",
})
_SECRET = re.compile(r"pass(word)?|token|secret|authorization|api[-_]?key|cookie|credential", re.I)
# Counters that merely contain the word "token".
_NOT_SECRET = frozenset({"prompt_tokens", "completion_tokens", "tokens_per_s", "max_tokens", "num_predict_tokens"})
_SAFE_ID = re.compile(r"[^A-Za-z0-9_-]")


def _sha(data: bytes | str) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def log_text_enabled() -> bool:
    return os.environ.get("TUTOR_LOG_TEXT") == "1"


def redact(value: Any, key: str | None = None, *, log_text: bool | None = None, _depth: int = 0) -> Any:
    """Return a JSON-safe, redacted copy of ``value``."""
    if log_text is None:
        log_text = log_text_enabled()
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        return {"sha256": _sha(raw), "bytes": len(raw)}
    if _depth >= MAX_DEPTH:
        return "<depth limit>"
    if isinstance(value, str):
        if key is not None and key.lower() in TEXT_KEYS and not log_text:
            return {"sha256": _sha(value), "len": len(value)}
        return value if len(value) <= MAX_STR else value[:MAX_STR] + "…"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            name = str(k)
            if _SECRET.search(name) and name not in _NOT_SECRET:
                continue
            out[name] = redact(v, name, log_text=log_text, _depth=_depth + 1)
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)[:MAX_ITEMS]
        # List items inherit the parent key, so ["a", "b"] under "text" is still hashed.
        return [redact(v, key, log_text=log_text, _depth=_depth + 1) for v in items]
    text = repr(value)
    return text if len(text) <= MAX_STR else text[:MAX_STR] + "…"


class TraceLog:
    """Append-only JSONL log for one session: ``<dir>/session-<id>.jsonl``."""

    def __init__(self, dir: str | os.PathLike[str], session_id: str, *, log_text: bool | None = None) -> None:
        # The id becomes part of a file name: nothing that could climb out of the directory.
        safe = _SAFE_ID.sub("_", str(session_id))[:64] or "anon"
        self.dir = Path(dir)
        self.session_id = safe
        self.path = self.dir / f"session-{safe}.jsonl"
        self._log_text = log_text
        self._lock = threading.Lock()
        self.dir.mkdir(parents=True, exist_ok=True)

    def _write(self, kind: str, fields: dict[str, Any]) -> None:
        record = {"ts": round(time.time(), 3), "session": self.session_id, "kind": kind}
        record.update(redact(fields, log_text=self._log_text))
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=str) + "\n"
        with self._lock, open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line)

    def log_call(
        self,
        agent: str,
        prompt_version: str,
        tier: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        ms: float,
        tokens_per_s: float,
        status: str,
        truncated: bool,
        **extra: Any,
    ) -> None:
        self._write("call", {
            "agent": agent, "prompt_version": prompt_version, "tier": tier, "model": model,
            "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
            "ms": round(ms, 1), "tokens_per_s": round(tokens_per_s, 2), "status": status,
            "truncated": truncated, **extra,
        })

    def log_event(self, type: str, **fields: Any) -> None:
        self._write("event", {"type": type, **fields})

    def log_transition(self, prev: str, state: str, event: str, **fields: Any) -> None:
        self._write("transition", {"prev": prev, "state": state, "event": event, **fields})


class TracingBackend:
    """An ``LLMBackend`` wrapper that logs every call and changes nothing else.

    Errors are logged and re-raised untouched. Other attributes (``stats()``,
    ``prefer()``, ``warm()`` ...) are forwarded to the wrapped backend.
    """

    def __init__(self, inner: LLMBackend, trace: TraceLog) -> None:
        self._inner = inner
        self._trace = trace

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__") or name in ("_inner", "_trace"):
            raise AttributeError(name)
        return getattr(self._inner, name)

    async def generate(self, req: LLMRequest) -> LLMResponse:
        start = time.monotonic()
        meta = {
            "think": req.think, "num_predict": req.num_predict, "images": len(req.images),
            "user": req.user, "system_len": len(req.system),
        }
        try:
            resp = await self._inner.generate(req)
        except BaseException as exc:  # logged, then re-raised unchanged (cancellation included)
            ms = (time.monotonic() - start) * 1000
            self._trace.log_call(req.agent, req.prompt_version, req.tier, "", 0, 0, ms, 0.0, "error", False,
                                 error=type(exc).__name__, **meta)
            raise
        ms = (time.monotonic() - start) * 1000
        secs = ms / 1000
        rate = resp.completion_tokens / secs if secs > 0 else 0.0
        truncated = is_truncated(resp)
        self._trace.log_call(req.agent, req.prompt_version, req.tier, resp.model, resp.prompt_tokens,
                             resp.completion_tokens, ms, rate, "truncated" if truncated else "ok", truncated,
                             done_reason=resp.done_reason, replayed=bool(getattr(resp, "replayed", False)), **meta)
        return resp
