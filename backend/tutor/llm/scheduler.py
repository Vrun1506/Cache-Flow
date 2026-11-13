"""Swap-aware model scheduler (final-plan.md §4, §16.12).

Only one model is loaded at a time, so a naive mix of router, reasoner and
vision calls would swap models constantly. ``ScheduledBackend`` wraps any
``LLMBackend`` and decides which queued call runs next:

* priority first (Q&A, hints and judging > required pipeline nodes > optional),
* then the pinned (sticky) tag, then the currently loaded tag, then FIFO,
* at most ``max_concurrency[tier]`` calls per tier,
* never two different model tags in flight together (the queue drains, then swaps),
* anti-starvation: after ``max_consecutive`` starts on the loaded tag, a call
  for another tag that has waited longer than ``swap_threshold_s`` forces a swap.

Tiers that map to the same tag (the default two-tier schedule) share one loaded
model and never count as a swap.

Approximation: ``swap_ms`` is the wall time of the *first call after a swap*
(load + that call's own generation), because Ollama loads on demand and does
not report load time separately. Compare it between runs, not in absolute terms.

Retries (§16.12) happen while the call still holds its slot, so the model
stays loaded. A cancelled caller leaves the queue, or releases its slot, and
the queue is pumped again so nothing is stranded.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Mapping

from .types import (
    BudgetExceeded,
    LLMBackend,
    LLMError,
    LLMRequest,
    LLMResponse,
    LLMTimeout,
    StructuredOutputError,
    Tier,
)

# Lower number runs first. Unknown agents are treated as required pipeline work.
PRIORITY: dict[str, int] = {
    "qa": 0, "hint": 0, "feedback": 0, "judge": 0,
    "orchestrator": 1, "solver": 1, "verifier_critic": 1, "lesson": 1, "lesson_designer": 1,
    "board_director": 1, "quiz_designer": 1, "vision": 1, "summariser": 1,
    "animator": 2, "guard": 2,
}
DEFAULT_PRIORITY = 1

RETRY_DELAYS = (0.5, 1.5)  # network / 5xx errors: two retries
TIMEOUT_RETRY_DELAYS = (0.5,)  # a timeout is retried once only
NO_RETRY = (BudgetExceeded, StructuredOutputError)


def priority_for(agent: str, overrides: Mapping[str, int] | None = None) -> int:
    name = agent.split("[", 1)[0]  # "animator[2]" shares animator's priority
    if overrides and name in overrides:
        return overrides[name]
    return PRIORITY.get(name, DEFAULT_PRIORITY)


@dataclass(eq=False)
class _Job:
    seq: int
    priority: int
    tier: Tier
    tag: str
    enqueued_at: float
    event: asyncio.Event = field(default_factory=asyncio.Event)
    granted: bool = False
    swap: bool = False


class ScheduledBackend:
    def __init__(
        self,
        inner: LLMBackend,
        models: Mapping[Tier, str],
        *,
        max_concurrency: Mapping[Tier, int] | int = 2,
        ps: Callable[[], Awaitable[set[str]]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        swap_threshold_s: float = 8.0,
        max_consecutive: int = 4,
        priorities: Mapping[str, int] | None = None,
    ) -> None:
        self._inner = inner
        self._models = dict(models)
        self._max = max_concurrency
        self._ps = ps
        self._clock = clock
        self._sleep = sleep
        self._threshold = swap_threshold_s
        self._max_consecutive = max_consecutive
        self._priorities = dict(priorities) if priorities else None

        self._pending: list[_Job] = []
        self._in_flight: dict[str, int] = {}  # tag -> calls running
        self._tier_running: dict[str, int] = {}  # tier -> calls running
        self._seq = itertools.count()
        self._loaded: str | None = None
        self._consecutive = 0
        self._pinned: str | None = None
        self._ps_checked = False

        self._swaps = 0
        self._swap_ms_total = 0.0
        self._calls_by_tag: dict[str, int] = {}
        self._wait_total = 0.0
        self._wait_max = 0.0
        self._started = 0

    # ---- public API -------------------------------------------------------

    async def generate(self, req: LLMRequest) -> LLMResponse:
        tag = self._models.get(req.tier)
        if tag is None:
            raise LLMError(f"no model configured for tier {req.tier!r}")
        await self._learn_loaded()
        job = _Job(
            seq=next(self._seq),
            priority=priority_for(req.agent, self._priorities),
            tier=req.tier,
            tag=tag,
            enqueued_at=self._clock(),
        )
        self._pending.append(job)
        self._pump()
        try:
            await job.event.wait()
            return await self._run(job, req)
        finally:
            if job.granted:
                self._release(job)
            elif job in self._pending:
                self._pending.remove(job)
            self._pump()

    def prefer(self, tag: str | None) -> None:
        """Pin a preferred model tag for the current mode (sticky tier, §4)."""
        self._pinned = tag
        self._pump()

    def stats(self) -> dict:
        return {
            "loaded": self._loaded,
            "pinned": self._pinned,
            "swaps": self._swaps,
            "avg_swap_ms": (self._swap_ms_total / self._swaps) if self._swaps else 0.0,
            "calls_by_tag": dict(self._calls_by_tag),
            "queue_depth": len(self._pending),
            "in_flight": sum(self._in_flight.values()),
            "avg_queue_wait_ms": (self._wait_total / self._started * 1000) if self._started else 0.0,
            "max_queue_wait_ms": self._wait_max * 1000,
        }

    # ---- scheduling -------------------------------------------------------

    def _cap(self, tier: Tier) -> int:
        if isinstance(self._max, int):
            return self._max
        return self._max.get(tier, 2)

    def _in_flight_tag(self) -> str | None:
        for tag, n in self._in_flight.items():
            if n > 0:
                return tag
        return None

    def _rank(self, job: _Job) -> tuple:
        return (
            job.priority,
            0 if job.tag == self._pinned else 1,
            0 if job.tag == self._loaded else 1,
            job.seq,
        )

    def _pump(self) -> None:
        """Grant every call that may start now. Synchronous, so it cannot interleave."""
        while self._pending:
            now = self._clock()
            pool = self._pending
            if self._loaded is not None and self._consecutive >= self._max_consecutive:
                starved = [j for j in pool if j.tag != self._loaded and now - j.enqueued_at > self._threshold]
                if starved:
                    pool = starved  # force the swap: stop feeding the loaded tag
            best = min(pool, key=self._rank)
            in_tag = self._in_flight_tag()
            if in_tag is not None and best.tag != in_tag:
                return  # drain the loaded model first, then swap
            candidates = [j for j in pool if j.tag == best.tag and self._tier_running.get(j.tier, 0) < self._cap(j.tier)]
            if not candidates:
                return
            self._start(min(candidates, key=self._rank), now)

    def _start(self, job: _Job, now: float) -> None:
        self._pending.remove(job)
        job.granted = True
        if self._loaded is not None and job.tag != self._loaded:
            job.swap = True
            self._swaps += 1
            self._consecutive = 0
        self._consecutive += 1
        self._loaded = job.tag
        self._in_flight[job.tag] = self._in_flight.get(job.tag, 0) + 1
        self._tier_running[job.tier] = self._tier_running.get(job.tier, 0) + 1
        self._calls_by_tag[job.tag] = self._calls_by_tag.get(job.tag, 0) + 1
        waited = now - job.enqueued_at
        self._started += 1
        self._wait_total += waited
        self._wait_max = max(self._wait_max, waited)
        job.event.set()

    def _release(self, job: _Job) -> None:
        job.granted = False
        self._in_flight[job.tag] -= 1
        self._tier_running[job.tier] -= 1

    async def _learn_loaded(self) -> None:
        """Ask Ollama once which model is already loaded, so the first call is not a false swap."""
        if self._ps_checked or self._ps is None:
            return
        self._ps_checked = True
        try:
            names = await self._ps()
        except Exception:  # noqa: BLE001 - /api/ps is advisory
            return
        if self._loaded is None:
            known = set(self._models.values())
            hits = [n for n in names if n in known]
            if hits:
                self._loaded = hits[0]

    # ---- execution --------------------------------------------------------

    async def _run(self, job: _Job, req: LLMRequest) -> LLMResponse:
        start = self._clock()
        try:
            return await self._call_with_retries(req)
        finally:
            if job.swap:
                self._swap_ms_total += (self._clock() - start) * 1000

    async def _call_with_retries(self, req: LLMRequest) -> LLMResponse:
        attempt = 0
        while True:
            try:
                return await self._inner.generate(req)
            except NO_RETRY:
                raise
            except LLMError as exc:
                delays = TIMEOUT_RETRY_DELAYS if isinstance(exc, LLMTimeout) else RETRY_DELAYS
                if attempt >= len(delays):
                    raise
                await self._sleep(delays[attempt])
                attempt += 1
