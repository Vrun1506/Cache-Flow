import asyncio

import pytest

from tutor.config import LLMConfig, SchedulerConfig, build_scheduled_client
from tutor.llm import (
    BudgetExceeded,
    FakeLLM,
    LLMError,
    LLMRequest,
    LLMResponse,
    LLMTimeout,
    StructuredOutputError,
)
from tutor.llm.scheduler import ScheduledBackend, priority_for

pytestmark = pytest.mark.anyio

MODELS = {"router": "q", "reasoner": "r", "vision": "v"}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class Stub:
    """Records start order and concurrency; refuses two different tags at once."""

    def __init__(self, models=MODELS, delay=0.01, swap_delay=0.0, clock=None, advance=0.0):
        self.models, self.delay, self.swap_delay = models, delay, swap_delay
        self.clock, self.advance = clock, advance
        self.started: list[str] = []
        self.running: dict[str, int] = {}
        self.max_running = 0
        self.max_by_tier: dict[str, int] = {}
        self._tier_running: dict[str, int] = {}
        self.loaded = None
        self.violations = 0

    async def generate(self, req):
        tag = self.models[req.tier]
        self.started.append(req.agent + ":" + req.user)
        if any(t != tag and n > 0 for t, n in self.running.items()):
            self.violations += 1
        self.running[tag] = self.running.get(tag, 0) + 1
        self._tier_running[req.tier] = self._tier_running.get(req.tier, 0) + 1
        self.max_running = max(self.max_running, sum(self.running.values()))
        self.max_by_tier[req.tier] = max(self.max_by_tier.get(req.tier, 0), self._tier_running[req.tier])
        try:
            if self.loaded is not None and self.loaded != tag and self.swap_delay:
                await asyncio.sleep(self.swap_delay)
            self.loaded = tag
            await asyncio.sleep(self.delay)
            if self.clock:
                self.clock.t += self.advance
        finally:
            self.running[tag] -= 1
            self._tier_running[req.tier] -= 1
        return LLMResponse(text="{}", prompt_tokens=1, completion_tokens=1, done_reason="stop", model=tag)


def req(tier="reasoner", agent="solver", user=""):
    return LLMRequest(agent=agent, tier=tier, system="s", user=user)


def tags_in_order(stub):
    return [MODELS[t] for t in ("router", "reasoner", "vision")]


async def spawn(sched, *reqs):
    """Start one task per request in order, letting each reach the queue."""
    tasks = []
    for r in reqs:
        tasks.append(asyncio.create_task(sched.generate(r)))
        await asyncio.sleep(0)
    return tasks


# ---- swap batching -------------------------------------------------------------------


async def test_interleaved_jobs_are_batched_by_model():
    stub = Stub(swap_delay=0.02)
    sched = ScheduledBackend(stub, MODELS)
    tiers = ["router", "reasoner", "vision", "router", "reasoner", "vision"]
    tasks = await spawn(sched, *[req(t, user=str(i)) for i, t in enumerate(tiers)])
    await asyncio.gather(*tasks)
    order = [MODELS[tiers[int(s.split(":")[1])]] for s in stub.started]
    switches = sum(1 for a, b in zip(order, order[1:]) if a != b)
    assert switches == 2 and order == ["q", "q", "r", "r", "v", "v"]
    assert sched.stats()["swaps"] == 2
    assert stub.violations == 0


async def test_router_and_reasoner_sharing_a_tag_never_swap():
    shared = {"router": "r", "reasoner": "r", "vision": "v"}
    stub = Stub(shared)
    sched = ScheduledBackend(stub, shared)
    tasks = await spawn(sched, *[req(t) for t in ["router", "reasoner", "router", "reasoner"]])
    await asyncio.gather(*tasks)
    assert sched.stats()["swaps"] == 0
    assert sched.stats()["calls_by_tag"] == {"r": 4}


async def test_never_two_tags_in_flight_and_swap_waits_for_drain():
    stub = Stub(delay=0.03)
    sched = ScheduledBackend(stub, MODELS)
    tasks = await spawn(sched, req("reasoner"), req("vision"), req("reasoner"), req("router"))
    await asyncio.gather(*tasks)
    assert stub.violations == 0


# ---- concurrency ---------------------------------------------------------------------


@pytest.mark.parametrize("cap", [1, 2, 3])
async def test_per_tier_concurrency_cap(cap):
    stub = Stub()
    sched = ScheduledBackend(stub, MODELS, max_concurrency=cap)
    await asyncio.gather(*await spawn(sched, *[req("reasoner", user=str(i)) for i in range(7)]))
    assert stub.max_by_tier["reasoner"] == cap


async def test_per_tier_cap_accepts_a_mapping():
    shared = {"router": "r", "reasoner": "r", "vision": "v"}
    stub = Stub(shared)
    sched = ScheduledBackend(stub, shared, max_concurrency={"router": 1, "reasoner": 3})
    reqs = [req("router", user=str(i)) for i in range(4)] + [req("reasoner", user=str(i)) for i in range(4)]
    await asyncio.gather(*await spawn(sched, *reqs))
    assert stub.max_by_tier["router"] == 1 and stub.max_by_tier["reasoner"] == 3


# ---- priority and stickiness -------------------------------------------------------------


async def test_priority_order_on_one_tag():
    stub = Stub(delay=0.02)
    sched = ScheduledBackend(stub, MODELS, max_concurrency=1)
    await asyncio.gather(
        *await spawn(
            sched,
            req(agent="solver", user="first"),
            req(agent="animator", user="opt"),
            req(agent="solver", user="req"),
            req(agent="qa", user="qa"),
        )
    )
    assert stub.started == ["solver:first", "qa:qa", "solver:req", "animator:opt"]


async def test_qa_jumps_ahead_even_when_it_needs_a_swap():
    stub = Stub(delay=0.02)
    sched = ScheduledBackend(stub, MODELS, max_concurrency=1)
    await asyncio.gather(
        *await spawn(
            sched,
            req("reasoner", "solver", "running"),
            req("reasoner", "solver", "more-reasoner"),
            req("vision", "qa", "qa-vision"),
        )
    )
    assert stub.started == ["solver:running", "qa:qa-vision", "solver:more-reasoner"]
    assert stub.violations == 0


async def test_loaded_tag_is_preferred_among_equals():
    stub = Stub(delay=0.02)
    sched = ScheduledBackend(stub, MODELS, max_concurrency=1)
    await asyncio.gather(
        *await spawn(sched, req("reasoner", user="a"), req("vision", user="v"), req("reasoner", user="b"))
    )
    assert [s.split(":")[1] for s in stub.started] == ["a", "b", "v"]


async def test_sticky_preference_pins_a_tag():
    stub = Stub(delay=0.02)
    sched = ScheduledBackend(stub, MODELS, max_concurrency=1)
    sched.prefer("v")
    await asyncio.gather(
        *await spawn(sched, req("reasoner", user="a"), req("reasoner", user="b"), req("vision", user="v"))
    )
    # "a" started before anything else existed; the pin then beats the loaded tag.
    assert [s.split(":")[1] for s in stub.started] == ["a", "v", "b"]


async def test_pin_never_outranks_a_higher_priority_job():
    stub = Stub(delay=0.02)
    sched = ScheduledBackend(stub, MODELS, max_concurrency=1)
    sched.prefer("v")
    await asyncio.gather(
        *await spawn(
            sched,
            req("vision", "solver", "running"),
            req("vision", "solver", "pinned"),
            req("reasoner", "qa", "question"),
        )
    )
    assert [s.split(":")[1] for s in stub.started] == ["running", "question", "pinned"]


async def test_prefer_none_clears_the_pin():
    sched = ScheduledBackend(Stub(), MODELS)
    sched.prefer("v")
    sched.prefer(None)
    assert sched.stats()["pinned"] is None


# ---- anti-starvation ------------------------------------------------------------------------


async def test_waiting_job_for_another_tag_forces_a_swap_after_the_streak():
    clock = Clock()
    stub = Stub(delay=0.005, clock=clock, advance=6.0)
    sched = ScheduledBackend(stub, MODELS, max_concurrency=1, clock=clock, swap_threshold_s=5.0, max_consecutive=2)
    reqs = [req("reasoner", user="r1"), req("vision", user="v"), *[req("reasoner", user=f"r{i}") for i in range(2, 6)]]
    await asyncio.gather(*await spawn(sched, *reqs))
    assert [s.split(":")[1] for s in stub.started][:3] == ["r1", "r2", "v"]


async def test_no_forced_swap_before_the_wait_threshold():
    clock = Clock()
    stub = Stub(delay=0.005, clock=clock, advance=0.1)
    sched = ScheduledBackend(stub, MODELS, max_concurrency=1, clock=clock, swap_threshold_s=5.0, max_consecutive=2)
    reqs = [req("reasoner", user="r1"), req("vision", user="v"), *[req("reasoner", user=f"r{i}") for i in range(2, 5)]]
    await asyncio.gather(*await spawn(sched, *reqs))
    assert [s.split(":")[1] for s in stub.started][-1] == "v"


# ---- retries ------------------------------------------------------------------------------------


def retrying(items, agent="solver"):
    slept: list[float] = []

    async def sleep(s):
        slept.append(s)

    llm = FakeLLM({agent: items})
    return llm, ScheduledBackend(llm, MODELS, sleep=sleep), slept


OK = LLMResponse(text="{}", prompt_tokens=1, completion_tokens=1, done_reason="stop", model="r")


async def test_network_errors_retry_twice_with_backoff():
    llm, sched, slept = retrying([LLMError("boom"), LLMError("boom"), OK])
    assert (await sched.generate(req())).text == "{}"
    assert slept == [0.5, 1.5] and len(llm.calls) == 3


async def test_gives_up_after_two_retries():
    llm, sched, slept = retrying([LLMError("a"), LLMError("b"), LLMError("c"), OK])
    with pytest.raises(LLMError, match="c"):
        await sched.generate(req())
    assert slept == [0.5, 1.5] and len(llm.calls) == 3


async def test_timeouts_retry_once_only():
    llm, sched, slept = retrying([LLMTimeout("t1"), LLMTimeout("t2"), OK])
    with pytest.raises(LLMTimeout):
        await sched.generate(req())
    assert slept == [0.5] and len(llm.calls) == 2


@pytest.mark.parametrize("exc", [BudgetExceeded("too big"), StructuredOutputError("solver", "bad json")])
async def test_non_retryable_errors_fail_immediately(exc):
    llm, sched, slept = retrying([exc, OK])
    with pytest.raises(type(exc)):
        await sched.generate(req())
    assert slept == [] and len(llm.calls) == 1


async def test_a_failing_call_releases_its_slot():
    llm, sched, _ = retrying([BudgetExceeded("x")])
    with pytest.raises(BudgetExceeded):
        await sched.generate(req())
    assert sched.stats()["in_flight"] == 0 and sched.stats()["queue_depth"] == 0


# ---- cancellation ---------------------------------------------------------------------------------


async def test_cancelling_a_queued_call_removes_it():
    stub = Stub(delay=0.05)
    sched = ScheduledBackend(stub, MODELS, max_concurrency=1)
    t1, t2, t3 = await spawn(sched, req(user="1"), req(user="2"), req(user="3"))
    assert sched.stats()["queue_depth"] == 2
    t2.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t2
    assert sched.stats()["queue_depth"] == 1
    await asyncio.gather(t1, t3)
    assert [s.split(":")[1] for s in stub.started] == ["1", "3"]
    assert sched.stats()["in_flight"] == 0


async def test_cancelling_a_running_call_frees_the_slot_and_unblocks_the_queue():
    stub = Stub(delay=5.0)
    sched = ScheduledBackend(stub, MODELS, max_concurrency=1)
    t1, = await spawn(sched, req("reasoner", user="slow"))
    stub.delay = 0.01
    t2, = await spawn(sched, req("vision", user="after-swap"))
    await asyncio.sleep(0.05)
    assert not t2.done()
    t1.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t1
    await asyncio.wait_for(t2, 1.0)
    assert sched.stats()["in_flight"] == 0 and sched.stats()["queue_depth"] == 0
    assert stub.violations == 0


async def test_cancelled_while_waiting_on_a_drain_leaves_no_leak():
    stub = Stub(delay=0.05)
    sched = ScheduledBackend(stub, MODELS, max_concurrency=2)
    tasks = await spawn(sched, req("reasoner"), req("vision"), req("vision"))
    tasks[1].cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    assert sched.stats()["in_flight"] == 0 and sched.stats()["queue_depth"] == 0
    assert sum(stub.running.values()) == 0


# ---- stats, ps, misc ---------------------------------------------------------------------------------


async def test_stats_report_swaps_calls_and_queue_wait():
    clock = Clock()
    stub = Stub(delay=0.005, clock=clock, advance=2.0)
    sched = ScheduledBackend(stub, MODELS, max_concurrency=1, clock=clock)
    for tier in ("reasoner", "vision", "reasoner"):  # sequential: nothing to batch
        await sched.generate(req(tier))
    s = sched.stats()
    assert s["swaps"] == 2 and s["calls_by_tag"] == {"r": 2, "v": 1}
    assert s["avg_swap_ms"] == pytest.approx(2000.0)  # one fake-clock call (2 s) per swap
    assert s["loaded"] == "r" and s["queue_depth"] == 0 and s["max_queue_wait_ms"] == 0.0

    await asyncio.gather(*await spawn(sched, req(), req()))  # the second waits for the first
    assert sched.stats()["max_queue_wait_ms"] >= 2000.0


async def test_ps_seeds_the_loaded_model_so_the_first_call_is_not_a_swap():
    async def ps():
        return {"v", "unrelated:1"}

    sched = ScheduledBackend(Stub(), MODELS, ps=ps)
    await sched.generate(req("vision"))
    assert sched.stats()["swaps"] == 0
    await sched.generate(req("reasoner"))
    assert sched.stats()["swaps"] == 1


async def test_ps_is_called_once_and_its_failure_is_ignored():
    calls = []

    async def ps():
        calls.append(1)
        raise RuntimeError("ollama down")

    sched = ScheduledBackend(Stub(), MODELS, ps=ps)
    await sched.generate(req())
    await sched.generate(req())
    assert len(calls) == 1 and sched.stats()["loaded"] == "r"


async def test_unknown_tier_is_an_llm_error():
    sched = ScheduledBackend(Stub(), {"reasoner": "r"})
    with pytest.raises(LLMError):
        await sched.generate(req("vision"))


def test_priority_table():
    assert [priority_for(a) for a in ("qa", "hint", "feedback", "judge")] == [0, 0, 0, 0]
    assert priority_for("solver") == priority_for("orchestrator") == priority_for("never-heard-of-it") == 1
    assert priority_for("animator") == priority_for("animator[2]") == priority_for("guard") == 2
    assert priority_for("animator", {"animator": 0}) == 0


# ---- config ------------------------------------------------------------------------------------------


def test_scheduler_config_from_env(monkeypatch):
    monkeypatch.setenv("TUTOR_MAX_CONCURRENCY", "4")
    monkeypatch.setenv("TUTOR_SWAP_THRESHOLD_S", "3.5")
    assert SchedulerConfig.from_env() == SchedulerConfig(max_concurrency=4, swap_threshold_s=3.5)
    monkeypatch.delenv("TUTOR_MAX_CONCURRENCY")
    monkeypatch.delenv("TUTOR_SWAP_THRESHOLD_S")
    assert SchedulerConfig.from_env() == SchedulerConfig(2, 8.0)


async def test_build_scheduled_client_wraps_ollama():
    from tutor.llm import OllamaClient

    config = LLMConfig(models=dict(MODELS))
    client = build_scheduled_client(config, SchedulerConfig(max_concurrency=3, swap_threshold_s=1.0))
    assert isinstance(client, ScheduledBackend)
    assert isinstance(client._inner, OllamaClient)
    assert client._models == MODELS and client._max == 3 and client._threshold == 1.0
    await client._inner.aclose()
