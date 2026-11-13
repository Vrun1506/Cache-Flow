"""Record and replay model calls (final-plan.md §15.9 "demo insurance").

``RecordingBackend`` wraps the real backend and stores every response in a
JSON-lines file. ``ReplayBackend`` answers later runs from that file, so a
demo question still plays if a model stalls on the day.

This is honest, not a fake: replayed responses are real local outputs, every
replayed response is a ``ReplayedResponse`` (``replayed=True``), and
``ReplayBackend.is_replay`` / ``label`` / ``stats()`` let ``/health`` and the
agent timeline say "replayed" out loud. Disclose it in the demo and submission.

A request is identified by ``request_key``: agent, prompt version, system text,
user text, JSON schema, temperature, think, seed and the SHA-256 of any image.
Images themselves are never stored. ``tier`` and ``num_predict`` are left out
on purpose so a re-tuned budget or model mapping does not orphan the store.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .types import LLMBackend, LLMError, LLMRequest, LLMResponse


@dataclass
class ReplayedResponse(LLMResponse):
    """An ``LLMResponse`` that came from the replay store, never from a model."""

    replayed: bool = True


class ReplayMiss(LLMError):
    """The replay store has no recorded answer for this request."""


def request_key(req: LLMRequest) -> str:
    payload = {
        "agent": req.agent,
        "prompt_version": req.prompt_version,
        "system": req.system,
        "user": req.user,
        "schema": req.schema,
        "temperature": req.temperature,
        "think": req.think,
        "seed": req.seed,
        "images": [hashlib.sha256(img).hexdigest() for img in req.images],
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_RESPONSE_FIELDS = ("text", "prompt_tokens", "completion_tokens", "done_reason", "model", "total_ms")


def _response_dict(resp: LLMResponse) -> dict[str, Any]:
    full = asdict(resp)
    return {k: full[k] for k in _RESPONSE_FIELDS}


def _read_store(path: Path) -> tuple[dict[str, dict[str, Any]], int]:
    """Load the store. Corrupt or malformed lines are skipped and counted; the last entry for a key wins."""
    entries: dict[str, dict[str, Any]] = {}
    skipped = 0
    if not path.exists():
        return entries, skipped
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                key, r = rec["key"], rec["response"]
                if not isinstance(key, str) or not isinstance(r["text"], str):
                    raise TypeError("bad types")
                resp = {
                    "text": r["text"],
                    "prompt_tokens": int(r["prompt_tokens"]),
                    "completion_tokens": int(r["completion_tokens"]),
                    "done_reason": str(r["done_reason"]),
                    "model": str(r["model"]),
                    "total_ms": float(r.get("total_ms", 0.0)),
                }
            except (ValueError, KeyError, TypeError, AttributeError):
                skipped += 1
                continue
            entries[key] = {"agent": rec.get("agent", ""), "prompt_version": rec.get("prompt_version", ""), "response": resp}
    return entries, skipped


class RecordingBackend:
    """Pass-through backend that records each successful response."""

    def __init__(self, inner: LLMBackend, store_path: str | os.PathLike[str]) -> None:
        self._inner = inner
        self.path = Path(store_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._known, _ = _read_store(self.path)
        self._lock = threading.Lock()
        self.recorded = 0

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__") or name in ("_inner", "path", "_known", "_lock"):
            raise AttributeError(name)
        return getattr(self._inner, name)

    async def generate(self, req: LLMRequest) -> LLMResponse:
        resp = await self._inner.generate(req)  # errors pass through and are never recorded
        key = request_key(req)
        data = _response_dict(resp)
        with self._lock:
            known = self._known.get(key)
            if known is None or known["response"] != data:
                line = json.dumps(
                    {"key": key, "agent": req.agent, "prompt_version": req.prompt_version, "response": data},
                    ensure_ascii=False, separators=(",", ":"),
                )
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
                self._known[key] = {"agent": req.agent, "prompt_version": req.prompt_version, "response": data}
                self.recorded += 1
        return resp


class ReplayBackend:
    """Answers from a recording. A miss raises ``ReplayMiss`` or, if asked, falls through to another backend."""

    def __init__(
        self,
        store_path: str | os.PathLike[str],
        *,
        miss: str = "raise",
        fallback: LLMBackend | None = None,
    ) -> None:
        if miss not in ("raise", "fallback"):
            raise ValueError("miss must be 'raise' or 'fallback'")
        if miss == "fallback" and fallback is None:
            raise ValueError("miss='fallback' needs a fallback backend")
        self.path = Path(store_path)
        self._miss = miss
        self._fallback = fallback
        self._entries, self.skipped_lines = _read_store(self.path)
        self.hits = 0
        self.misses = 0

    @property
    def is_replay(self) -> bool:
        """Always True: a /health that reads this says "replayed"."""
        return True

    @property
    def label(self) -> str:
        """"replayed" when every answer so far came from the store, "partially replayed" after a miss was served live."""
        if self.misses and self._miss == "fallback":
            return "partially replayed"
        return "replayed"

    def stats(self) -> dict[str, Any]:
        return {
            "replay": True,
            "label": self.label,
            "hits": self.hits,
            "misses": self.misses,
            "entries": len(self._entries),
            "skipped_lines": self.skipped_lines,
        }

    async def generate(self, req: LLMRequest) -> LLMResponse:
        key = request_key(req)
        entry = self._entries.get(key)
        if entry is not None:
            self.hits += 1
            return ReplayedResponse(**entry["response"])
        self.misses += 1
        if self._miss == "fallback":
            return await self._fallback.generate(req)  # type: ignore[union-attr]
        raise ReplayMiss(f"{req.agent}: no recorded response for request {key[:12]} (prompt v{req.prompt_version})")
